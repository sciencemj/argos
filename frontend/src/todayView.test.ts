import { beforeEach, expect, test } from "vitest";
import type { CalEvent, Task } from "./api";
import {
  clockWindow,
  countdown,
  dayStart,
  duration,
  monthGrid,
  scheduleItems,
  weekRows,
} from "./todayView";

const HOUR = 3_600_000;
const DAY = 24 * HOUR;
// Friday 2026-10-09 00:00 in Seoul.
const FRI = Date.parse("2026-10-08T15:00:00Z");

function event(over: Partial<CalEvent>): CalEvent {
  return {
    id: "e",
    channel_id: "os",
    title: "강의",
    all_day: false,
    starts_at: null,
    ends_at: null,
    start_date: null,
    end_date: null,
    location: null,
    rrule: null,
    calendar_id: null,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
    ...over,
  } as CalEvent;
}

function task(over: Partial<Task>): Task {
  return {
    id: "t",
    channel_id: "aice",
    title: "PART 3",
    description: null,
    status: "todo",
    position: 1,
    due_at: null,
    priority: null,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
    ...over,
  } as Task;
}

beforeEach(() => {
  localStorage.setItem("argos-language", "ko");
});

test("schedule items take timed and all-day events and open due tasks", () => {
  const items = scheduleItems(
    [
      event({ id: "os", starts_at: "2026-10-09T04:30:00Z" }),
      event({
        id: "trip",
        all_day: true,
        start_date: "2026-10-10",
        end_date: "2026-10-11",
      }),
    ],
    [
      task({ id: "p3", due_at: "2026-10-10T14:59:00Z" }),
      task({ id: "old", due_at: "2026-10-10T14:59:00Z", status: "done" }),
      task({ id: "nodate" }),
    ],
  );
  expect(items.map((i) => [i.key, i.kind, i.allDay])).toEqual([
    ["event:os:1791520200000", "event", false],
    ["event:trip:1791558000000", "event", true],
    ["due:p3", "due", false],
  ]);
  // An event without an end lasts an hour; all-day spans the zone's day.
  expect(items[0].end - items[0].start).toBe(HOUR);
  expect(items[1].start).toBe(FRI + DAY);
  expect(items[1].end).toBe(FRI + 2 * DAY);
});

test("dayStart is midnight in the display zone", () => {
  expect(dayStart(FRI + 13.5 * HOUR)).toBe(FRI);
});

test("week rows keep channels with something in range, earliest first", () => {
  const items = scheduleItems(
    [
      event({ id: "a", channel_id: "os", starts_at: "2026-10-12T04:30:00Z" }),
      event({ id: "b", channel_id: "ml", starts_at: "2026-10-20T01:30:00Z" }),
    ],
    [task({ id: "d", channel_id: "aice", due_at: "2026-10-10T14:59:00Z" })],
  );
  const rows = weekRows(items, FRI, 7);
  expect(rows.map((r) => r.channelId)).toEqual(["aice", "os"]);
  expect(rows[0].items[0].left).toBeCloseTo(
    ((DAY + 23.98 * HOUR) / (7 * DAY)) * 100,
    1,
  );
});

test("clock window places hour lines and events in pixels", () => {
  const items = scheduleItems(
    [
      event({
        id: "os",
        starts_at: "2026-10-09T04:30:00Z",
        ends_at: "2026-10-09T05:45:00Z",
      }),
    ],
    [task({ due_at: "2026-10-09T05:00:00Z" })],
  );
  const now = FRI + 11 * HOUR + 52 * 60_000;
  const view = clockWindow(items, now - HOUR, now + 9 * HOUR, 300);
  expect(view.hours[0].label).toBe("11");
  expect(view.blocks).toHaveLength(1);
  // 13:30 is 158 minutes after the 10:52 window start; 300px / 600min.
  expect(view.blocks[0].top).toBeCloseTo(79, 5);
  expect(view.blocks[0].height).toBeCloseTo(37.5, 5);
});

test("midnight hour lines carry the weekday", () => {
  const view = clockWindow([], FRI + 20 * HOUR, FRI + 26 * HOUR, 300);
  expect(view.hours.map((h) => h.label)).toContain("토 00");
});

test("countdown finds the current and the next timed event", () => {
  const items = scheduleItems(
    [
      event({
        id: "a",
        starts_at: "2026-10-09T01:30:00Z",
        ends_at: "2026-10-09T02:45:00Z",
      }),
      event({
        id: "b",
        starts_at: "2026-10-09T04:30:00Z",
        ends_at: "2026-10-09T05:45:00Z",
      }),
    ],
    [],
  );
  const during = countdown(items, Date.parse("2026-10-09T02:00:00Z"));
  expect(during.current?.key).toContain("event:a");
  expect(during.next?.key).toContain("event:b");
  const after = countdown(items, Date.parse("2026-10-09T06:00:00Z"));
  expect(after).toEqual({});
});

test("durations read naturally", () => {
  expect(duration(98 * 60_000)).toBe("1시간 38분");
  expect(duration(5 * 60_000)).toBe("5분");
  expect(duration(2 * HOUR)).toBe("2시간");
  expect(duration(2 * DAY + 19 * HOUR + 5 * 60_000)).toBe("2일 19시간");
});

test("month grid starts on Monday and fills whole weeks", () => {
  const grid = monthGrid(FRI);
  expect(grid).toHaveLength(35);
  expect(new Date(grid[0]).toISOString()).toBe("2026-09-27T15:00:00.000Z");
  expect(new Date(grid[34]).toISOString()).toBe("2026-10-31T15:00:00.000Z");
});

test("items close together in one channel stack on separate lanes", () => {
  const items = scheduleItems(
    [
      event({ id: "a", starts_at: "2026-10-12T01:00:00Z" }),
      event({ id: "b", starts_at: "2026-10-12T05:00:00Z" }),
      event({ id: "c", starts_at: "2026-10-14T05:00:00Z" }),
    ],
    [],
  );
  const [row] = weekRows(items, FRI, 7);
  expect(row.items.map((i) => i.lane)).toEqual([0, 1, 0]);
  expect(row.lanes).toBe(2);
});
