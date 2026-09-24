import {
  type CollisionDetection,
  closestCorners,
  DndContext,
  type DragEndEvent,
  type DragOverEvent,
  DragOverlay,
  type DragStartEvent,
  type DropAnimation,
  defaultDropAnimationSideEffects,
  KeyboardSensor,
  PointerSensor,
  pointerWithin,
  useDroppable,
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
import { type FormEvent, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router";
import {
  STATUSES,
  type Task,
  type TaskStatus,
  useConfig,
  useCreateTask,
  useMoveTask,
  useTasks,
} from "../api";
import { dday, localInputToIso } from "../dates";
import { CheckIcon, PlusIcon } from "../icons";
import {
  anchorFor,
  applyColumns,
  type Columns,
  columnOf,
  toColumns,
} from "../kanban";
import { btn, Chip, DdayBadge, Dialog, ErrorText, field, label } from "../ui";
import { useChannel } from "./ChannelPage";

/** What the pointer is actually over wins; columns are tall, so corner distance alone
 * keeps picking the card's old slot. Keyboard drags have no pointer and fall back. */
const collision: CollisionDetection = (args) => {
  const hits = pointerWithin(args);
  return hits.length > 0 ? hits : closestCorners(args);
};

/** The lifted card is tilted -2deg (Kanban design); the drop animation straightens it
 * while it settles into its slot instead of snapping at the end. */
const dropAnimation: DropAnimation = {
  duration: 200,
  easing: "cubic-bezier(0.2, 0, 0, 1)",
  keyframes: ({ transform }) => [
    { transform: CSS.Transform.toString(transform.initial) },
    { transform: `${CSS.Transform.toString(transform.final)} rotate(2deg)` },
  ],
  sideEffects: defaultDropAnimationSideEffects({
    styles: { active: { opacity: "0" } },
  }),
};

const DOT: Record<TaskStatus, string> = {
  backlog: "bg-step-1",
  todo: "bg-step-2",
  in_progress: "bg-step-4",
  review: "bg-step-3",
  done: "bg-step-5",
};

export function KanbanTab() {
  const channel = useChannel();
  const tasks = useTasks(channel.id);
  const config = useConfig();
  const move = useMoveTask(channel.id);
  const [params, setParams] = useSearchParams();
  const [creating, setCreating] = useState(false);
  // While dragging, the board renders this local copy; otherwise the server order.
  // The ref mirrors it so drag handlers never read a stale render's value.
  const [dragColumns, setDragColumnsState] = useState<Columns | null>(null);
  const dragRef = useRef<Columns | null>(null);
  const setDragColumns = (next: Columns | null) => {
    dragRef.current = next;
    setDragColumnsState(next);
  };
  const [activeId, setActiveId] = useState<string | null>(null);

  const serverColumns = useMemo(
    () => toColumns(tasks.data ?? []),
    [tasks.data],
  );
  const columns = dragColumns ?? serverColumns;
  const byId = useMemo(
    () => new Map(tasks.data?.map((t) => [t.id, t])),
    [tasks.data],
  );
  const wipLimit = config.data?.wip_limit ?? 3;

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    }),
  );

  const containerOf = (id: string, cols: Columns): TaskStatus | undefined =>
    STATUSES.some((s) => s.id === id) ? (id as TaskStatus) : columnOf(cols, id);

  const onDragStart = ({ active }: DragStartEvent) => {
    setActiveId(String(active.id));
    setDragColumns(serverColumns);
  };

  // Moving across columns happens live so the drop placeholder shows where it lands.
  const onDragOver = ({ active, over }: DragOverEvent) => {
    const cols = dragRef.current;
    if (!over || !cols) return;
    const from = containerOf(String(active.id), cols);
    const to = containerOf(String(over.id), cols);
    if (!from || !to || from === to) return;
    const target = cols[to];
    const overIndex = target.indexOf(String(over.id));
    const index = overIndex >= 0 ? overIndex : target.length;
    setDragColumns({
      ...cols,
      [from]: cols[from].filter((id) => id !== active.id),
      [to]: [
        ...target.slice(0, index),
        String(active.id),
        ...target.slice(index),
      ],
    });
  };

  const onDragEnd = ({ active, over }: DragEndEvent) => {
    const cols = dragRef.current;
    setActiveId(null);
    setDragColumns(null);
    if (!over || !cols || !tasks.data) return;
    const id = String(active.id);
    const status = containerOf(id, cols);
    if (!status) return;
    let ids = cols[status];
    const overIndex = ids.indexOf(String(over.id));
    if (overIndex >= 0) ids = arrayMove(ids, ids.indexOf(id), overIndex);
    const final = { ...cols, [status]: ids };

    const unchanged =
      byId.get(id)?.status === status &&
      serverColumns[status].join() === ids.join();
    if (unchanged) return;
    move.move({
      id,
      status,
      ...anchorFor(ids, id),
      optimistic: applyColumns(tasks.data, final),
    });
  };

  const openTask = (id: string) => {
    const next = new URLSearchParams(params);
    next.set("task", id);
    setParams(next);
  };

  const active = activeId ? byId.get(activeId) : undefined;

  return (
    <>
      <div className="flex items-center gap-1 px-8 pb-3.5">
        <span className="grow" />
        <span className="mr-2.5 text-[12px] text-meta">
          드래그로 이동 · 순서는 새로고침 후에도 유지
        </span>
        <button
          type="button"
          className={btn.cta}
          onClick={() => setCreating(true)}
        >
          <PlusIcon />할 일
        </button>
      </div>
      <ErrorText error={move.error} />
      <DndContext
        sensors={sensors}
        collisionDetection={collision}
        onDragStart={onDragStart}
        onDragOver={onDragOver}
        onDragEnd={onDragEnd}
        onDragCancel={() => {
          setActiveId(null);
          setDragColumns(null);
        }}
        accessibility={{
          screenReaderInstructions: {
            draggable:
              "스페이스로 카드를 들고, 방향키로 옮긴 뒤 스페이스로 내려놓아요. Esc는 취소.",
          },
        }}
      >
        <div className="grid min-h-0 grow grid-cols-[repeat(5,minmax(184px,1fr))] gap-3.5 overflow-x-auto px-8 pb-[22px]">
          {STATUSES.map((s) => (
            <Column
              key={s.id}
              status={s.id}
              title={s.label}
              ids={columns[s.id]}
              byId={byId}
              wipLimit={s.id === "in_progress" ? wipLimit : undefined}
              onOpen={openTask}
            />
          ))}
        </div>
        <DragOverlay dropAnimation={dropAnimation}>
          {active && (
            <div className="rotate-[-2deg] rounded-2xl shadow-lift">
              <CardBody task={active} />
            </div>
          )}
        </DragOverlay>
      </DndContext>
      <NewTaskDialog
        channelId={channel.id}
        open={creating}
        onClose={() => setCreating(false)}
      />
    </>
  );
}

function Column({
  status,
  title,
  ids,
  byId,
  wipLimit,
  onOpen,
}: {
  status: TaskStatus;
  title: string;
  ids: string[];
  byId: Map<string, Task>;
  wipLimit?: number;
  onOpen: (id: string) => void;
}) {
  const { setNodeRef } = useDroppable({ id: status });
  const overLimit = wipLimit !== undefined && ids.length >= wipLimit;

  return (
    <section
      aria-label={title}
      ref={setNodeRef}
      className="flex min-h-0 flex-col gap-2.5 overflow-y-auto rounded-3xl bg-inset p-3.5"
    >
      <div className="flex items-center gap-2 px-1.5 pt-0.5 pb-1">
        <span className={`size-[7px] rounded-full ${DOT[status]}`} />
        <span className="grow text-[14px] font-medium whitespace-nowrap text-ink">
          {title}
        </span>
        {wipLimit !== undefined ? (
          <span
            className={`rounded-full px-[9px] py-0.5 font-mono text-[11.5px] whitespace-nowrap ${overLimit ? "bg-danger-bg font-medium text-danger" : "text-meta"}`}
          >
            {ids.length} / {wipLimit}
          </span>
        ) : (
          <span className="font-mono text-[11.5px] text-meta">
            {ids.length}
          </span>
        )}
      </div>
      {overLimit && (
        <div className="rounded-xl bg-danger-bg px-3 py-2 text-[12px] text-danger">
          {ids.length > (wipLimit ?? 0)
            ? "WIP 한도를 넘었어요. 하나 끝내고 시작하는 게 어때요?"
            : "WIP 한도에 닿았어요. 하나 끝내고 시작하는 게 어때요?"}
        </div>
      )}
      <SortableContext items={ids} strategy={verticalListSortingStrategy}>
        {ids.map((id) => {
          const task = byId.get(id);
          return task ? (
            <SortableCard key={id} task={task} onOpen={onOpen} />
          ) : null;
        })}
      </SortableContext>
    </section>
  );
}

function SortableCard({
  task,
  onOpen,
}: {
  task: Task;
  onOpen: (id: string) => void;
}) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({
    id: task.id,
  });
  // dnd-kit's `attributes` supply role="button" and tabIndex; Biome cannot see through the spread.
  return (
    // biome-ignore lint/a11y/noStaticElementInteractions: interactive via dnd-kit attributes
    // biome-ignore lint/a11y/useAriaPropsSupportedByRole: role="button" comes from attributes
    <div
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      {...attributes}
      {...listeners}
      aria-label={task.title}
      onClick={() => onOpen(task.id)}
      onKeyDown={(e) => {
        listeners?.onKeyDown?.(e);
        if (e.key === "Enter") onOpen(task.id);
      }}
    >
      {isDragging ? (
        // Same size as the card it stands in for, so nothing shifts on drop.
        <div className="rounded-2xl outline-[1.5px] outline-line outline-dashed">
          <div className="invisible">
            <CardBody task={task} />
          </div>
        </div>
      ) : (
        <CardBody task={task} />
      )}
    </div>
  );
}

function CardBody({ task }: { task: Task }) {
  if (task.status === "done") {
    return (
      <article className="flex cursor-pointer items-center gap-2 rounded-2xl border border-line px-3.5 py-3 text-meta">
        <CheckIcon size={14} />
        <span className="text-[13px] line-through">{task.title}</span>
      </article>
    );
  }
  return (
    <article className="flex cursor-grab flex-col gap-2.5 rounded-2xl border border-line-soft bg-card p-3.5 shadow-sm">
      <div className="text-[13.5px] font-medium text-ink">{task.title}</div>
      <div className="flex flex-wrap items-center gap-1.5">
        {task.due_at ? (
          <DdayBadge days={dday(task.due_at)} />
        ) : (
          <span className="rounded-full border border-line-soft px-[9px] py-px text-[11.5px] text-text-3">
            날짜 없음
          </span>
        )}
        {task.priority !== null && task.priority >= 2 && (
          <Chip>{task.priority === 3 ? "긴급" : "높음"}</Chip>
        )}
      </div>
    </article>
  );
}

function NewTaskDialog({
  channelId,
  open,
  onClose,
}: {
  channelId: string;
  open: boolean;
  onClose: () => void;
}) {
  const create = useCreateTask();
  const [title, setTitle] = useState("");
  const [due, setDue] = useState("");
  const [status, setStatus] = useState<TaskStatus>("todo");

  const submit = (e: FormEvent) => {
    e.preventDefault();
    create.mutate(
      {
        channel_id: channelId,
        title: title.trim(),
        status,
        due_at: due ? localInputToIso(due) : null,
      },
      {
        onSuccess: () => {
          setTitle("");
          setDue("");
          onClose();
        },
      },
    );
  };

  return (
    <Dialog open={open} onClose={onClose} title="할 일 추가">
      <form onSubmit={submit} className="flex flex-col gap-4">
        <label className="flex flex-col gap-1">
          <span className={label}>제목</span>
          <input
            className={field}
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="예: 과제2 제출"
            required
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className={label}>마감 (선택)</span>
          <input
            type="datetime-local"
            className={field}
            value={due}
            onChange={(e) => setDue(e.target.value)}
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className={label}>상태</span>
          <select
            className={field}
            value={status}
            onChange={(e) => setStatus(e.target.value as TaskStatus)}
          >
            {STATUSES.map((s) => (
              <option key={s.id} value={s.id}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
        <ErrorText error={create.error} />
        <div className="flex justify-end gap-2">
          <button type="button" className={btn.ghost} onClick={onClose}>
            취소
          </button>
          <button type="submit" className={btn.cta} disabled={create.isPending}>
            추가하기
          </button>
        </div>
      </form>
    </Dialog>
  );
}
