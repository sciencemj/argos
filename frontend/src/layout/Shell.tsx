import { useEffect, useRef, useState } from "react";
import { Outlet, useParams, useSearchParams } from "react-router";
import { registerAgents } from "../agents";
import { useAgents, useChannels, useConfig } from "../api";
import { setZone } from "../dates";
import { useRealtime } from "../realtime";
import { Toaster } from "../toast";
import { useEnterOnChange } from "../ui";
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
      <Rail areaId={areaId} onPickArea={setPickedArea} />
      <Sidebar areaId={areaId} onOpenSwitcher={() => setSwitcherOpen(true)} />
      <main ref={main} className="flex min-h-0 min-w-0 flex-col bg-page">
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
