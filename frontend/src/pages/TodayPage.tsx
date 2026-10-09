import { addDays, addMonths, differenceInCalendarDays, format } from "date-fns";
import { type CSSProperties, type ReactNode, useEffect, useState } from "react";
import { Link } from "react-router";
import {
  type Channel,
  type InboxItem,
  type Task,
  useChannels,
  useConfig,
  useEvents,
  useOpenInbox,
  useTasks,
  useToday,
  useUpdateInbox,
} from "../api";
import { InboxRow } from "../cards";
import { CheckButton, useCompleteTask } from "../complete";
import { dday, fmt, inZone, relativeDue } from "../dates";
import { t, tr, tt } from "../i18n";
import {
  CalendarIcon,
  CheckIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  ClockIcon,
  KanbanIcon,
} from "../icons";
import { RoutinePanel } from "../routines";
import {
  clockWindow,
  countdown,
  dayStart,
  duration,
  type Item,
  monthGrid,
  scheduleItems,
  weekRows,
} from "../todayView";
import { btn, card, DdayBadge } from "../ui";

const HOUR = 3_600_000;
const DAY_MS = 24 * HOUR;
const WEEK_DAYS = 7;

function useNow(intervalMs = 30_000): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}

const channelName = (c: Channel | undefined) =>
  !c ? "" : c.kind === "personal" ? tr("내 공간") : `# ${c.name}`;

/** Home: KPIs, a week of events and deadlines per channel, near deadlines and the
 * inbox on the left; a month calendar, a live clock (or a picked day) and routines on
 * the right. Both columns end level, so no card leaves a gap. */
export function TodayPage() {
  const now = useNow();
  const today = useToday();
  const tasks = useTasks();
  const inbox = useOpenInbox();
  const channels = useChannels();
  const byId = new Map(channels.data?.channels.map((c) => [c.id, c]));

  const todayStart = dayStart(now.getTime());
  const [month, setMonth] = useState(todayStart);
  const [picked, setPicked] = useState<number | null>(null);
  const grid = monthGrid(month);
  // From yesterday so the clock's first hour sees events that began before midnight.
  const week = useEvents(
    new Date(todayStart - DAY_MS).toISOString(),
    new Date(todayStart + WEEK_DAYS * DAY_MS).toISOString(),
  );
  const monthEvents = useEvents(
    new Date(grid[0]).toISOString(),
    new Date(grid[grid.length - 1] + DAY_MS).toISOString(),
  );
  const openTasks = (tasks.data ?? []).filter((x) => x.status !== "done");
  const weekItems = scheduleItems(week.data ?? [], openTasks);
  const monthItems = scheduleItems(monthEvents.data ?? [], openTasks);

  const events = today.data?.events ?? [];
  const due = today.data?.due_tasks ?? [];
  const showDay = picked !== null && picked !== todayStart ? picked : null;

  return (
    <div className="min-h-0 grow overflow-y-auto">
      <div className="flex w-full flex-col gap-[18px] px-9 pt-8 pb-[26px] max-sm:px-4">
        <header data-tauri-drag-region="deep" className="flex flex-col gap-2">
          <div className="font-mono text-[12px] text-meta">
            {format(inZone(now), "EEE · yyyy.MM.dd · HH:mm").toUpperCase()}
          </div>
          <h1 className="m-0 text-[44px] leading-[1.1] font-light tracking-[-0.02em] text-ink">
            {tr("오늘 챙길 것")}
          </h1>
          <p className="m-0 text-[15px] text-text-2">
            {summary(
              weekItems.filter(
                (i) =>
                  i.kind === "event" &&
                  i.end > now.getTime() &&
                  i.start < todayStart + DAY_MS,
              ).length,
              due.length,
              today.data?.inbox_count ?? 0,
            )}
          </p>
        </header>

        <Kpis
          doneThisWeek={today.data?.done_this_week ?? 0}
          doneLastWeek={today.data?.done_last_week ?? 0}
          tasks={tasks.data ?? []}
          due={due}
          eventsToday={events.length}
          next={countdown(weekItems, now.getTime()).next}
          now={now}
        />

        <div className="flex flex-wrap items-stretch gap-[18px]">
          <div className="flex min-w-0 flex-[2_1_640px] flex-col gap-[18px]">
            <WeekTimeline
              items={weekItems}
              byId={byId}
              start={todayStart}
              now={now}
            />
            <div className="flex grow flex-wrap items-stretch gap-[18px]">
              <Deadlines
                due={due}
                tasks={tasks.data ?? []}
                byId={byId}
                now={now}
              />
              <Inbox items={inbox.data?.items ?? []} now={now} byId={byId} />
            </div>
          </div>
          <div className="flex min-w-0 flex-[1_1_320px] flex-col gap-[18px]">
            <MonthCalendar
              month={month}
              grid={grid}
              items={monthItems}
              today={todayStart}
              picked={showDay}
              onMonth={setMonth}
              onPick={(day) =>
                setPicked((current) =>
                  current === day || day === todayStart ? null : day,
                )
              }
            />
            <NowPanel
              items={showDay === null ? weekItems : monthItems}
              byId={byId}
              now={now}
              day={showDay}
              today={todayStart}
              onBack={() => setPicked(null)}
            />
            <RoutinePanel className="grow" />
          </div>
        </div>
      </div>
    </div>
  );
}

function summary(events: number, due: number, inbox: number): string {
  let text: string;
  if (events === 0 && due === 0)
    text = t(
      "오늘은 챙길 일정과 마감이 없어요.",
      "Nothing scheduled or due today.",
    );
  else if (events === 0)
    text = t(
      `오늘은 일정이 없어요. 마감 ${due}개만 챙기면 돼요.`,
      `No events today. Just ${due} deadlines to watch.`,
    );
  else if (due === 0)
    text = t(`일정 ${events}개가 남았어요.`, `${events} events left today.`);
  else
    text = t(
      `마감 ${due}개와 일정 ${events}개가 남았어요.`,
      `${due} deadlines and ${events} events left.`,
    );
  if (inbox > 0)
    text += ` ${t(`인박스에 정리할 입력이 ${inbox}개 있어요.`, `${inbox} inbox items to sort.`)}`;
  return text;
}

// --- KPIs ---------------------------------------------------------------------

function Kpis({
  doneThisWeek,
  doneLastWeek,
  tasks,
  due,
  eventsToday,
  next,
  now,
}: {
  doneThisWeek: number;
  doneLastWeek: number;
  tasks: Task[];
  due: Task[];
  eventsToday: number;
  next: Item | undefined;
  now: Date;
}) {
  const diff = doneThisWeek - doneLastWeek;
  const review = tasks.filter((x) => x.status === "review").length;
  const active = tasks.filter(
    (x) => x.status === "in_progress" || x.status === "review",
  ).length;
  const first = due[0]?.due_at;
  const urgent = first !== undefined && first !== null && dday(first, now) <= 1;
  const nextToday =
    next && next.start < dayStart(now.getTime()) + DAY_MS ? next : undefined;
  return (
    <div className="grid grid-cols-[repeat(auto-fit,minmax(220px,1fr))] gap-[18px]">
      <Kpi
        icon={<CheckIcon size={20} />}
        label={tr("이번 주 완료")}
        value={doneThisWeek}
        sub={
          diff > 0
            ? t(`지난주보다 ${diff}개 더`, `${diff} more than last week`)
            : diff < 0
              ? t(
                  `지난주보다 ${-diff}개 적어요`,
                  `${-diff} fewer than last week`,
                )
              : t("지난주와 같아요", "Same as last week")
        }
      />
      <Kpi
        icon={<KanbanIcon size={20} />}
        label={tr("진행 중")}
        value={active}
        sub={
          review > 0
            ? t(`검토 대기 ${review}개 포함`, `${review} waiting for review`)
            : t("검토 대기 없음", "Nothing waiting for review")
        }
      />
      <Kpi
        icon={<ClockIcon size={20} />}
        label={tr("마감 임박")}
        value={due.length}
        sub={
          first
            ? t(
                `가장 빠른 마감 ${relativeDue(first, now)}`,
                `Next due ${relativeDue(first, now)}`,
              )
            : t("가까운 마감이 없어요", "No deadlines soon")
        }
        danger={urgent}
      />
      <Kpi
        icon={<CalendarIcon size={20} />}
        label={tr("오늘 일정")}
        value={eventsToday}
        sub={
          nextToday
            ? t(
                `다음 ${fmt(new Date(nextToday.start), "HH:mm")} ${nextToday.title}`,
                `Next ${fmt(new Date(nextToday.start), "HH:mm")} ${nextToday.title}`,
              )
            : next
              ? t(
                  `다음 일정 ${fmt(new Date(next.start), "EEE HH:mm")}`,
                  `Next ${fmt(new Date(next.start), "EEE HH:mm")}`,
                )
              : t("남은 일정이 없어요", "Nothing left today")
        }
      />
    </div>
  );
}

function Kpi({
  icon,
  label,
  value,
  sub,
  danger,
}: {
  icon: ReactNode;
  label: string;
  value: number;
  sub: string;
  danger?: boolean;
}) {
  return (
    <div className={`${card} flex items-center gap-4 px-[22px] py-5`}>
      <span className="grid size-12 shrink-0 place-items-center rounded-2xl bg-inset text-text-2">
        {icon}
      </span>
      <span className="flex min-w-0 flex-col gap-0.5">
        <span className="text-[12px] text-meta">{label}</span>
        <span className="text-[32px] leading-[1.15] font-light tracking-[-0.02em] text-ink">
          {value}
        </span>
        <span
          className={`truncate text-[12px] ${danger ? "text-danger" : "text-text-3"}`}
        >
          {sub}
        </span>
      </span>
    </div>
  );
}

function Panel({
  title,
  aside,
  label,
  className = "",
  children,
}: {
  title: ReactNode;
  aside?: ReactNode;
  label: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section
      aria-label={label}
      className={`${card} flex min-h-0 min-w-0 flex-col gap-3 p-[22px] ${className}`}
    >
      <div className="flex min-h-8 items-center gap-2">
        <h2 className="m-0 grow text-[19px] font-light tracking-[-0.02em] text-ink">
          {title}
        </h2>
        {aside}
      </div>
      {children}
    </section>
  );
}

function Empty({ children }: { children: ReactNode }) {
  return <p className="m-0 py-4 text-[13px] text-meta">{children}</p>;
}

const Aside = ({ children }: { children: ReactNode }) => (
  <span className="text-[12px] text-meta">{children}</span>
);

// --- week timeline ------------------------------------------------------------

const LANE = 26;

function WeekTimeline({
  items,
  byId,
  start,
  now,
}: {
  items: Item[];
  byId: Map<string, Channel>;
  start: number;
  now: Date;
}) {
  const rows = weekRows(items, start, WEEK_DAYS);
  const nowLeft = ((now.getTime() - start) / (WEEK_DAYS * DAY_MS)) * 100;
  const days = Array.from({ length: WEEK_DAYS }, (_, i) =>
    addDays(inZone(new Date(start)), i),
  );
  return (
    <Panel
      title={tr("앞으로 7일")}
      label={tr("앞으로 7일")}
      aside={
        <span className="flex flex-wrap gap-x-3.5 gap-y-1 text-[12px] text-meta">
          <span className="inline-flex items-center gap-1.5">
            <span className="h-2 w-3.5 rounded bg-step-3" />
            {tr("일정")}
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span className="size-2 rotate-45 bg-step-4" />
            {tr("마감")}
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span className="size-2 rotate-45 bg-danger" />
            {tr("하루 안")}
          </span>
        </span>
      }
    >
      {rows.length === 0 ? (
        <Empty>{tr("앞으로 7일 동안 일정과 마감이 없어요.")}</Empty>
      ) : (
        <div className="overflow-x-auto">
          <div className="flex min-w-[600px] flex-col">
            <div className="grid grid-cols-[140px_minmax(0,1fr)] gap-3 border-b border-line-soft pb-1.5">
              <span />
              <div className="grid grid-cols-7">
                {days.map((day, i) => (
                  <span
                    key={day.getTime()}
                    className={`pl-1.5 text-[11.5px] ${i === 0 ? "font-medium text-ink" : day.getDay() % 6 === 0 ? "text-hash" : "text-meta"}`}
                  >
                    {i === 0 ? tt`오늘 ${day.getDate()}` : fmt(day, "EEE d")}
                  </span>
                ))}
              </div>
            </div>
            {rows.map((row) => (
              <div
                key={row.channelId}
                className="grid grid-cols-[140px_minmax(0,1fr)] items-center gap-3 border-b border-line-soft"
              >
                <Link
                  to={`/c/${row.channelId}/calendar`}
                  className="truncate text-[13.5px] text-text hover:text-ink"
                >
                  {channelName(byId.get(row.channelId))}
                </Link>
                <div
                  className="relative bg-[linear-gradient(to_right,var(--line-soft)_1px,transparent_1px)] bg-[length:calc(100%/7)_100%]"
                  style={{ height: row.lanes * LANE + 18 }}
                >
                  <span className="absolute inset-y-0 left-0 w-[calc(100%/7)] bg-inset" />
                  <span
                    className="absolute inset-y-0 w-px bg-ink"
                    style={{ left: `${nowLeft}%` }}
                  />
                  {row.items.map((item) => (
                    <TimelineMark
                      key={item.key}
                      item={item}
                      now={now}
                      style={{
                        left: `${item.left}%`,
                        top: 9 + item.lane * LANE,
                      }}
                    />
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </Panel>
  );
}

function TimelineMark({
  item,
  now,
  style,
}: {
  item: Item;
  now: Date;
  style: CSSProperties;
}) {
  const urgent = item.kind === "due" && item.start - now.getTime() < DAY_MS;
  const label =
    item.kind === "event" && !item.allDay
      ? `${item.title} ${fmt(new Date(item.start), "HH:mm")}`
      : item.title;
  const to =
    item.kind === "due"
      ? `/c/${item.channelId}/kanban?task=${item.taskId}`
      : `/c/${item.channelId}/calendar`;
  return (
    <Link
      to={to}
      title={label}
      style={style}
      className={`absolute flex h-[22px] max-w-[150px] items-center gap-1.5 text-[12px] text-text-2 hover:text-ink ${item.kind === "due" ? "-translate-x-1" : ""}`}
    >
      {item.kind === "due" ? (
        <span
          className={`size-2 shrink-0 rotate-45 ${urgent ? "bg-danger" : "bg-step-4"}`}
        />
      ) : (
        <span className="h-2 w-3.5 shrink-0 rounded bg-step-3" />
      )}
      <span className="truncate">{label}</span>
    </Link>
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
  const undated = tasks.filter((x) => !x.due_at && x.status !== "done");

  return (
    <Panel
      title={tr("마감 임박")}
      label={tr("마감 임박")}
      className="flex-[1_1_300px]"
      aside={
        config.data && <Aside>{tt`${config.data.due_soon_days}일 이내`}</Aside>
      }
    >
      <div className="flex min-h-0 grow flex-col gap-2.5">
        {due.length === 0 && <Empty>{tr("가까운 마감이 없어요.")}</Empty>}
        {due.map((x) => (
          <div
            key={x.id}
            className={`flex items-center gap-3 rounded-xl bg-page px-3.5 py-2.5 ${completion.pending.has(x.id) ? "completing" : ""}`}
          >
            <DdayBadge days={dday(x.due_at ?? "", now)} />
            <Link
              to={`/c/${x.channel_id}/kanban?task=${x.id}`}
              className="flex min-w-0 grow flex-col gap-0.5"
            >
              <span className="truncate font-medium text-ink">{x.title}</span>
              <span className="truncate text-[12px] text-text-3">
                {channelName(byId.get(x.channel_id))} ·{" "}
                {relativeDue(x.due_at ?? "", now)}
              </span>
            </Link>
            <CheckButton
              checked={completion.pending.has(x.id)}
              label={tt`${x.title} 완료 처리`}
              onClick={() => completion.complete(x)}
            />
          </div>
        ))}
        {undated.length > 0 && (
          <div className="mt-auto flex items-center gap-2.5 rounded-xl border border-dashed border-line px-3.5 py-2.5 text-[12.5px] text-text-3">
            <span className="grow">
              {t(
                `날짜 없는 할 일 ${undated.length}개`,
                `${undated.length} tasks have no due date`,
              )}
            </span>
            <Link
              to={`/c/${undated[0].channel_id}/kanban?task=${undated[0].id}`}
              className="font-medium text-text underline underline-offset-[3px]"
            >
              {tr("날짜 정하기")}
            </Link>
          </div>
        )}
      </div>
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
      title={tr("수집함")}
      label={tr("수집함")}
      className="flex-[1_1_300px]"
      aside={<Aside>{tt`정리 안 된 입력 ${items.length}`}</Aside>}
    >
      {items.length === 0 ? (
        <div className="flex grow flex-col items-center justify-center gap-1.5 py-4 text-center">
          <span className="text-[14px] text-text-2">
            {tr("수집함이 비었어요.")}
          </span>
          <span className="text-[12px] text-meta">
            {tr("채널에 적은 메모는 여기서 분류돼요.")}
          </span>
        </div>
      ) : (
        <div className="flex min-h-0 flex-col gap-2.5">
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
                <span className="font-mono">
                  {fmt(i.created_at, "M/d HH:mm")}
                </span>
                <span className="grow" />
                <button
                  type="button"
                  className={`${btn.ghost} h-8 text-[12.5px]`}
                  onClick={() =>
                    update.mutate({ id: i.id, status: "dismissed" })
                  }
                >
                  {tr("넘기기")}
                </button>
              </div>
            </div>
          ))}
          {stale.length > 0 && (
            <div className="flex items-center gap-2 px-1 py-0.5 text-[12px] text-danger">
              <span className="size-1.5 rounded-full bg-danger" />
              <span className="grow">
                {t(
                  `${stale.length}개는 하루 넘게 방치됨`,
                  `${stale.length} items have waited over a day`,
                )}
              </span>
              {inboxChannel && (
                <Link
                  to={`/c/${inboxChannel.id}`}
                  className="underline underline-offset-[3px]"
                >
                  {tr("전부 보기")}
                </Link>
              )}
            </div>
          )}
        </div>
      )}
    </Panel>
  );
}

// --- month calendar -----------------------------------------------------------

function MonthCalendar({
  month,
  grid,
  items,
  today,
  picked,
  onMonth,
  onPick,
}: {
  month: number;
  grid: number[];
  items: Item[];
  today: number;
  picked: number | null;
  onMonth: (month: number) => void;
  onPick: (day: number) => void;
}) {
  const shown = inZone(new Date(month));
  const busy = new Set(items.map((i) => dayStart(i.start)));
  const shift = (by: number) => onMonth(addMonths(shown, by).getTime());
  return (
    <section
      aria-label={tr("캘린더")}
      className={`${card} flex flex-col gap-2 px-[22px] py-5`}
    >
      <div className="flex items-center gap-1">
        <button
          type="button"
          className={btn.icon}
          aria-label={tr("이전 달")}
          onClick={() => shift(-1)}
        >
          <ChevronLeftIcon />
        </button>
        <h2 className="m-0 grow text-center text-[15px] font-medium text-ink">
          {fmt(shown, t("yyyy년 M월", "MMMM yyyy"))}
        </h2>
        <button
          type="button"
          className={btn.icon}
          aria-label={tr("다음 달")}
          onClick={() => shift(1)}
        >
          <ChevronRightIcon />
        </button>
      </div>
      <div className="grid grid-cols-7 text-center">
        {grid.slice(0, 7).map((day) => (
          <span key={day} className="h-[26px] text-[11px] text-meta">
            {fmt(new Date(day), "EEEEE")}
          </span>
        ))}
        {grid.map((day) => {
          const date = inZone(new Date(day));
          const isToday = day === today;
          const selected = day === picked;
          const inMonth = date.getMonth() === shown.getMonth();
          return (
            <span key={day} className="flex h-10 flex-col items-center gap-0.5">
              <button
                type="button"
                aria-label={fmt(date, t("M월 d일 (EEE)", "EEE, MMM d"))}
                aria-pressed={selected}
                onClick={() => onPick(day)}
                className={`grid size-8 cursor-pointer place-items-center rounded-full border-[1.5px] text-[13px] ${
                  isToday
                    ? "border-ink bg-ink font-medium text-page"
                    : selected
                      ? "border-ink bg-inset text-text"
                      : `border-transparent hover:bg-inset ${inMonth ? "text-text" : "text-hash"}`
                }`}
              >
                {date.getDate()}
              </button>
              <span
                className={`size-1 rounded-full ${busy.has(day) && !isToday ? "bg-step-3" : "bg-transparent"}`}
              />
            </span>
          );
        })}
      </div>
    </section>
  );
}

// --- now / picked day ---------------------------------------------------------

const CLOCK_HEIGHT = 300;

function NowPanel({
  items,
  byId,
  now,
  day,
  today,
  onBack,
}: {
  items: Item[];
  byId: Map<string, Channel>;
  now: Date;
  day: number | null;
  today: number;
  onBack: () => void;
}) {
  const at = now.getTime();
  const live = day === null;
  // Live: an hour back and nine ahead, the now line fixed near the top. A picked
  // day: 08:00–22:00, standing still.
  const from = live ? at - HOUR : day + 8 * HOUR;
  const to = live ? at + 9 * HOUR : day + 22 * HOUR;
  const view = clockWindow(items, from, to, CLOCK_HEIGHT);
  const shownDay = live ? today : day;
  const onShownDay = (i: Item) =>
    i.start >= shownDay && i.start < shownDay + DAY_MS;
  // All-day events, and on a picked day its deadlines, don't fit the hour axis.
  const chips = items.filter(
    (i) => onShownDay(i) && (i.allDay || (!live && i.kind === "due")),
  );
  const { current, next } = countdown(items, at);

  let big: string;
  let sub: string;
  if (live) {
    big = fmt(now, "HH:mm");
    sub = current
      ? t(
          `${current.title} 중 · ${duration(current.end - at)} 남음`,
          `${current.title} · ${duration(current.end - at)} left`,
        )
      : next
        ? t(
            `${next.title}까지 ${duration(next.start - at)}`,
            `${duration(next.start - at)} until ${next.title}`,
          )
        : t("남은 일정이 없어요", "Nothing coming up");
  } else {
    const days = differenceInCalendarDays(inZone(new Date(day)), inZone(now));
    big =
      days === 1
        ? t("내일", "Tomorrow")
        : days === -1
          ? t("어제", "Yesterday")
          : days > 0
            ? t(`${days}일 후`, `In ${days} days`)
            : t(`${-days}일 전`, `${-days} days ago`);
    const events = items.filter((i) => onShownDay(i) && i.kind === "event");
    const dues = items.filter((i) => onShownDay(i) && i.kind === "due");
    const parts = [
      events.length > 0 &&
        t(`일정 ${events.length}개`, `${events.length} events`),
      dues.length > 0 && t(`마감 ${dues.length}개`, `${dues.length} due`),
    ].filter(Boolean);
    sub = parts.length
      ? parts.join(" · ")
      : t("일정과 마감이 없어요", "Nothing scheduled or due");
  }

  return (
    <Panel
      title={
        live ? tr("지금") : fmt(new Date(day), t("M월 d일 (EEE)", "EEE, MMM d"))
      }
      label={live ? tr("지금") : tr("선택한 날")}
      aside={
        !live && (
          <button type="button" className={btn.outline} onClick={onBack}>
            {tr("지금으로")}
          </button>
        )
      }
    >
      <div className="flex flex-col gap-0.5">
        <span
          className="font-mono text-[34px] tracking-[-0.02em] text-ink"
          aria-live={live ? "off" : "polite"}
        >
          {big}
        </span>
        <span className="text-[13px] text-text-2">{sub}</span>
      </div>
      {chips.map((i) => (
        <div
          key={i.key}
          className="flex items-center gap-2.5 rounded-xl bg-inset px-3 py-2 text-[12.5px] text-text-2"
        >
          {i.kind === "due" ? (
            <span className="size-2 shrink-0 rotate-45 bg-step-4" />
          ) : (
            <span className="h-2 w-3.5 shrink-0 rounded bg-step-3" />
          )}
          <span className="truncate">
            {i.kind === "due"
              ? t(
                  `마감 ${fmt(new Date(i.start), "HH:mm")} · ${i.title}`,
                  `Due ${fmt(new Date(i.start), "HH:mm")} · ${i.title}`,
                )
              : t(`종일 · ${i.title}`, `All day · ${i.title}`)}{" "}
            · {channelName(byId.get(i.channelId))}
          </span>
        </div>
      ))}
      <div
        className="relative overflow-hidden rounded-2xl bg-inset"
        style={{ height: CLOCK_HEIGHT }}
      >
        {view.hours.map((h) => (
          <span key={h.at}>
            <span
              className="absolute right-0 left-[50px] h-px bg-line-soft"
              style={{ top: h.top }}
            />
            <span
              className="absolute left-2 font-mono text-[10.5px] text-hash"
              style={{ top: h.top - 7 }}
            >
              {h.label}
            </span>
          </span>
        ))}
        {view.blocks.map(({ item, top, height }) => {
          const past = item.end <= at;
          const happening = live && item.start <= at && at < item.end;
          const time = `${fmt(new Date(item.start), "HH:mm")}–${fmt(new Date(item.end), "HH:mm")}`;
          const where = item.location ?? channelName(byId.get(item.channelId));
          // Short events get one line so nothing is squeezed or cut.
          const compact = height < 44;
          return (
            <Link
              key={item.key}
              to={`/c/${item.channelId}/calendar`}
              className={`absolute right-2.5 left-[58px] flex overflow-hidden rounded-[10px] bg-card px-2.5 ${
                compact
                  ? "items-center gap-2"
                  : "flex-col justify-center gap-px"
              } ${happening ? "border-[1.5px] border-ink" : "border border-line-soft"} ${past ? "opacity-45" : ""}`}
              style={{ top: top + 1, height: Math.max(height - 2, 28) }}
            >
              <span className="truncate text-[13px] font-medium text-ink">
                {item.title}
              </span>
              <span className="truncate text-[11.5px] text-text-3">
                {compact ? time : `${time} · ${where}`}
              </span>
            </Link>
          );
        })}
        {view.blocks.length === 0 && (
          <span className="absolute inset-x-3 top-1/2 left-14 -translate-y-1/2 text-center text-[13px] text-meta">
            {live
              ? t(
                  "앞으로 9시간 동안 일정이 없어요",
                  "Nothing in the next 9 hours",
                )
              : t("08–22시에 일정이 없어요", "Nothing from 8 AM to 10 PM")}
          </span>
        )}
        {live && (
          <div
            className="absolute inset-x-0 flex -translate-y-1/2 items-center"
            style={{ top: (CLOCK_HEIGHT * HOUR) / (to - from) }}
          >
            <span className="w-[46px] shrink-0" />
            <span className="size-[7px] rounded-full bg-ink" />
            <span className="h-px grow bg-ink" />
          </div>
        )}
      </div>
    </Panel>
  );
}
