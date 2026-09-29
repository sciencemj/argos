import { useState } from "react";
import { useSearchParams } from "react-router";
import {
  type Activity,
  STATUSES,
  type Task,
  useActivity,
  useChannels,
  useDeleteTask,
  useTask,
  useUpdateTask,
} from "../api";
import { channelLabel } from "../cards";
import { dday, fmt, isoToLocalInput, localInputToIso } from "../dates";
import { tr, tt } from "../i18n";
import { CloseIcon } from "../icons";
import { JobSection } from "../jobs";
import { btn, Chip, DdayBadge, ErrorText, field, label } from "../ui";

export const PRIORITIES = [
  { value: 0, label: tr("낮음") },
  { value: 1, label: tr("보통") },
  { value: 2, label: tr("높음") },
  { value: 3, label: tr("긴급") },
];

const statusLabel = new Map(STATUSES.map((s) => [s.id as string, s.label]));

/** Right panel for ?task=<id>: card detail plus its activity trail ("발자국"). */
export function TaskPanel({ taskId }: { taskId: string }) {
  const [params, setParams] = useSearchParams();
  const task = useTask(taskId);

  const close = () => {
    const next = new URLSearchParams(params);
    next.delete("task");
    setParams(next);
  };

  return (
    <aside
      aria-label={tr("카드 상세")}
      className="flex min-h-0 flex-col border-l border-line-soft bg-sidebar"
    >
      <div className="flex h-16 items-center gap-2.5 px-5">
        <span className="grow text-[12.5px] text-meta">{tr("할 일")}</span>
        <button
          type="button"
          aria-label={tr("닫기")}
          className={btn.icon}
          onClick={close}
        >
          <CloseIcon />
        </button>
      </div>
      {task.isError && (
        <p className="px-5 text-[13px] text-text-3">
          {tr("이 할 일은 삭제되었거나 찾을 수 없어요.")}
        </p>
      )}
      {task.data && (
        <TaskDetail key={task.data.id} task={task.data} onDeleted={close} />
      )}
    </aside>
  );
}

function TaskDetail({
  task,
  onDeleted,
}: {
  task: Task;
  onDeleted: () => void;
}) {
  const channels = useChannels();
  const update = useUpdateTask();
  const remove = useDeleteTask();
  const [title, setTitle] = useState(task.title);
  const [description, setDescription] = useState(task.description ?? "");
  const pickable =
    channels.data?.channels.filter(
      (c) => c.kind !== "system" && c.kind !== "dm",
    ) ?? [];
  const channel = channels.data?.channels.find((c) => c.id === task.channel_id);

  const save = (changes: Parameters<typeof update.mutate>[0]) =>
    update.mutate(changes);

  return (
    <div className="flex min-h-0 grow flex-col gap-[18px] overflow-y-auto px-5 pb-5">
      <div className="flex flex-col gap-3">
        <label className="sr-only" htmlFor="task-title">
          {tr("제목")}
        </label>
        <textarea
          id="task-title"
          rows={2}
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          onBlur={() =>
            title.trim() && title !== task.title && save({ id: task.id, title })
          }
          className="resize-none border-0 bg-transparent p-0 text-[26px] leading-tight font-light tracking-[-0.02em] text-ink outline-none"
        />
        <div className="flex flex-wrap gap-1.5">
          <span className="rounded-full border border-line bg-inset px-2 py-px text-[11.5px] font-semibold text-text">
            ● {statusLabel.get(task.status)}
          </span>
          {channel && <Chip>{channelLabel(channel)}</Chip>}
          {task.due_at && <Chip>{fmt(task.due_at, "M/d (EEE) HH:mm")}</Chip>}
          {task.due_at && task.status !== "done" && (
            <DdayBadge days={dday(task.due_at)} />
          )}
        </div>
      </div>

      <div className="flex flex-col gap-4 rounded-xl bg-page p-3.5">
        <label className="flex flex-col gap-1">
          <span className={label}>{tr("상태")}</span>
          <select
            className={field}
            value={task.status}
            onChange={(e) =>
              save({ id: task.id, status: e.target.value as Task["status"] })
            }
          >
            {STATUSES.map((s) => (
              <option key={s.id} value={s.id}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className={label}>{tr("채널")}</span>
          <select
            className={field}
            value={task.channel_id}
            onChange={(e) => save({ id: task.id, channel_id: e.target.value })}
          >
            {channel && !pickable.includes(channel) && (
              <option value={channel.id}>{channelLabel(channel)}</option>
            )}
            {pickable.map((c) => (
              <option key={c.id} value={c.id}>
                {channelLabel(c)}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className={label}>{tr("마감")}</span>
          <input
            type="datetime-local"
            className={field}
            defaultValue={task.due_at ? isoToLocalInput(task.due_at) : ""}
            onBlur={(e) => {
              const due_at = e.target.value
                ? localInputToIso(e.target.value)
                : null;
              if (due_at !== task.due_at) save({ id: task.id, due_at });
            }}
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className={label}>{tr("우선순위")}</span>
          <select
            className={field}
            value={task.priority ?? ""}
            onChange={(e) =>
              save({
                id: task.id,
                priority: e.target.value === "" ? null : Number(e.target.value),
              })
            }
          >
            <option value="">{tr("없음")}</option>
            {PRIORITIES.map((p) => (
              <option key={p.value} value={p.value}>
                {p.label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className={label}>{tr("메모")}</span>
          <textarea
            rows={3}
            className={`${field} h-auto resize-y py-2`}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            onBlur={() =>
              description !== (task.description ?? "") &&
              save({ id: task.id, description: description || null })
            }
          />
        </label>
      </div>
      <ErrorText error={update.error} />

      <JobSection task={task} />

      <Trail taskId={task.id} />

      <div className="mt-auto flex justify-end pt-2">
        <button
          type="button"
          className={btn.ghost}
          onClick={() => {
            if (confirm(tt`"${task.title}"을(를) 삭제할까요?`)) {
              remove.mutate(task.id, { onSuccess: onDeleted });
            }
          }}
        >
          {tr("삭제")}
        </button>
      </div>
    </div>
  );
}

function describe(a: Activity): string {
  const before = a.before_json ?? {};
  const after = a.after_json ?? {};
  if (a.action === "created") return tr("만들어짐");
  if ("status" in after) {
    return `${statusLabel.get(String(before.status))} → ${statusLabel.get(String(after.status))}`;
  }
  if (a.action === "moved") return tr("순서 바꿈");
  const names: Record<string, string> = {
    title: tr("제목"),
    description: tr("메모"),
    due_at: tr("마감"),
    priority: tr("우선순위"),
    channel_id: tr("채널"),
  };
  const fields = Object.keys(after)
    .filter((k) => k !== "position")
    .map((k) => names[k] ?? k);
  return fields.length ? tt`${fields.join(", ")} 수정` : tr("순서 바꿈");
}

function Trail({ taskId }: { taskId: string }) {
  const { data } = useActivity(taskId);
  if (!data?.length) return null;
  const items = [...data].reverse();
  return (
    <div className="flex flex-col">
      <div className={`${label} pb-2.5`}>{tr("발자국")}</div>
      {items.map((a, i) => (
        <div key={a.id} className="flex gap-3">
          <div className="flex flex-col items-center">
            <span className="mt-1.5 size-[7px] rounded-full border-[1.5px] border-step-4" />
            {i < items.length - 1 && <span className="w-px grow bg-line" />}
          </div>
          <div className="pb-3.5 text-[13px] text-text">
            {describe(a)}
            <div className="font-mono text-[11px] text-meta">
              {fmt(a.created_at, "M/d HH:mm")} · {a.actor}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
