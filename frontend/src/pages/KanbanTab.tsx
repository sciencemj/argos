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
import {
  createContext,
  type FormEvent,
  useContext,
  useMemo,
  useRef,
  useState,
} from "react";
import { useSearchParams } from "react-router";
import {
  STATUSES,
  type Task,
  type TaskStatus,
  useChannels,
  useConfig,
  useCreateTask,
  useMoveTask,
  useReorderTasks,
  useTasks,
  useUpdateTask,
} from "../api";
import { channelLabel } from "../cards";
import { dday, localInputToIso } from "../dates";
import { tr, tt } from "../i18n";
import { CheckIcon, PlusIcon } from "../icons";
import { JobLine } from "../jobs";
import {
  anchorFor,
  applyColumns,
  type Columns,
  columnOf,
  type SortDirection,
  sortTasks,
  type TaskSort,
  type TaskSortBy,
  toColumns,
} from "../kanban";
import { toast } from "../toast";
import {
  btn,
  Chip,
  DdayBadge,
  Dialog,
  ErrorText,
  field,
  label,
  SelectMenu,
} from "../ui";
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

/** On the combined board, the channel a card belongs to ("# 이름"). */
const ChannelOf = createContext<(task: Task) => string | undefined>(
  () => undefined,
);

const DOT: Record<TaskStatus, string> = {
  backlog: "bg-step-1",
  todo: "bg-step-2",
  in_progress: "bg-step-4",
  review: "bg-step-3",
  done: "bg-step-5",
};

const SORT_KEY = "argos.kanban.sort";

const SORTS: { id: TaskSortBy; label: () => string }[] = [
  { id: "manual", label: () => tr("수동") },
  { id: "due_at", label: () => tr("마감일") },
  { id: "priority", label: () => tr("우선순위") },
];

function savedSort(): TaskSort {
  try {
    const saved = JSON.parse(
      localStorage.getItem(SORT_KEY) ?? "null",
    ) as TaskSort | null;
    if (
      saved &&
      ["manual", "due_at", "priority"].includes(saved.by) &&
      ["asc", "desc"].includes(saved.direction)
    ) {
      return saved;
    }
  } catch {
    // private window or blocked storage: use the default
  }
  return { by: "manual", direction: "asc" };
}

function SortBar({
  sort,
  onSelect,
  onFlip,
}: {
  sort: TaskSort;
  onSelect: (by: TaskSortBy) => void;
  onFlip: () => void;
}) {
  const dirLabel =
    sort.by === "due_at"
      ? sort.direction === "asc"
        ? tr("임박순")
        : tr("늦은순")
      : sort.direction === "desc"
        ? tr("높은순")
        : tr("낮은순");

  return (
    <div className="flex items-center gap-1">
      <fieldset className="m-0 flex min-w-0 gap-0.5 rounded-full border-0 bg-inset p-[2px] text-[12px]">
        <legend className="sr-only">{tr("정렬 기준")}</legend>
        {SORTS.map((s) => (
          <button
            key={s.id}
            type="button"
            aria-pressed={sort.by === s.id}
            onClick={() => onSelect(s.id)}
            className="h-6 cursor-pointer rounded-full px-2.5 text-text-3 aria-pressed:bg-card aria-pressed:font-medium aria-pressed:text-ink aria-pressed:shadow-sm"
          >
            {s.label()}
          </button>
        ))}
      </fieldset>
      {sort.by !== "manual" && (
        <button
          type="button"
          onClick={onFlip}
          aria-label={tt`정렬 방향 바꾸기 (지금: ${dirLabel})`}
          title={dirLabel}
          className="flex h-6 cursor-pointer items-center gap-1 rounded-full px-2 text-[12px] text-text-3 hover:bg-inset hover:text-ink"
        >
          <span aria-hidden="true">{sort.direction === "asc" ? "↑" : "↓"}</span>
          {dirLabel}
        </button>
      )}
    </div>
  );
}

export function KanbanTab() {
  const channel = useChannel();
  // 내 공간 manages every task in one board; each card names its channel.
  const all = channel.kind === "personal";
  const tasks = useTasks(all ? undefined : channel.id);
  const channels = useChannels();
  const config = useConfig();
  const move = useMoveTask(all ? undefined : channel.id);
  const reorder = useReorderTasks(all ? undefined : channel.id);
  const [sort, setSort] = useState<TaskSort>(savedSort);
  const rememberSort = (next: TaskSort) => {
    setSort(next);
    try {
      localStorage.setItem(SORT_KEY, JSON.stringify(next));
    } catch {
      // blocked storage: preference still applies for this session
    }
  };
  const applySort = (next: TaskSort) => {
    rememberSort(next);
    if (!tasks.data) return;
    reorder.reorder({
      by: next.by,
      direction: next.direction,
      optimistic: sortTasks(tasks.data, next.by, next.direction),
    });
  };
  const pickSort = (by: TaskSortBy) => {
    const direction: SortDirection =
      by === "manual"
        ? "asc"
        : sort.by === by
          ? sort.direction
          : by === "priority"
            ? "desc"
            : "asc";
    applySort({ by, direction });
  };
  const flipSort = () => {
    if (sort.by === "manual") return;
    const direction: SortDirection = sort.direction === "asc" ? "desc" : "asc";
    applySort({ by: sort.by, direction });
  };
  const channelOf = (task: Task) => {
    if (!all) return undefined;
    const c = channels.data?.channels.find((c) => c.id === task.channel_id);
    return c ? channelLabel(c) : undefined;
  };
  const undo = useUpdateTask();
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
    const before = byId.get(id);
    move.move({
      id,
      status,
      ...anchorFor(ids, id),
      optimistic: applyColumns(tasks.data, final),
    });
    if (sort.by !== "manual") {
      rememberSort({ by: "manual", direction: "asc" });
    }
    if (status === "done" && before && before.status !== "done") {
      toast(tr("완료했어요"), {
        detail: before.title,
        undo: () => undo.mutate({ id, status: before.status }),
      });
    }
  };

  const openTask = (id: string) => {
    const next = new URLSearchParams(params);
    next.set("task", id);
    setParams(next);
  };

  const active = activeId ? byId.get(activeId) : undefined;

  return (
    <>
      <div className="flex flex-wrap items-center gap-2 px-8 max-md:px-4 pb-3.5">
        <SortBar sort={sort} onSelect={pickSort} onFlip={flipSort} />
        <span className="grow" />
        <span className="mr-2.5 text-[12px] text-meta max-sm:hidden">
          {tr("드래그로 이동 · 순서는 새로고침 후에도 유지")}
        </span>
        <button
          type="button"
          className={btn.cta}
          onClick={() => setCreating(true)}
        >
          <PlusIcon />
          {tr("할 일")}
        </button>
      </div>
      <ErrorText error={move.error ?? reorder.error} />
      <ChannelOf.Provider value={channelOf}>
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
              draggable: tr(
                "스페이스로 카드를 들고, 방향키로 옮긴 뒤 스페이스로 내려놓아요. Esc는 취소.",
              ),
            },
          }}
        >
          <div className="grid min-h-0 grow grid-cols-[repeat(5,minmax(184px,1fr))] gap-3.5 overflow-x-auto px-8 max-md:px-4 pb-[22px]">
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
      </ChannelOf.Provider>
      <NewTaskDialog
        channelId={channel.id}
        chooseChannel={all}
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
            ? tr("WIP 한도를 넘었어요. 하나 끝내고 시작하는 게 어때요?")
            : tr("WIP 한도에 닿았어요. 하나 끝내고 시작하는 게 어때요?")}
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
        <CardBody task={task} onOpen={onOpen} />
      )}
    </div>
  );
}

function CardBody({
  task,
  onOpen,
}: {
  task: Task;
  onOpen?: (id: string) => void;
}) {
  const channel = useContext(ChannelOf)(task);
  if (task.status === "done") {
    return (
      <article className="flex cursor-pointer items-center gap-2 rounded-2xl border border-line px-3.5 py-3 text-meta">
        <CheckIcon size={14} />
        <span className="flex min-w-0 flex-col">
          <span className="text-[13px] line-through">{task.title}</span>
          {channel && <span className="truncate text-[11.5px]">{channel}</span>}
        </span>
      </article>
    );
  }
  return (
    <article
      className={`flex cursor-grab flex-col gap-2.5 rounded-2xl border bg-card p-3.5 shadow-sm ${task.job?.status === "error" ? "border-danger-line" : "border-line-soft"}`}
    >
      <div className="flex flex-col gap-0.5">
        <div className="text-[13.5px] font-medium text-ink">{task.title}</div>
        {channel && (
          <div className="truncate text-[11.5px] text-meta">{channel}</div>
        )}
      </div>
      {task.job && <JobLine task={task} onOpen={onOpen ?? (() => {})} />}
      <div className="flex flex-wrap items-center gap-1.5">
        {task.due_at ? (
          <DdayBadge days={dday(task.due_at)} />
        ) : (
          <span className="rounded-full border border-line-soft px-[9px] py-px text-[11.5px] text-text-3">
            {tr("날짜 없음")}
          </span>
        )}
        {task.priority !== null && task.priority >= 2 && (
          <Chip>{task.priority === 3 ? tr("긴급") : tr("높음")}</Chip>
        )}
      </div>
    </article>
  );
}

function NewTaskDialog({
  channelId,
  chooseChannel = false,
  open,
  onClose,
}: {
  channelId: string;
  /** Combined board: the card can go to any channel. */
  chooseChannel?: boolean;
  open: boolean;
  onClose: () => void;
}) {
  const create = useCreateTask();
  const channels = useChannels();
  const [target, setTarget] = useState(channelId);
  const [title, setTitle] = useState("");
  const [due, setDue] = useState("");
  const [status, setStatus] = useState<TaskStatus>("todo");

  const submit = (e: FormEvent) => {
    e.preventDefault();
    create.mutate(
      {
        channel_id: target,
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
    <Dialog open={open} onClose={onClose} title={tr("할 일 추가")}>
      <form onSubmit={submit} className="flex flex-col gap-4">
        <label className="flex flex-col gap-1">
          <span className={label}>{tr("제목")}</span>
          <input
            className={field}
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder={tr("예: 과제2 제출")}
            required
          />
        </label>
        {chooseChannel && (
          <label className="flex flex-col gap-1">
            <span className={label}>{tr("채널")}</span>
            <SelectMenu
              className={field}
              value={target}
              onChange={(value) => setTarget(value)}
              options={[
                ...(channels.data?.channels ?? [])
                  .filter((c) => c.kind !== "system" && c.kind !== "dm")
                  .map((c) => ({ value: c.id, label: channelLabel(c) })),
              ]}
            />
          </label>
        )}
        <label className="flex flex-col gap-1">
          <span className={label}>{tr("마감 (선택)")}</span>
          <input
            type="datetime-local"
            className={field}
            value={due}
            onChange={(e) => setDue(e.target.value)}
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className={label}>{tr("상태")}</span>
          <SelectMenu
            className={field}
            value={status}
            onChange={(value) => setStatus(value)}
            options={[
              ...STATUSES.map((s) => ({ value: s.id, label: s.label })),
            ]}
          />
        </label>
        <ErrorText error={create.error} />
        <div className="flex justify-end gap-2">
          <button type="button" className={btn.ghost} onClick={onClose}>
            {tr("취소")}
          </button>
          <button type="submit" className={btn.cta} disabled={create.isPending}>
            {tr("추가하기")}
          </button>
        </div>
      </form>
    </Dialog>
  );
}
