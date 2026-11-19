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
  Fragment,
  type MouseEvent,
  type ReactNode,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  Link,
  NavLink,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router";
import { useBusyAgents } from "../agentStream";
import { AgentAvatar } from "../agents";
import {
  type Channel,
  type Task,
  useAgentSettings,
  useAgents,
  useChannels,
  useChannelThreads,
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
  SelectMenu,
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
  const [adding, setAdding] = useState(false);
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
  const personal = channels.find((c) => c.kind === "personal");
  const { channelId: openChannel } = useParams();
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
          <NavLink to="/today" className={row}>
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
          {inbox && personal && openChannel === inbox.id && (
            <ChannelSessions
              channelId={personal.id}
              base={`/c/${inbox.id}/chat`}
            />
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
          {SECTIONS.filter(
            // Courses only come from the LMS: no empty 과목 header without any.
            ({ kind }) =>
              kind !== "course" || inScope.some((c) => c.kind === "course"),
          ).map(({ kind, title }) => (
            <Section
              key={kind}
              title={title}
              action={
                // Courses come from the LMS; only projects are made by hand.
                kind === "project" ? (
                  <button
                    type="button"
                    aria-label={tt`${title} 추가`}
                    className="flex size-6 cursor-pointer items-center justify-center rounded-full text-meta hover:bg-inset hover:text-ink"
                    onClick={() => setAdding(true)}
                  >
                    <PlusIcon size={14} />
                  </button>
                ) : undefined
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
        open={adding}
        defaultAreaId={areaId ?? data?.areas[0]?.id ?? ""}
        onClose={() => setAdding(false)}
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
  const { channelId } = useParams();
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
          <Fragment key={c.id}>
            <ChannelRow
              channel={c}
              days={due.get(c.id)}
              unread={unread?.[c.id] ?? 0}
              onContextMenu={(event) => onMenu(event, c)}
            />
            {c.id === channelId && <ChannelSessions channelId={c.id} />}
          </Fragment>
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
  // Stays a link: dnd-kit's role="button" would hide that from screen readers.
  const { role: _role, ...linkAttributes } = attributes;
  // Releasing a drag over the row would also click it and open the channel.
  const dragged = useRef(false);
  if (isDragging) dragged.current = true;
  return (
    <NavLink
      ref={setNodeRef}
      to={`/c/${channel.id}`}
      className={`${row} ${isDragging ? "relative z-10 bg-inset shadow-sm" : ""}`}
      style={{ transform: CSS.Translate.toString(transform), transition }}
      title={tr("끌어서 순서 바꾸기 · 우클릭하여 삭제 메뉴")}
      onContextMenu={onContextMenu}
      onPointerDownCapture={() => {
        dragged.current = false;
      }}
      onClickCapture={(event) => {
        if (!dragged.current) return;
        dragged.current = false;
        event.preventDefault();
      }}
      {...linkAttributes}
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

const SESSIONS_SHOWN = 6;

/** The open channel's conversations (v0.2, Discord-style tree): a new one, then the
 * recent ones, newest activity first; a dot marks one an agent is answering in. */
function ChannelSessions({
  channelId,
  base = `/c/${channelId}`,
}: {
  channelId: string; // whose conversations
  base?: string; // where its chat lives (내 공간: the inbox's chat tab)
}) {
  const threads = useChannelThreads(channelId);
  const [params] = useSearchParams();
  const [all, setAll] = useState(false);
  const current = params.get("session");
  const list = threads.data ?? [];
  const shown = all ? list : list.slice(0, SESSIONS_SHOWN);
  const item =
    "flex h-7 items-center gap-2 rounded-full pr-3 pl-8 text-[12.5px] text-text-3 hover:bg-inset hover:text-ink aria-[current=true]:bg-inset aria-[current=true]:text-ink";
  return (
    <nav
      aria-label={tr("대화 목록", "Conversations")}
      className="flex flex-col gap-px pb-1"
    >
      <Link
        to={base}
        aria-current={current === null || undefined}
        className={item}
      >
        <PlusIcon size={12} />
        <span className="grow truncate">{tr("새 세션", "New session")}</span>
      </Link>
      {shown.map((t) => (
        <Link
          key={t.root_message_id}
          to={`${base}?session=${t.root_message_id}`}
          aria-current={current === t.root_message_id || undefined}
          className={item}
          title={t.preview}
        >
          <span
            className={`size-1.5 shrink-0 rounded-full ${t.active ? "animate-pulse bg-ink" : "bg-line"}`}
            aria-hidden
          />
          <span className="grow truncate">{t.title}</span>
          <span className="shrink-0 font-mono text-[10.5px] text-meta">
            {fmt(t.updated_at, "M/d")}
          </span>
        </Link>
      ))}
      {list.length > SESSIONS_SHOWN && (
        <button
          type="button"
          className="h-6 cursor-pointer pl-8 text-left text-[11.5px] text-meta hover:text-ink"
          onClick={() => setAll(!all)}
        >
          {all
            ? tr("접기", "Show less")
            : tt`이전 대화 ${list.length - SESSIONS_SHOWN}개 더`}
        </button>
      )}
    </nav>
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

/** A channel made by hand is always a project; course channels come from the LMS. */
export function AddChannelDialog({
  open,
  defaultAreaId,
  onClose,
}: {
  open: boolean;
  defaultAreaId: string;
  onClose: () => void;
}) {
  const { data } = useChannels();
  const create = useCreateChannel();
  const [name, setName] = useState("");
  const [areaId, setAreaId] = useState("");

  const submit = (e: FormEvent) => {
    e.preventDefault();
    create.mutate(
      {
        name: name.trim().replace(/^#/, ""),
        kind: "project",
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
      open={open}
      onClose={onClose}
      title={tr("프로젝트 채널 추가", "Add project channel")}
    >
      <form onSubmit={submit} className="flex flex-col gap-4">
        <label className="flex flex-col gap-1">
          <span className={label}>{tr("이름")}</span>
          <input
            className={field}
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={tr("예: argos")}
            required
          />
        </label>
        {data && data.areas.length > 0 ? (
          <label className="flex flex-col gap-1">
            <span className={label}>{tr("영역")}</span>
            <SelectMenu
              className={field}
              value={areaId || defaultAreaId}
              onChange={(value) => setAreaId(value)}
              options={[
                ...data.areas.map((a) => ({ value: a.id, label: a.name })),
              ]}
            />
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
