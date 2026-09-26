import { format } from "date-fns";
import { type ReactNode, useEffect, useState } from "react";
import { Link } from "react-router";
import {
  type CalEvent,
  type Channel,
  type InboxItem,
  STATUSES,
  type Task,
  useChannels,
  useConfig,
  useOpenInbox,
  usePendingApprovals,
  useTasks,
  useToday,
  useUpdateInbox,
} from "../api";
import { InboxRow } from "../cards";
import { CheckButton, useCompleteTask } from "../complete";
import { dday, fmt, inZone, relativeDue } from "../dates";
import { RoutinePanel } from "../routines";
import { btn, card, DdayBadge } from "../ui";

const DAY_MS = 24 * 60 * 60 * 1000;

function useNow(intervalMs = 60_000): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}

/** Home (docs/design/Today.dc.html): today's schedule, near deadlines, open inbox, and
 * per-course progress. */
export function TodayPage() {
  const now = useNow();
  const today = useToday();
  const tasks = useTasks();
  const inbox = useOpenInbox();
  const channels = useChannels();
  const approvals = usePendingApprovals().data?.length ?? 0;
  const byId = new Map(channels.data?.channels.map((c) => [c.id, c]));

  const events = today.data?.events ?? [];
  const due = today.data?.due_tasks ?? [];

  return (
    <div className="min-h-0 grow overflow-y-auto">
      <div className="mx-auto flex w-full max-w-[1320px] flex-col gap-6 px-9 pt-8 pb-[26px]">
        <header className="flex flex-wrap items-end gap-5">
          <div className="flex grow flex-col gap-2">
            <div className="font-mono text-[12px] text-meta">
              {format(inZone(now), "EEE · yyyy.MM.dd · HH:mm").toUpperCase()}
            </div>
            <h1 className="m-0 text-[50px] leading-[1.1] font-light tracking-[-0.02em] text-ink">
              오늘 챙길 것
            </h1>
          </div>
          <div className="flex gap-2.5">
            <Stat label="일정" value={events.length} />
            <Stat label="마감 임박" value={due.length} />
            <Stat label="수집함" value={today.data?.inbox_count ?? 0} />
            <Link to="/approvals">
              <Stat
                label="승인 대기"
                value={approvals}
                danger={approvals > 0}
              />
            </Link>
          </div>
        </header>

        <div className="grid min-h-[360px] grid-cols-3 gap-[18px]">
          <Schedule events={events} now={now} />
          <Deadlines due={due} tasks={tasks.data ?? []} byId={byId} now={now} />
          <Inbox items={inbox.data?.items ?? []} now={now} byId={byId} />
        </div>

        <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,2fr)] items-start gap-[18px]">
          <RoutinePanel />
          <Progress
            channels={channels.data?.channels ?? []}
            tasks={tasks.data ?? []}
          />
        </div>
      </div>
    </div>
  );
}

function Stat({
  label,
  value,
  danger,
}: {
  label: string;
  value: number;
  danger?: boolean;
}) {
  return (
    <div
      className={`${card} flex min-w-[88px] flex-col gap-0.5 px-[18px] py-3`}
    >
      <span className="text-[12px] text-meta">{label}</span>
      <span
        className={`text-[30px] leading-[1.15] font-light tracking-[-0.02em] ${danger ? "text-danger" : "text-ink"}`}
      >
        {value}
      </span>
    </div>
  );
}

function Panel({
  title,
  aside,
  label,
  children,
}: {
  title: string;
  aside?: ReactNode;
  label: string;
  children: ReactNode;
}) {
  return (
    <section
      aria-label={label}
      className={`${card} flex min-h-0 flex-col gap-3 p-[22px]`}
    >
      <div className="flex items-baseline gap-2">
        <h2 className="m-0 grow text-[20px] font-light tracking-[-0.02em] text-ink">
          {title}
        </h2>
        {aside && <span className="text-[12px] text-meta">{aside}</span>}
      </div>
      <div className="flex min-h-0 flex-col gap-2.5 overflow-y-auto">
        {children}
      </div>
    </section>
  );
}

function Empty({ children }: { children: ReactNode }) {
  return <p className="m-0 py-4 text-[13px] text-meta">{children}</p>;
}

// --- schedule -----------------------------------------------------------------

function untilLabel(start: Date, now: Date): string | null {
  const minutes = Math.round((start.getTime() - now.getTime()) / 60_000);
  if (minutes <= 0 || minutes > 180) return null;
  return minutes < 60
    ? `${minutes}분 뒤`
    : `${Math.floor(minutes / 60)}시간 ${minutes % 60}분 뒤`;
}

function Schedule({ events, now }: { events: CalEvent[]; now: Date }) {
  const allDay = events.filter((e) => e.all_day);
  const timed = events
    .filter((e) => !e.all_day && e.starts_at)
    .sort((a, b) => (a.starts_at ?? "").localeCompare(b.starts_at ?? ""));
  const ended = (e: CalEvent) =>
    new Date(e.ends_at ?? e.starts_at ?? 0).getTime() <= now.getTime();
  const nowIndex = timed.findIndex((e) => !ended(e));
  const nowAt = nowIndex === -1 ? timed.length : nowIndex;

  const row = (e: CalEvent) => {
    const start = new Date(e.starts_at ?? 0);
    const past = ended(e);
    const note = past ? "끝남" : untilLabel(start, now);
    return (
      <div key={e.id} className="grid grid-cols-[50px_minmax(0,1fr)] gap-3">
        <span className="pt-3 font-mono text-[11.5px] text-meta">
          {fmt(start, "HH:mm")}
        </span>
        <div
          className={`flex flex-col gap-1 rounded-xl bg-page px-3.5 py-2.5 ${past ? "opacity-55" : ""}`}
        >
          <div className="flex items-center gap-2">
            <span className="grow font-medium text-ink">{e.title}</span>
            {note && <span className="text-[11.5px] text-text-2">{note}</span>}
          </div>
          <span className="font-mono text-[11.5px] text-text-3">
            {fmt(start, "HH:mm")}
            {e.ends_at ? `–${fmt(e.ends_at, "HH:mm")}` : ""}
          </span>
        </div>
      </div>
    );
  };

  return (
    <Panel title="일정" label="오늘 일정">
      {events.length === 0 && <Empty>오늘은 일정이 없어요.</Empty>}
      {allDay.map((e) => (
        <div key={e.id} className="grid grid-cols-[50px_minmax(0,1fr)] gap-3">
          <span className="pt-2.5 text-[11.5px] text-meta">종일</span>
          <div className="rounded-xl bg-page px-3.5 py-2.5 font-medium text-ink">
            {e.title}
          </div>
        </div>
      ))}
      {timed.slice(0, nowAt).map(row)}
      {timed.length > 0 && (
        <div className="my-0.5 grid grid-cols-[50px_minmax(0,1fr)] items-center gap-3">
          <span className="font-mono text-[11.5px] font-medium text-ink">
            {fmt(now, "HH:mm")}
          </span>
          <span className="flex items-center">
            <span className="size-[7px] rounded-full bg-ink" />
            <span className="h-px grow bg-ink" />
          </span>
        </div>
      )}
      {timed.slice(nowAt).map(row)}
    </Panel>
  );
}

// --- deadlines ----------------------------------------------------------------

function Deadlines({
  due,
  tasks,
  byId,
  now,
}: {
  due: Task[];
  tasks: Task[];
  byId: Map<string, Channel>;
  now: Date;
}) {
  const config = useConfig();
  const completion = useCompleteTask();
  const undated = tasks.filter((t) => !t.due_at && t.status !== "done");

  return (
    <Panel
      title="마감 임박"
      label="마감 임박"
      aside={config.data ? `${config.data.due_soon_days}일 이내` : undefined}
    >
      {due.length === 0 && <Empty>가까운 마감이 없어요.</Empty>}
      {due.map((t) => (
        <div
          key={t.id}
          className={`flex items-start gap-3 rounded-xl bg-page px-3.5 py-3 ${completion.pending.has(t.id) ? "completing" : ""}`}
        >
          <div className="pt-0.5">
            <DdayBadge days={dday(t.due_at ?? "", now)} />
          </div>
          <Link
            to={`/c/${t.channel_id}/kanban?task=${t.id}`}
            className="flex min-w-0 grow flex-col gap-1"
          >
            <span className="font-medium text-ink">{t.title}</span>
            <span className="text-[12px] text-text-3">
              {byId.get(t.channel_id)?.kind === "personal"
                ? "내 공간"
                : `# ${byId.get(t.channel_id)?.name}`}{" "}
              · {relativeDue(t.due_at ?? "", now)}
            </span>
          </Link>
          <CheckButton
            checked={completion.pending.has(t.id)}
            label={`${t.title} 완료 처리`}
            onClick={() => completion.complete(t)}
          />
        </div>
      ))}
      {undated.length > 0 && (
        <div className="flex items-center gap-2.5 rounded-xl border border-dashed border-line px-3.5 py-2.5 text-[12.5px] text-text-3">
          <span className="grow">
            날짜 없는 할 일 {undated.length}개가 조용히 있어요
          </span>
          <Link
            to={`/c/${undated[0].channel_id}/kanban?task=${undated[0].id}`}
            className="font-medium underline underline-offset-[3px]"
          >
            날짜 정하기
          </Link>
        </div>
      )}
    </Panel>
  );
}

// --- inbox --------------------------------------------------------------------

function Inbox({
  items,
  now,
  byId,
}: {
  items: InboxItem[];
  now: Date;
  byId: Map<string, Channel>;
}) {
  const update = useUpdateInbox();
  const stale = items.filter(
    (i) => now.getTime() - new Date(i.created_at).getTime() > DAY_MS,
  );
  const inboxChannel = [...byId.values()].find(
    (c) => c.kind === "system" && c.name === "inbox",
  );

  return (
    <Panel
      title="수집함"
      label="수집함"
      aside={`정리 안 된 입력 ${items.length}`}
    >
      {items.length === 0 && <Empty>수집함이 비었어요.</Empty>}
      {items.map((i) => (
        <div
          key={i.id}
          className="flex flex-col gap-2.5 rounded-xl bg-page p-3.5"
        >
          <div className="text-[14px] text-ink">"{i.raw_text}"</div>
          <InboxRow item={i} />
          <div className="flex items-center gap-1.5 text-[11.5px] text-meta">
            <span className="rounded-full border border-line-soft px-[9px] py-px">
              ↳ {i.captured_via}
            </span>
            <span className="font-mono">{fmt(i.created_at, "M/d HH:mm")}</span>
            <span className="grow" />
            <button
              type="button"
              className={`${btn.ghost} h-8 text-[12.5px]`}
              onClick={() => update.mutate({ id: i.id, status: "dismissed" })}
            >
              넘기기
            </button>
          </div>
        </div>
      ))}
      {stale.length > 0 && (
        <div className="flex items-center gap-2 px-1 py-0.5 text-[12px] text-danger">
          <span className="size-1.5 rounded-full bg-danger" />
          <span className="grow">{stale.length}개는 하루 넘게 방치됨</span>
          {inboxChannel && (
            <Link
              to={`/c/${inboxChannel.id}`}
              className="underline underline-offset-[3px]"
            >
              전부 보기
            </Link>
          )}
        </div>
      )}
    </Panel>
  );
}

// --- progress -----------------------------------------------------------------

const STEP: Record<Task["status"], string> = {
  backlog: "bg-step-1",
  todo: "bg-step-2",
  in_progress: "bg-step-3",
  review: "bg-step-4",
  done: "bg-step-5",
};

function Progress({ channels, tasks }: { channels: Channel[]; tasks: Task[] }) {
  // Courses plus the personal board shown under 내 공간.
  const courses = channels.filter(
    (c) => c.kind === "course" || c.kind === "personal",
  );
  const inbox = channels.find((c) => c.kind === "system" && c.name === "inbox");
  if (courses.length === 0) return null;
  const grid =
    "grid grid-cols-[120px_repeat(5,minmax(0,1fr))] items-center gap-2.5";

  return (
    <section
      aria-label="진행 현황"
      className={`${card} flex flex-col gap-3 px-[22px] py-[18px]`}
    >
      <div className={`${grid} text-[11.5px] font-medium text-meta`}>
        <span>진행 현황</span>
        {STATUSES.map((s) => (
          <span key={s.id}>{s.label}</span>
        ))}
      </div>
      {courses.map((c) => (
        <Link
          key={c.id}
          to={`/c/${c.kind === "personal" && inbox ? inbox.id : c.id}/kanban`}
          className={`${grid} text-[13px]`}
        >
          <span className="truncate text-ink">
            {c.kind === "personal" ? "내 공간" : `# ${c.name}`}
          </span>
          {STATUSES.map((s) => {
            const inCell = tasks.filter(
              (t) => t.channel_id === c.id && t.status === s.id,
            );
            const count = inCell.length;
            return (
              <span
                key={s.id}
                className="flex flex-wrap gap-1"
                title={`${s.label} ${count}`}
              >
                {inCell.slice(0, 8).map((t) => (
                  <span
                    key={t.id}
                    className={`h-2 w-[18px] rounded-full ${STEP[s.id]}`}
                  />
                ))}
                {count > 8 && (
                  <span className="font-mono text-[11px] text-meta">
                    +{count - 8}
                  </span>
                )}
              </span>
            );
          })}
        </Link>
      ))}
    </section>
  );
}
