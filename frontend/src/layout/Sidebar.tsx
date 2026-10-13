import {
  closestCenter,
  DndContext,
  type DragEndEvent,
  KeyboardSensor,
  type Modifier,
  MouseSensor,
  TouchSensor,
  useSensor,
  useSensors,
} from "@dnd-kit/core";
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import {
  type FormEvent,
  type MouseEvent,
  type ReactNode,
  useMemo,
  useRef,
  useState,
} from "react";
import { NavLink, useNavigate, useParams } from "react-router";
import { useBusyAgents } from "../agentStream";
import { AgentAvatar } from "../agents";
import {
  type Channel,
  type Task,
  useAgentSettings,
  useAgents,
  useChannels,
  useCreateChannel,
  useNotifications,
  useOpenDM,
  usePendingApprovals,
  useReorderChannels,
  useTasks,
  useToday,
  useUnread,
} from "../api";
import { ChannelDeleteDialog } from "../ChannelDeleteDialog";
import { dday, fmt } from "../dates";
import { t, tr, tt } from "../i18n";
import {
  BellIcon,
  ChartIcon,
  InboxIcon,
  PlusIcon,
  SearchIcon,
  ShieldIcon,
  SunIcon,
} from "../icons";
import { PawTrail } from "../paws";
import {
  btn,
  ContextMenu,
  DdayBadge,
  Dialog,
  ErrorText,
  field,
  label,
  useEnterOnChange,
} from "../ui";

type Kind = "course" | "project";
const SECTIONS: { kind: Kind; title: string }[] = [
  { kind: "course", title: t("과목", "Courses") },
  { kind: "project", title: t("프로젝트", "Projects") },
];

const row =
  "flex h-8 items-center gap-[9px] rounded-full px-3 text-text-2 hover:bg-inset aria-[current=page]:bg-inset aria-[current=page]:font-medium aria-[current=page]:text-ink";
const count =
  "inline-flex min-w-7 shrink-0 items-center justify-center font-mono text-[11.5px] tabular-nums";

/** Nearest open deadline per channel: the sidebar shows D-day instead of unread counts. */
function nearestDue(tasks: Task[] | undefined): Map<string, number> {
  const out = new Map<string, number>();
  for (const t of tasks ?? []) {
    if (!t.due_at || t.status === "done") continue;
    const days = dday(t.due_at);
    const prev = out.get(t.channel_id);
    if (prev === undefined || days < prev) out.set(t.channel_id, days);
  }
  return out;
}

export function Sidebar({
  areaId,
  onOpenSwitcher,
}: {
  areaId: string | null;
  onOpenSwitcher: () => void;
}) {
  const navigate = useNavigate();
  const { data } = useChannels();
  const tasks = useTasks();
  const today = useToday();
  const [adding, setAdding] = useState<Kind | null>(null);
  const [menu, setMenu] = useState<{
    x: number;
    y: number;
    channel: Channel;
  } | null>(null);
  const [deleting, setDeleting] = useState<Channel | null>(null);
  const due = useMemo(() => nearestDue(tasks.data), [tasks.data]);

  const area = data?.areas.find((a) => a.id === areaId);
  const channels = data?.channels ?? [];
  const inbox = channels.find((c) => c.kind === "system" && c.name === "inbox");
  const approvals = usePendingApprovals().data?.length ?? 0;
  const unreadNotices = useNotifications().data?.unread ?? 0;
  const unread = useUnread();
  const inScope = channels.filter(
    (c) => c.kind !== "system" && (!areaId || c.area_id === areaId),
  );
  // Switching areas: the name and the area's channels ease in.
  const heading = useRef<HTMLDivElement>(null);
  const scoped = useRef<HTMLDivElement>(null);
  useEnterOnChange(heading, areaId, "-8px 0");
  useEnterOnChange(scoped, areaId);
  const todayCount =
    (today.data?.events.length ?? 0) + (today.data?.due_tasks.length ?? 0);

  return (
    <aside className="flex min-h-0 flex-col border-r border-line-soft bg-sidebar">
      <div
        data-tauri-drag-region="deep"
        className="flex flex-col gap-3.5 px-4 pt-[18px] pb-3.5"
      >
        <div className="flex items-baseline justify-between px-1">
          <div
            ref={heading}
            className="text-[22px] font-light tracking-[-0.02em] text-ink"
          >
            {area?.name ?? tr("전체")}
          </div>
          <div className="font-mono text-[11px] text-meta">
            {fmt(new Date(), "yyyy.MM.dd")}
          </div>
        </div>
        <button
          type="button"
          onClick={onOpenSwitcher}
          className="flex h-9 cursor-pointer items-center gap-2 rounded-full border border-line-soft bg-page px-3 text-[13px] text-text-3"
        >
          <SearchIcon />
          <span className="grow text-left">{tr("채널·할 일 찾기")}</span>
          <span className="font-mono text-[11px] text-meta">⌘K</span>
        </button>
      </div>

      <div className="flex min-h-0 grow flex-col gap-5 overflow-y-auto px-2.5 pt-1.5 pb-3.5">
        <Section title={tr("지켜보는 중")}>
          <NavLink to="/" end className={row}>
            <SunIcon />
            <span className="grow">today</span>
            <span className={`${count} text-meta`}>{todayCount}</span>
          </NavLink>
          {inbox && (
            <NavLink to={`/c/${inbox.id}`} className={row}>
              <InboxIcon />
              <span className="grow">{tr("내 공간")}</span>
              {!!today.data?.inbox_count && (
                <span
                  className={`${count} rounded-full border border-line bg-inset px-1 py-px font-semibold text-text`}
                >
                  {today.data.inbox_count}
                </span>
              )}
            </NavLink>
          )}
          <NavLink to="/notifications" className={row}>
            <BellIcon />
            <span className="grow">{tr("알림")}</span>
            {unreadNotices > 0 && (
              <span
                className={`${count} rounded-full border border-line bg-inset px-1 py-px font-semibold text-text`}
              >
                {unreadNotices}
              </span>
            )}
          </NavLink>
          <NavLink to="/approvals" className={row}>
            <ShieldIcon />
            <span className="grow">{tr("승인 대기")}</span>
            {approvals > 0 && (
              <span
                className={`${count} rounded-full bg-danger-bg px-1 py-0.5 font-medium text-danger`}
              >
                {approvals}
              </span>
            )}
          </NavLink>
          <NavLink to="/review" className={row}>
            <ChartIcon />
            <span className="grow">{tr("리뷰")}</span>
          </NavLink>
        </Section>

        <div ref={scoped} className="flex flex-col gap-5">
          {SECTIONS.map(({ kind, title }) => (
            <Section
              key={kind}
              title={title}
              action={
                <button
                  type="button"
                  aria-label={tt`${title} 추가`}
                  className="flex size-6 cursor-pointer items-center justify-center rounded-full text-meta hover:bg-inset hover:text-ink"
                  onClick={() => setAdding(kind)}
                >
                  <PlusIcon size={14} />
                </button>
              }
            >
              <SortableChannels
                channels={inScope.filter((c) => c.kind === kind)}
                due={due}
                unread={unread.data}
                onMenu={(event, channel) => {
                  event.preventDefault();
                  setMenu({ x: event.clientX, y: event.clientY, channel });
                }}
              />
            </Section>
          ))}
        </div>
        <AgentSection />
      </div>

      <AddChannelDialog
        kind={adding}
        defaultAreaId={areaId ?? data?.areas[0]?.id ?? ""}
        onClose={() => setAdding(null)}
      />
      {menu && (
        <ContextMenu
          x={menu.x}
          y={menu.y}
          label={tr("채널 삭제…")}
          onClose={() => setMenu(null)}
          onAction={() => setDeleting(menu.channel)}
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
    </aside>
  );
}

function Section({
  title,
  action,
  children,
}: {
  title: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-0.5">
      <div className="flex items-center justify-between px-2.5 pb-1.5 text-[11.5px] font-medium text-meta">
        {title}
        {action}
      </div>
      {children}
    </div>
  );
}

const vertical: Modifier = ({ transform }) => ({ ...transform, x: 0 });

/** One sidebar section; drag a row to reorder (saved, PUT /channels/order). Rows
 * stay within their section: course and project are channel kinds, not folders. */
function SortableChannels({
  channels,
  due,
  unread,
  onMenu,
}: {
  channels: Channel[];
  due: Map<string, number>;
  unread: Record<string, number> | undefined;
  onMenu: (event: MouseEvent<HTMLAnchorElement>, channel: Channel) => void;
}) {
  const reorder = useReorderChannels();
  const sensors = useSensors(
    useSensor(MouseSensor, { activationConstraint: { distance: 5 } }),
    // Touch: a long press, so a swipe still scrolls the drawer.
    useSensor(TouchSensor, {
      activationConstraint: { delay: 300, tolerance: 6 },
    }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    }),
  );
  const ids = channels.map((c) => c.id);
  const onDragEnd = ({ active, over }: DragEndEvent) => {
    if (!over || active.id === over.id) return;
    const from = ids.indexOf(String(active.id));
    const to = ids.indexOf(String(over.id));
    if (from < 0 || to < 0) return;
    reorder(arrayMove(ids, from, to));
  };
  return (
    <DndContext
      sensors={sensors}
      collisionDetection={closestCenter}
      modifiers={[vertical]}
      onDragEnd={onDragEnd}
    >
      <SortableContext items={ids} strategy={verticalListSortingStrategy}>
        {channels.map((c) => (
          <ChannelRow
            key={c.id}
            channel={c}
            days={due.get(c.id)}
            unread={unread?.[c.id] ?? 0}
            onContextMenu={(event) => onMenu(event, c)}
          />
        ))}
      </SortableContext>
    </DndContext>
  );
}

function ChannelRow({
  channel,
  days,
  unread,
  onContextMenu,
}: {
  channel: Channel;
  days: number | undefined;
  unread: number;
  onContextMenu: (event: MouseEvent<HTMLAnchorElement>) => void;
}) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: channel.id });
  return (
    <NavLink
      ref={setNodeRef}
      to={`/c/${channel.id}`}
      className={`${row} ${isDragging ? "relative z-10 bg-inset shadow-sm" : ""}`}
      style={{ transform: CSS.Translate.toString(transform), transition }}
      title={tr("끌어서 순서 바꾸기 · 우클릭하여 삭제 메뉴")}
      onContextMenu={onContextMenu}
      {...attributes}
      {...listeners}
    >
      <span className="font-mono text-hash">#</span>
      <span
        className={`grow truncate ${unread ? "font-semibold text-ink" : ""}`}
      >
        {channel.name}
      </span>
      {unread > 0 && <UnreadDot count={unread} />}
      {days !== undefined && <DdayBadge days={days} />}
    </NavLink>
  );
}

/** New messages since the channel was last read (PLAN Phase 16b): a dot, so the
 * row keeps showing the deadline as its number. */
function UnreadDot({ count }: { count: number }) {
  return (
    <span
      role="img"
      aria-label={tt`안 읽은 메시지 ${String(count)}개`}
      title={tt`안 읽은 메시지 ${String(count)}개`}
      className="size-[7px] shrink-0 rounded-full bg-ink"
    />
  );
}

function AddChannelDialog({
  kind,
  defaultAreaId,
  onClose,
}: {
  kind: Kind | null;
  defaultAreaId: string;
  onClose: () => void;
}) {
  const { data } = useChannels();
  const create = useCreateChannel();
  const [name, setName] = useState("");
  const [areaId, setAreaId] = useState("");
  const noun =
    kind === "project" ? t("프로젝트", "project") : t("과목", "course");

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!kind) return;
    create.mutate(
      {
        name: name.trim().replace(/^#/, ""),
        kind,
        area_id: areaId || defaultAreaId,
      },
      {
        onSuccess: () => {
          setName("");
          onClose();
        },
      },
    );
  };

  return (
    <Dialog
      open={kind !== null}
      onClose={onClose}
      title={tt`${noun} 채널 추가`}
    >
      <form onSubmit={submit} className="flex flex-col gap-4">
        <label className="flex flex-col gap-1">
          <span className={label}>{tr("이름")}</span>
          <input
            className={field}
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={
              kind === "project" ? tr("예: argos") : tr("예: 자료구조")
            }
            required
          />
        </label>
        {data && data.areas.length > 0 ? (
          <label className="flex flex-col gap-1">
            <span className={label}>{tr("영역")}</span>
            <select
              className={field}
              value={areaId || defaultAreaId}
              onChange={(e) => setAreaId(e.target.value)}
            >
              {data.areas.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                </option>
              ))}
            </select>
          </label>
        ) : (
          <p className="m-0 text-[12.5px] text-text-3">
            {tr("먼저 레일의 + 로 영역을 만들어 주세요.")}
          </p>
        )}
        <ErrorText error={create.error} />
        <div className="flex justify-end gap-2">
          <button type="button" className={btn.ghost} onClick={onClose}>
            {tr("취소")}
          </button>
          <button
            type="submit"
            className={btn.cta}
            disabled={create.isPending || !data?.areas.length}
          >
            {tr("추가하기")}
          </button>
        </div>
      </form>
    </Dialog>
  );
}

/** Agents as bot users (PLAN Phase 5 "DM 섹션"): click opens a 1:1 conversation. */
function AgentSection() {
  const agents = useAgents();
  const settings = useAgentSettings();
  const openDM = useOpenDM();
  const navigate = useNavigate();
  const busy = useBusyAgents();
  const { channelId } = useParams();
  const channels = useChannels();
  const unread = useUnread();
  const current = channels.data?.channels.find((c) => c.id === channelId);
  const dmOf = (agentId: string) =>
    channels.data?.channels.find(
      (c) => c.kind === "dm" && c.default_agent_id === agentId,
    );

  return (
    <Section title={tr("에이전트")}>
      {agents.data?.map((a) => {
        const active =
          current?.kind === "dm" && current.default_agent_id === a.id;
        const dm = dmOf(a.id);
        const fresh = dm ? (unread.data?.[dm.id] ?? 0) : 0;
        return (
          <button
            key={a.id}
            type="button"
            title={a.available ? undefined : (a.problem ?? undefined)}
            aria-current={active ? "page" : undefined}
            onClick={() =>
              openDM.mutate(a.name, {
                onSuccess: (dm) => navigate(`/c/${dm.id}`),
              })
            }
            className={`${row} h-[34px] cursor-pointer text-left ${a.available ? "" : "opacity-55"}`}
          >
            <AgentAvatar id={a.name} size={22} />
            <span
              className={`grow truncate ${fresh ? "font-semibold text-ink" : ""}`}
            >
              {a.display_name}
            </span>
            {fresh > 0 && <UnreadDot count={fresh} />}
            {busy.has(a.name) ? (
              <span className="text-[11.5px] text-meta">
                <PawTrail label={tr("입력 중")} />
              </span>
            ) : (
              settings.data?.default_agent === a.name && (
                <span className="text-[11.5px] text-meta">{tr("기본")}</span>
              )
            )}
          </button>
        );
      })}
    </Section>
  );
}
