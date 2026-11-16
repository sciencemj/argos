import { STATUSES, type Task, type TaskStatus } from "./api";

export type Columns = Record<TaskStatus, string[]>;
export type TaskSortBy = "manual" | "due_at" | "priority";
export type SortDirection = "asc" | "desc";
export type TaskSort = { by: TaskSortBy; direction: SortDirection };

/** Task ids per status in server order (the API sorts by status, position). */
export function toColumns(tasks: Task[]): Columns {
  const columns = Object.fromEntries(
    STATUSES.map((s) => [s.id, [] as string[]]),
  ) as Columns;
  const sorted = [...tasks].sort((a, b) => a.position - b.position);
  for (const t of sorted) columns[t.status].push(t.id);
  return columns;
}

export function columnOf(columns: Columns, id: string): TaskStatus | undefined {
  return STATUSES.find((s) => columns[s.id].includes(id))?.id;
}

/** The single neighbour the move API needs: the card above, else the card below. */
export function anchorFor(
  ids: string[],
  movedId: string,
): { after_id?: string; before_id?: string } {
  const index = ids.indexOf(movedId);
  if (index > 0) return { after_id: ids[index - 1] };
  if (index === 0 && ids.length > 1) return { before_id: ids[1] };
  return {};
}

/** Task list as it will look once the move lands, used for the optimistic cache. */
export function applyColumns(tasks: Task[], columns: Columns): Task[] {
  const byId = new Map(tasks.map((t) => [t.id, t]));
  return STATUSES.flatMap(({ id: status }) =>
    columns[status].flatMap((taskId, i) => {
      const task = byId.get(taskId);
      return task ? [{ ...task, status, position: (i + 1) * 1024 }] : [];
    }),
  );
}

function compareCreated(a: Task, b: Task): number {
  const cmp = a.created_at.localeCompare(b.created_at);
  return cmp !== 0 ? cmp : a.id.localeCompare(b.id);
}

/** Optimistic board-wide sort matching the backend's `reorder_tasks` comparator. */
export function sortTasks(
  tasks: Task[],
  by: TaskSortBy,
  direction: SortDirection,
): Task[] {
  const columns = Object.fromEntries(
    STATUSES.map((s) => [s.id, [] as Task[]]),
  ) as Record<TaskStatus, Task[]>;
  for (const t of tasks) columns[t.status].push(t);

  const cmp = (a: Task, b: Task): number => {
    if (by === "manual") return compareCreated(a, b);
    if (by === "due_at") {
      if (a.due_at === null && b.due_at === null) return compareCreated(a, b);
      if (a.due_at === null) return 1;
      if (b.due_at === null) return -1;
      const diff = Date.parse(a.due_at) - Date.parse(b.due_at);
      if (diff !== 0) return direction === "asc" ? diff : -diff;
      return compareCreated(a, b);
    }
    if (a.priority === null && b.priority === null) return compareCreated(a, b);
    if (a.priority === null) return 1;
    if (b.priority === null) return -1;
    const diff = a.priority - b.priority;
    if (diff !== 0) return direction === "asc" ? diff : -diff;
    return compareCreated(a, b);
  };

  return STATUSES.flatMap(({ id }) =>
    columns[id].sort(cmp).map((t, i) => ({ ...t, position: (i + 1) * 1024 })),
  );
}
