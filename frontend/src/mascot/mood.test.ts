import { describe, expect, it } from "vitest";
import { countOverdue, deriveMood, INBOX_PILE } from "./mood";

const NOW = new Date("2026-11-21T12:00:00+09:00");

describe("deriveMood", () => {
  const calm = { overdue: 0, dueToday: 2, inbox: 0, doneThisWeek: 0 };

  it("is idle on an ordinary day", () => {
    expect(deriveMood(calm)).toBe("idle");
  });

  it("is sad when a deadline has passed, whatever else is going on", () => {
    expect(
      deriveMood({ overdue: 1, dueToday: 0, inbox: 99, doneThisWeek: 9 }),
    ).toBe("sad");
  });

  it("is curious when the inbox piles up", () => {
    expect(deriveMood({ ...calm, inbox: INBOX_PILE })).toBe("curious");
    expect(deriveMood({ ...calm, inbox: INBOX_PILE - 1 })).toBe("idle");
  });

  it("is happy when nothing is due today and something got done this week", () => {
    expect(deriveMood({ ...calm, dueToday: 0, doneThisWeek: 3 })).toBe("happy");
    expect(deriveMood({ ...calm, dueToday: 0, doneThisWeek: 0 })).toBe("idle");
  });
});

describe("countOverdue", () => {
  it("counts open tasks whose due time has passed", () => {
    const tasks = [
      { status: "todo", due_at: "2026-11-20T23:59:00+09:00" },
      { status: "in_progress", due_at: "2026-11-21T11:00:00+09:00" },
      { status: "done", due_at: "2026-11-19T23:59:00+09:00" },
      { status: "todo", due_at: "2026-11-21T23:59:00+09:00" },
      { status: "todo", due_at: null },
    ];
    expect(countOverdue(tasks, NOW)).toBe(2);
  });
});
