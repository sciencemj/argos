import { describe, expect, test } from "vitest";
import type { Task } from "./api";
import {
  anchorFor,
  applyColumns,
  columnOf,
  sortTasks,
  toColumns,
} from "./kanban";

const task = (
  id: string,
  status: Task["status"],
  position: number,
  extra: Partial<Task> = {},
): Task => ({
  id,
  status,
  position,
  channel_id: "c",
  title: id,
  description: null,
  due_at: null,
  priority: null,
  created_at: "",
  updated_at: "",
  ...extra,
});

describe("kanban helpers", () => {
  const tasks = [
    task("b", "todo", 2048),
    task("a", "todo", 1024),
    task("x", "done", 1),
  ];

  test("groups ids per status by position", () => {
    const columns = toColumns(tasks);
    expect(columns.todo).toEqual(["a", "b"]);
    expect(columns.done).toEqual(["x"]);
    expect(columns.backlog).toEqual([]);
    expect(columnOf(columns, "x")).toBe("done");
  });

  test("anchor prefers the card above, falls back to the card below", () => {
    expect(anchorFor(["a", "m", "b"], "m")).toEqual({ after_id: "a" });
    expect(anchorFor(["m", "a"], "m")).toEqual({ before_id: "a" });
    expect(anchorFor(["m"], "m")).toEqual({});
  });

  test("optimistic list reflects the new column and order", () => {
    const columns = toColumns(tasks);
    columns.todo = ["b"];
    columns.done = ["a", "x"];
    const next = applyColumns(tasks, columns);
    expect(next.filter((t) => t.status === "done").map((t) => t.id)).toEqual([
      "a",
      "x",
    ]);
  });

  test("sortTasks orders by manual, due_at, and priority with nulls last", () => {
    const items = [
      task("none", "todo", 1024, {
        created_at: "2026-11-16T01:00:00Z",
        due_at: null,
        priority: null,
      }),
      task("late", "todo", 2048, {
        created_at: "2026-11-16T02:00:00Z",
        due_at: "2026-11-20T00:00:00Z",
        priority: 1,
      }),
      task("early", "todo", 3072, {
        created_at: "2026-11-16T03:00:00Z",
        due_at: "2026-11-17T00:00:00Z",
        priority: 3,
      }),
    ];

    expect(toColumns(sortTasks(items, "due_at", "asc")).todo).toEqual([
      "early",
      "late",
      "none",
    ]);
    expect(toColumns(sortTasks(items, "due_at", "desc")).todo).toEqual([
      "late",
      "early",
      "none",
    ]);
    expect(toColumns(sortTasks(items, "priority", "desc")).todo).toEqual([
      "early",
      "late",
      "none",
    ]);
    expect(toColumns(sortTasks(items, "priority", "asc")).todo).toEqual([
      "late",
      "early",
      "none",
    ]);
    expect(toColumns(sortTasks(items, "manual", "asc")).todo).toEqual([
      "none",
      "late",
      "early",
    ]);
  });
});
