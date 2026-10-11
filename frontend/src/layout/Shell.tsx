import { type ReactNode, useEffect, useRef, useState } from "react";
import {
  Link,
  Navigate,
  Outlet,
  useParams,
  useSearchParams,
} from "react-router";
import { registerAgents } from "../agents";
import { useAgents, useChannels, useConfig, useSetup } from "../api";
import { setZone } from "../dates";
import { tr } from "../i18n";
import { DogIcon, MenuIcon, SearchIcon } from "../icons";
import { useRealtime } from "../realtime";
import { Toaster } from "../toast";
import { btn, useEnterOnChange, useIsMobile } from "../ui";
import { QuickSwitcher } from "./QuickSwitcher";
import { Rail } from "./Rail";
import { Sidebar } from "./Sidebar";
import { StatusBar } from "./StatusBar";
import { TaskPanel } from "./TaskPanel";
import { ThreadPanel } from "./ThreadPanel";

/** Rail | sidebar | main | (task panel) over a 32px status bar — docs/design/Main.dc.html. */
export function Shell() {
  const link = useRealtime();
  const config = useConfig();
  const channels = useChannels();
  const agents = useAgents();
  const setup = useSetup();
  // Custom agents' names and avatars for every place that shows an agent.
  if (agents.data) registerAgents(agents.data);
  const { channelId } = useParams();
  const [params] = useSearchParams();
  const taskId = params.get("task");
  const threadId = taskId ? null : params.get("thread");
  // "넓게 보기": the thread takes the feed's place instead of the side panel.
  const wide = Boolean(threadId && params.get("wide"));
  const panel = Boolean(taskId || threadId) && !wide;
  const [pickedArea, setPickedArea] = useState<string | null>(null);
  const [switcherOpen, setSwitcherOpen] = useState(false);
  const mobile = useIsMobile();

  useEffect(() => {
    if (config.data) setZone(config.data.timezone);
  }, [config.data]);

  // Inside a channel the rail follows that channel's area; elsewhere the user's pick.
  const channelArea = channels.data?.channels.find(
    (c) => c.id === channelId,
  )?.area_id;
  const areaId = channelArea ?? (channelId ? null : pickedArea);

  // The page eases in when the area changes (the feed stays mounted).
  const main = useRef<HTMLElement>(null);
  useEnterOnChange(main, areaId);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setSwitcherOpen((open) => !open);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // First run: the setup screen comes before everything else (it can be skipped).
  if (setup.data && !setup.data.done) return <Navigate to="/welcome" replace />;

  if (mobile)
    return (
      <MobileShell
        areaId={areaId}
        onPickArea={setPickedArea}
        threadId={threadId}
        taskId={taskId}
        switcherOpen={switcherOpen}
        setSwitcherOpen={setSwitcherOpen}
      >
        <main
          ref={main}
          className="relative flex min-h-0 min-w-0 grow flex-col"
        >
          <Outlet />
        </main>
      </MobileShell>
    );

  return (
    <div
      className="grid h-full overflow-hidden bg-page"
      style={{
        gridTemplateColumns: panel
          ? "68px 256px minmax(0,1fr) 352px"
          : "68px 256px minmax(0,1fr)",
        gridTemplateRows: "minmax(0,1fr) 32px",
      }}
    >
      {/* Desktop app: the window has no title bar, so its top edge moves it. */}
      <div data-tauri-drag-region className="window-drag" />
      <Rail areaId={areaId} onPickArea={setPickedArea} />
      <Sidebar areaId={areaId} onOpenSwitcher={() => setSwitcherOpen(true)} />
      {/* relative: absolutely positioned bits in a page (visually hidden inputs) are
          clipped by the shell instead of growing the document and scrolling it. */}
      <main
        ref={main}
        className="relative flex min-h-0 min-w-0 flex-col bg-page"
      >
        {wide && threadId ? (
          <ThreadPanel messageId={threadId} wide />
        ) : (
          <Outlet />
        )}
      </main>
      {taskId && <TaskPanel taskId={taskId} />}
      {threadId && !wide && <ThreadPanel messageId={threadId} />}
      <StatusBar link={link} />
      <Toaster />
      <QuickSwitcher
        open={switcherOpen}
        onClose={() => setSwitcherOpen(false)}
      />
    </div>
  );
}

/** Phone layout: a top bar, the page full width, and the rail + sidebar in a drawer.
 * Task and thread panels cover the page instead of sitting beside it. */
function MobileShell({
  areaId,
  onPickArea,
  threadId,
  taskId,
  switcherOpen,
  setSwitcherOpen,
  children,
}: {
  areaId: string | null;
  onPickArea: (id: string | null) => void;
  threadId: string | null;
  taskId: string | null;
  switcherOpen: boolean;
  setSwitcherOpen: (open: boolean) => void;
  children: ReactNode;
}) {
  const [drawer, setDrawer] = useState(false);

  return (
    <div className="flex h-full flex-col overflow-hidden bg-page pb-[env(safe-area-inset-bottom)]">
      <header className="flex h-12 shrink-0 items-center gap-1 border-b border-line-soft bg-page px-2 pt-[env(safe-area-inset-top)] box-content">
        <button
          type="button"
          aria-label={tr("메뉴")}
          aria-expanded={drawer}
          className={btn.icon}
          onClick={() => setDrawer(true)}
        >
          <MenuIcon />
        </button>
        <span className="flex grow justify-center">
          <Link
            to="/"
            aria-label={tr("홈 · 오늘")}
            onClick={() => onPickArea(null)}
            className="flex items-center gap-1.5 rounded-full px-2 py-1 text-[15px] font-medium text-ink"
          >
            <DogIcon size={20} />
            Argos
          </Link>
        </span>
        <button
          type="button"
          aria-label={tr("채널·할 일 찾기")}
          className={btn.icon}
          onClick={() => setSwitcherOpen(true)}
        >
          <SearchIcon size={17} />
        </button>
      </header>
      {children}
      {(taskId || threadId) && (
        <div className="fixed inset-0 z-30 grid grid-cols-[minmax(0,1fr)] grid-rows-[minmax(0,1fr)] bg-sidebar pt-[env(safe-area-inset-top)] pb-[env(safe-area-inset-bottom)]">
          {taskId ? (
            <TaskPanel taskId={taskId} />
          ) : (
            threadId && <ThreadPanel messageId={threadId} />
          )}
        </div>
      )}
      <div
        className={`fixed inset-0 z-40 transition-[visibility] ${drawer ? "visible" : "invisible delay-200"}`}
      >
        <button
          type="button"
          aria-label={tr("닫기")}
          tabIndex={-1}
          className={`absolute inset-0 cursor-default bg-black/30 transition-opacity duration-200 ${drawer ? "opacity-100" : "opacity-0"}`}
          onClick={() => setDrawer(false)}
        />
        {/* Following a link (a channel, settings) closes the drawer; picking an area
            in the rail keeps it open to show that area's channels. */}
        {/* biome-ignore lint/a11y/noStaticElementInteractions: delegates link clicks */}
        {/* biome-ignore lint/a11y/useKeyWithClickEvents: links handle their own keys */}
        <div
          onClick={(e) => {
            if ((e.target as HTMLElement).closest("a")) setDrawer(false);
          }}
          className={`absolute inset-y-0 left-0 grid w-[324px] max-w-[88vw] grid-cols-[68px_minmax(0,1fr)] bg-sidebar pt-[env(safe-area-inset-top)] pb-[env(safe-area-inset-bottom)] shadow-lift transition-transform duration-200 ease-out ${drawer ? "translate-x-0" : "-translate-x-full"}`}
        >
          <Rail areaId={areaId} onPickArea={onPickArea} />
          <Sidebar
            areaId={areaId}
            onOpenSwitcher={() => {
              setDrawer(false);
              setSwitcherOpen(true);
            }}
          />
        </div>
      </div>
      <Toaster />
      <QuickSwitcher
        open={switcherOpen}
        onClose={() => setSwitcherOpen(false)}
      />
    </div>
  );
}
