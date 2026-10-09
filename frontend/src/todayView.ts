import {
  addDays,
  addHours,
  startOfDay,
  startOfHour,
  startOfMonth,
  startOfWeek,
} from "date-fns";
import type { CalEvent, Task } from "./api";
import { dateInZone, fmt, inZone } from "./dates";
import { t } from "./i18n";

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/** One thing on the Today timelines: a calendar event or an open task's deadline. */
export type Item = {
  key: string;
  kind: "event" | "due";
  start: number;
  end: number;
  allDay: boolean;
  title: string;
  channelId: string;
  location: string | null;
  taskId?: string;
};

/** Midnight of `ms`'s day in the display zone. */
export const dayStart = (ms: number) =>
  startOfDay(inZone(new Date(ms))).getTime();

const zonedDate = (isoDate: string) => dateInZone(isoDate).getTime();

export function scheduleItems(events: CalEvent[], tasks: Task[]): Item[] {
  const items: Item[] = [];
  for (const e of events) {
    let start: number;
    let end: number;
    if (e.all_day && e.start_date) {
      start = zonedDate(e.start_date);
      end = e.end_date ? zonedDate(e.end_date) : start + DAY;
    } else if (e.starts_at) {
      start = Date.parse(e.starts_at);
      end = e.ends_at ? Date.parse(e.ends_at) : start + HOUR;
    } else continue;
    items.push({
      // Recurring events repeat their id, so the start keeps keys unique.
      key: `event:${e.id}:${start}`,
      kind: "event",
      start,
      end,
      allDay: e.all_day,
      title: e.title,
      channelId: e.channel_id,
      location: e.location,
    });
  }
  for (const task of tasks) {
    if (!task.due_at || task.status === "done") continue;
    const at = Date.parse(task.due_at);
    items.push({
      key: `due:${task.id}`,
      kind: "due",
      start: at,
      end: at,
      allDay: false,
      title: task.title,
      channelId: task.channel_id,
      location: null,
      taskId: task.id,
    });
  }
  return items.sort((a, b) => a.start - b.start);
}

export type WeekRow = {
  channelId: string;
  lanes: number;
  items: (Item & { left: number; lane: number })[];
};

/** Channels with something in [start, start + days), earliest first. `left` is a
 * percent; items less than a day apart go on separate lanes so labels don't overlap. */
export function weekRows(
  items: Item[],
  start: number,
  days: number,
): WeekRow[] {
  const span = days * DAY;
  const rows = new Map<string, WeekRow & { ends: number[] }>();
  for (const item of items) {
    if (item.start < start || item.start >= start + span) continue;
    const row = rows.get(item.channelId) ?? {
      channelId: item.channelId,
      lanes: 1,
      items: [],
      ends: [],
    };
    let lane = row.ends.findIndex((end) => end <= item.start);
    if (lane === -1) lane = row.ends.length;
    row.ends[lane] = item.start + DAY;
    row.lanes = Math.max(row.lanes, lane + 1);
    row.items.push({
      ...item,
      left: ((item.start - start) / span) * 100,
      lane,
    });
    rows.set(item.channelId, row);
  }
  return [...rows.values()].map(({ ends: _, ...row }) => row);
}

export type ClockView = {
  hours: { at: number; top: number; label: string }[];
  blocks: { item: Item; top: number; height: number }[];
};

/** Hour lines and timed events between `from` and `to`, scaled to `height` pixels. */
export function clockWindow(
  items: Item[],
  from: number,
  to: number,
  height: number,
): ClockView {
  const px = height / (to - from);
  const hours: ClockView["hours"] = [];
  for (
    let at = addHours(startOfHour(inZone(new Date(from))), 1).getTime();
    at <= to;
    at += HOUR
  ) {
    const midnight = dayStart(at) === at;
    hours.push({
      at,
      top: (at - from) * px,
      label: fmt(new Date(at), midnight ? "EEE HH" : "HH"),
    });
  }
  const blocks = items
    .filter(
      (i) => i.kind === "event" && !i.allDay && i.end > from && i.start < to,
    )
    .map((item) => ({
      item,
      top: (item.start - from) * px,
      height: (item.end - item.start) * px,
    }));
  return { hours, blocks };
}

/** The timed event happening at `now` and the next one to start. */
export function countdown(
  items: Item[],
  now: number,
): { current?: Item; next?: Item } {
  const timed = items.filter((i) => i.kind === "event" && !i.allDay);
  const current = timed.find((i) => i.start <= now && now < i.end);
  const next = timed.find((i) => i.start > now);
  return { ...(current && { current }), ...(next && { next }) };
}

export function duration(ms: number): string {
  const minutes = Math.max(1, Math.round(ms / MINUTE));
  const days = Math.floor(minutes / 1440);
  const hours = Math.floor((minutes % 1440) / 60);
  const rest = minutes % 60;
  if (days) return t(`${days}일 ${hours}시간`, `${days}d ${hours}h`);
  if (!hours) return t(`${rest}분`, `${rest}m`);
  return rest
    ? t(`${hours}시간 ${rest}분`, `${hours}h ${rest}m`)
    : t(`${hours}시간`, `${hours}h`);
}

/** Day starts of the Monday-first weeks covering `ms`'s month. */
export function monthGrid(ms: number): number[] {
  const first = startOfMonth(inZone(new Date(ms)));
  const nextMonth = addDays(first, 32);
  const last = startOfMonth(nextMonth).getTime();
  const days: number[] = [];
  for (
    let day = startOfWeek(first, { weekStartsOn: 1 });
    ;
    day = addDays(day, 1)
  ) {
    if (day.getTime() >= last && days.length % 7 === 0) break;
    days.push(day.getTime());
  }
  return days;
}
