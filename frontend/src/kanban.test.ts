import { describe, expect, test } from "vitest";
import type { Task } from "./api";
import { anchorFor, applyColumns, columnOf, toColumns } from "./kanban";

const task = (id: string, status: Task["status"], position: number): Task => ({
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
});
