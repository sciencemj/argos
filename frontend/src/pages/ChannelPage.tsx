import { type FormEvent, useEffect, useState } from "react";
import {
  Navigate,
  NavLink,
  Outlet,
  useLocation,
  useMatch,
  useNavigate,
  useOutletContext,
  useParams,
} from "react-router";
import { AgentAvatar } from "../agents";
import {
  type Channel,
  useAgents,
  useChannels,
  useDmSessions,
  useStartDmSession,
  useUpdateChannel,
  useVaultFolders,
  useVaultSettings,
} from "../api";
import { ChannelDeleteDialog } from "../ChannelDeleteDialog";
import { fmt } from "../dates";
import { FEED_COLUMN, Feed } from "../feed";
import { tr } from "../i18n";
import { PlusIcon, SettingsIcon } from "../icons";
import { btn, Dialog, ErrorText, field, label } from "../ui";

const tab =
  "flex h-8 items-center rounded-full border border-transparent px-3.5 text-text-3 aria-[current=page]:border-line-soft aria-[current=page]:bg-card aria-[current=page]:font-medium aria-[current=page]:text-ink aria-[current=page]:shadow-sm";

export const useChannel = () => useOutletContext<Channel>();

const TABS = [
  { path: "", label: tr("피드") },
  { path: "kanban", label: tr("칸반") },
  { path: "calendar", label: tr("캘린더") },
  { path: "notes", label: tr("노트") },
  { path: "materials", label: tr("자료") },
];

const SYSTEM_TABS = TABS.slice(0, 3); // #today-style channels have no vault folder
const SPACE_TABS = [{ path: "", label: tr("수집함") }, ...TABS.slice(1)];

const isMac = /Mac|iPhone|iPad/.test(navigator.userAgent);
const ALT = isMac ? "⌥" : "Alt+";

/** Feed · kanban · calendar · notes · materials. ⌥1–5 jumps to a tab and ⌥[ / ⌥]
 * steps left/right (by key position, so it works with any keyboard layout or IME);
 * holding ⌥ shows the keys on the tabs. */
function ChannelTabs({ channel, space }: { channel: Channel; space: boolean }) {
  const navigate = useNavigate();
  const [showKeys, setShowKeys] = useState(false);
  const tabs = space
    ? SPACE_TABS
    : channel.kind === "system"
      ? SYSTEM_TABS
      : TABS;
  const base = `/c/${channel.id}`;

  useEffect(() => {
    // The current tab is read from the address at each key press, so fast repeats
    // (⌥]⌥]⌥]) step correctly even before the page has re-rendered.
    const currentTab = () => {
      const rest = window.location.pathname
        .slice(base.length)
        .replace(/^\//, "");
      return Math.max(
        0,
        tabs.findIndex((t) => t.path === rest.split("/")[0]),
      );
    };
    const down = (e: KeyboardEvent) => {
      if (e.key === "Alt") setShowKeys(true);
      if (!e.altKey || e.metaKey || e.ctrlKey) return;
      const digit = /^Digit([1-9])$/.exec(e.code);
      const now = currentTab();
      let target: number | null = null;
      if (e.code === "BracketRight") target = (now + 1) % tabs.length;
      else if (e.code === "BracketLeft")
        target = (now - 1 + tabs.length) % tabs.length;
      else if (digit && Number(digit[1]) <= tabs.length)
        target = Number(digit[1]) - 1;
      if (target === null) return;
      e.preventDefault(); // ⌥[ would otherwise type “ into the message box
      navigate(`${base}/${tabs[target].path}${window.location.search}`);
    };
    const up = (e: KeyboardEvent) => {
      if (e.key === "Alt" || !e.altKey) setShowKeys(false);
    };
    const hide = () => setShowKeys(false);
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    window.addEventListener("blur", hide);
    return () => {
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
      window.removeEventListener("blur", hide);
    };
  }, [navigate, base, tabs]);

  return (
    <div className="flex items-center gap-2.5">
      <nav
        aria-label={tr("채널 탭")}
        className="flex gap-0.5 rounded-full bg-inset p-[3px] text-[13.5px] whitespace-nowrap"
      >
        {tabs.map((t, i) => (
          <NavLink
            key={t.path || "feed"}
            to={t.path || "."}
            end={t.path === ""}
            className={tab}
            title={`${t.label} (${ALT}${i + 1})`}
            aria-keyshortcuts={`Alt+${i + 1}`}
          >
            {t.label}
            {showKeys && (
              <kbd className="ml-1.5 rounded bg-card px-1 font-mono text-[10.5px] font-normal text-meta">
                {ALT}
                {i + 1}
              </kbd>
            )}
          </NavLink>
        ))}
      </nav>
      <span
        className={`font-mono text-[11px] text-meta transition-opacity ${showKeys ? "opacity-100" : "opacity-0"}`}
        aria-hidden={!showKeys}
      >
        {ALT}[ {ALT}
        {tr("] 이전·다음")}
      </span>
    </div>
  );
}

export function ChannelPage() {
  const { channelId } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const { data, isLoading } = useChannels();
  const [editing, setEditing] = useState(false);
  const [deleting, setDeleting] = useState<Channel | null>(null);
  const channel = data?.channels.find((c) => c.id === channelId);
  const inbox = data?.channels.find(
    (c) => c.kind === "system" && c.name === "inbox",
  );
  const personal = data?.channels.find((c) => c.kind === "personal");
  // The feed reads in a centered column; kanban, calendar and notes get more room.
  const onFeed = useMatch("/c/:channelId") !== null;
  const column = onFeed ? FEED_COLUMN : "mx-auto w-full max-w-[1480px]";

  if (isLoading) return null;
  if (!channel) {
    return (
      <p className="p-8 text-text-3">
        {tr("채널을 찾을 수 없어요. 삭제되었을 수 있어요.")}
      </p>
    );
  }
  if (channel.kind === "personal" && inbox) {
    const suffix = location.pathname.slice(`/c/${channel.id}`.length);
    return (
      <Navigate
        to={`/c/${inbox.id}${suffix}${location.search}${location.hash}`}
        replace
      />
    );
  }
  const space = channel.id === inbox?.id;
  const settingsChannel = space ? personal : channel;
  const editable =
    Boolean(settingsChannel) &&
    (space || (channel.kind !== "system" && channel.kind !== "dm"));

  if (channel.kind === "dm") {
    return <DMPage channel={channel} />;
  }

  return (
    <>
      <header
        data-tauri-drag-region="deep"
        className={`${column} flex flex-col gap-3 px-8 pt-[18px] pb-4`}
      >
        <div className="flex items-center gap-3.5">
          <h1 className="m-0 grow truncate text-[28px] leading-tight font-light tracking-[-0.02em] text-ink">
            {!space && <span className="text-hash">#</span>}{" "}
            {space ? tr("내 공간") : channel.name}
          </h1>
          {editable && (
            <button
              type="button"
              aria-label={tr("채널 설정")}
              className="flex size-9 cursor-pointer items-center justify-center rounded-full border border-line-soft bg-card text-text-3 hover:text-ink"
              onClick={() => setEditing(true)}
            >
              <SettingsIcon />
            </button>
          )}
        </div>
        <div className="flex items-center gap-3.5">
          <ChannelTabs channel={channel} space={space} />
          <span className="grow" />
          {settingsChannel?.vault_path && (
            <span className="truncate text-[12.5px] text-meta">
              {tr("옵시디언")}{" "}
              <span className="font-mono">{settingsChannel.vault_path}/</span>
            </span>
          )}
        </div>
      </header>
      <div
        className={`flex min-h-0 w-full grow flex-col ${onFeed ? "" : column}`}
      >
        <Outlet context={space && !onFeed && personal ? personal : channel} />
      </div>
      {editable && settingsChannel && (
        <ChannelSettings
          key={settingsChannel.id}
          channel={settingsChannel}
          space={space}
          open={editing}
          onClose={() => setEditing(false)}
          onDelete={() => {
            setEditing(false);
            setDeleting(settingsChannel);
          }}
        />
      )}
      {deleting && (
        <ChannelDeleteDialog
          key={deleting.id}
          channel={deleting}
          onClose={() => setDeleting(null)}
          onDeleted={() => {
            setDeleting(null);
            navigate("/");
          }}
        />
      )}
    </>
  );
}

/** 1:1 conversation with an agent: no kanban/calendar, the whole page is the chat. */
/** A DM is a list of conversations: the current one unless the user picks an older
 * one to reread or continue. Long quiet gaps start a new one on the server. */
function DMPage({ channel }: { channel: Channel }) {
  const agents = useAgents();
  const agent = agents.data?.find((a) => a.id === channel.default_agent_id);
  const sessions = useDmSessions(channel.id);
  const startSession = useStartDmSession();
  const [picked, setPicked] = useState<string | null>(null);
  const list = sessions.data ?? [];
  const current = list[0];
  const chosen = list.find((s) => s.id === picked);
  const shown = chosen ?? current;
  return (
    <>
      <header
        data-tauri-drag-region="deep"
        className={`${FEED_COLUMN} flex items-center gap-3.5 px-8 pt-[18px] pb-4`}
      >
        <AgentAvatar id={agent?.name} size={36} />
        <div className="flex grow flex-col">
          <h1 className="m-0 text-[28px] leading-tight font-light tracking-[-0.02em] text-ink">
            {agent?.display_name ?? tr("에이전트")}
          </h1>
          {agent && !agent.available && (
            <span className="text-[12.5px] text-danger">{agent.problem}</span>
          )}
        </div>
        {list.length > 0 && (
          <select
            aria-label={tr("대화 고르기")}
            className={`${field} w-auto max-w-[280px] truncate`}
            value={shown?.id ?? ""}
            onChange={(e) =>
              setPicked(e.target.value === current?.id ? null : e.target.value)
            }
          >
            {list.map((s, i) => (
              <option key={s.id} value={s.id}>
                {(s.title ?? tr("새 대화")) +
                  ` · ${fmt(s.last_active_at, "M/d HH:mm")}` +
                  (i === 0 ? ` ${tr("(현재)")}` : "")}
              </option>
            ))}
          </select>
        )}
        <button
          type="button"
          className={btn.outline}
          disabled={startSession.isPending}
          onClick={() =>
            startSession.mutate(channel.id, {
              onSuccess: () => setPicked(null),
            })
          }
        >
          <PlusIcon size={14} />
          {tr("새 대화")}
        </button>
      </header>
      {sessions.isSuccess && (
        <Feed
          key={`${channel.id}-${shown?.id ?? ""}`}
          channel={channel}
          sessionId={shown?.id}
          pickedSession={chosen?.id}
        />
      )}
    </>
  );
}

export function FeedTab() {
  const channel = useChannel();
  return <Feed key={channel.id} channel={channel} />;
}

function ChannelSettings({
  channel,
  space,
  open,
  onClose,
  onDelete,
}: {
  channel: Channel;
  space: boolean;
  open: boolean;
  onClose: () => void;
  onDelete: () => void;
}) {
  const update = useUpdateChannel();
  const [name, setName] = useState(channel.name);
  const [areaId, setAreaId] = useState(channel.area_id ?? "");
  const [vaultPath, setVaultPath] = useState(channel.vault_path ?? "");
  const [agentId, setAgentId] = useState(channel.default_agent_id ?? "");
  const [workspace, setWorkspace] = useState(channel.workspace_path ?? "");
  const [folderOpen, setFolderOpen] = useState(false);
  const [folderQuery, setFolderQuery] = useState("");
  const agents = useAgents();
  const areas = useChannels().data?.areas ?? [];
  const vault = useVaultSettings();
  const folders = useVaultFolders(open && Boolean(vault.data?.path));

  const submit = (e: FormEvent) => {
    e.preventDefault();
    update.mutate(
      {
        id: channel.id,
        ...(space
          ? {}
          : {
              name: name.trim(),
              area_id: areaId || null,
              workspace_path: workspace.trim() || null,
            }),
        vault_path: vaultPath.trim() || null,
        default_agent_id: agentId || null,
      },
      { onSuccess: onClose },
    );
  };

  const matchingFolders = folders.data?.filter((folder) =>
    folder.toLocaleLowerCase().includes(folderQuery.toLocaleLowerCase()),
  );

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={space ? tr("내 공간 설정") : tr("채널 설정")}
    >
      <form onSubmit={submit} className="flex flex-col gap-4">
        {!space && (
          <label className="flex flex-col gap-1">
            <span className={label}>{tr("이름")}</span>
            <input
              className={field}
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
            />
          </label>
        )}
        {!space && (
          <label className="flex flex-col gap-1">
            <span className={label}>{tr("영역")}</span>
            <select
              className={field}
              value={areaId}
              onChange={(event) => setAreaId(event.target.value)}
            >
              <option value="">{tr("전체 (영역 없음)")}</option>
              {areas.map((area) => (
                <option key={area.id} value={area.id}>
                  {area.name}
                </option>
              ))}
            </select>
          </label>
        )}
        <label className="flex flex-col gap-1">
          <span className={label}>{tr("기본 에이전트 (/ask를 받음)")}</span>
          <select
            className={field}
            value={agentId}
            onChange={(e) => setAgentId(e.target.value)}
          >
            <option value="">{tr("앱 기본값 따르기")}</option>
            {agents.data?.map((a) => (
              <option key={a.id} value={a.id}>
                {a.display_name}
              </option>
            ))}
          </select>
        </label>
        {!space && (
          <label className="flex flex-col gap-1">
            <span className={label}>{tr("작업 폴더 (코딩 모드, 선택)")}</span>
            <input
              className={`${field} font-mono`}
              value={workspace}
              onChange={(e) => setWorkspace(e.target.value)}
              placeholder={tr("예: my-project (코딩 잡 허용 폴더 안)")}
            />
            <span className="text-[12px] text-meta">
              {tr(
                "정하면 입력창의 코딩 토글로 Claude·Codex가 이 폴더에서 파일·셸·네트워크를 써요.",
              )}
            </span>
          </label>
        )}
        <div className="flex flex-col gap-1">
          <label htmlFor={`vault-folder-${channel.id}`} className={label}>
            {tr("옵시디언 폴더 (선택)")}
          </label>
          <fieldset
            className="relative m-0 min-w-0 border-0 p-0"
            aria-label={tr("옵시디언 폴더 선택")}
            onBlur={(event) => {
              if (
                !event.currentTarget.contains(
                  event.relatedTarget as Node | null,
                )
              )
                setFolderOpen(false);
            }}
          >
            <input
              id={`vault-folder-${channel.id}`}
              className={`${field} font-mono text-[13px]`}
              value={vaultPath}
              onFocus={() => {
                setFolderQuery("");
                setFolderOpen(true);
              }}
              onChange={(event) => {
                setVaultPath(event.target.value);
                setFolderQuery(event.target.value);
                setFolderOpen(true);
              }}
              onKeyDown={(event) => {
                if (event.key === "Escape" && folderOpen) {
                  event.preventDefault();
                  event.stopPropagation();
                  setFolderOpen(false);
                }
                if (event.key === "ArrowDown" && folderOpen) {
                  event.preventDefault();
                  event.currentTarget.parentElement
                    ?.querySelector<HTMLButtonElement>("[data-folder-option]")
                    ?.focus();
                }
              }}
              placeholder={
                folders.data
                  ? tr("볼트 안의 폴더 고르기")
                  : tr("앱 설정에서 볼트를 먼저 지정하세요")
              }
              disabled={!folders.data && !vaultPath}
            />
            {folderOpen && matchingFolders && matchingFolders.length > 0 && (
              <div className="absolute right-0 bottom-full left-0 z-20 mb-1 max-h-44 overflow-y-auto rounded-xl border border-line bg-card p-1 shadow-lift">
                {matchingFolders.map((folder) => (
                  <button
                    key={folder}
                    data-folder-option
                    type="button"
                    className="block w-full cursor-pointer rounded-lg px-2 py-1.5 text-left font-mono text-[12px] text-text hover:bg-inset focus:bg-inset"
                    onClick={() => {
                      setVaultPath(folder);
                      setFolderOpen(false);
                    }}
                  >
                    {folder}
                  </button>
                ))}
              </div>
            )}
          </fieldset>
          <span className="text-[11.5px] text-meta">
            {tr(
              "이 폴더의 노트·자료가 채널에 보이고, 노트의 체크박스 할 일이 칸반으로 와요.",
            )}
          </span>
        </div>
        <ErrorText error={update.error} />
        <div className="flex items-center gap-2">
          {channel.kind !== "personal" && (
            <button type="button" className={btn.danger} onClick={onDelete}>
              {tr("채널 삭제")}
            </button>
          )}
          <span className="grow" />
          <button type="button" className={btn.ghost} onClick={onClose}>
            {tr("취소")}
          </button>
          <button type="submit" className={btn.cta} disabled={update.isPending}>
            {tr("저장")}
          </button>
        </div>
      </form>
    </Dialog>
  );
}
