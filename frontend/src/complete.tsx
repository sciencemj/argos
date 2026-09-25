import { useState } from "react";
import { type Task, useUpdateTask } from "./api";
import { toast } from "./toast";

const SETTLE_MS = 600; // long enough to see the check and the strike-through

/** Marks tasks done with a moment of feedback (backlog: "완료 버튼 피드백"): the box
 * fills, the title is struck through, then the task moves on, and a toast offers undo. */
export function useCompleteTask() {
  const update = useUpdateTask();
  const [pending, setPending] = useState<Set<string>>(new Set());

  const complete = (task: Pick<Task, "id" | "title" | "status">) => {
    if (pending.has(task.id)) return;
    const before = task.status;
    setPending((s) => new Set(s).add(task.id));
    setTimeout(() => {
      update.mutate(
        { id: task.id, status: "done" },
        {
          onSettled: () =>
            setPending((s) => {
              const next = new Set(s);
              next.delete(task.id);
              return next;
            }),
          onSuccess: () =>
            toast(`완료했어요 · ${task.title}`, {
              label: "되돌리기",
              run: () => update.mutate({ id: task.id, status: before }),
            }),
        },
      );
    }, SETTLE_MS);
  };

  return { pending, complete, error: update.error };
}

/** Round check box: fills and draws its tick when `checked`. */
export function CheckButton({
  checked,
  label,
  onClick,
  size = 32,
}: {
  checked: boolean;
  label: string;
  onClick: () => void;
  size?: number;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      aria-pressed={checked}
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
      className={`check-box flex shrink-0 cursor-pointer items-center justify-center rounded-full border transition-colors ${checked ? "border-step-5 bg-step-5 text-on-dark" : "border-line bg-card text-text-3 hover:border-step-4 hover:text-ink"}`}
      style={{ width: size, height: size }}
    >
      <svg
        width={size * 0.5}
        height={size * 0.5}
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth={2.4}
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        <path
          d="m5 12 5 5 9-10"
          className={checked ? "check-draw" : "opacity-40"}
        />
      </svg>
    </button>
  );
}
