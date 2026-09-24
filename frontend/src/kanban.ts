import { STATUSES, type Task, type TaskStatus } from "./api";

export type Columns = Record<TaskStatus, string[]>;

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
