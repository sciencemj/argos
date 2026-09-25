import { useEffect, useState } from "react";
import { AgentAvatar } from "./agents";
import { cancelRun, type Job, type Task, useConfig, useStartJob } from "./api";
import { useOpenThread } from "./feed";
import { btn, ErrorText, field, label } from "./ui";

/** Coding agents that can take a job (PLAN Phase 6). */
export const JOB_AGENTS = [
  { name: "codex", label: "Codex" },
  { name: "claude", label: "Claude" },
];

const active = (job: Job) =>
  job.status === "queued" || job.status === "running";

/** Ticks once a second while `on`, for the running job's elapsed time. */
function useNow(on: boolean) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!on) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [on]);
  return now;
}

function clock(ms: number) {
  const s = Math.max(0, Math.floor(ms / 1000));
  const mm = String(Math.floor(s / 60)).padStart(2, "0");
  return `${mm}:${String(s % 60).padStart(2, "0")}`;
}

function took(job: Job) {
  if (!job.finished_at) return "";
  const minutes = Math.round(
    (Date.parse(job.finished_at) - Date.parse(job.started_at)) / 60000,
  );
  return minutes >= 1 ? ` · ${minutes}분` : " · 1분 안에";
}

export function jobText(job: Job): string {
  switch (job.status) {
    case "queued":
      return "잡 대기 중";
    case "running":
      return "잡 실행 중";
    case "done":
      return `잡 완료${took(job)}`;
    case "cancelled":
      return "잡 중단됨";
    default:
      return `잡 실패${job.error ? ` · ${job.error}` : ""}`;
  }
}

// Buttons inside a draggable card must not start a drag or open the card.
const stop = {
  onPointerDown: (e: React.PointerEvent) => e.stopPropagation(),
  onKeyDown: (e: React.KeyboardEvent) => e.stopPropagation(),
};

/** Kanban card footer (Kanban design): agent, state, elapsed, result or retry. */
export function JobLine({
  task,
  onOpen,
}: {
  task: Task;
  onOpen: (id: string) => void;
}) {
  const job = task.job;
  const now = useNow(job?.status === "running");
  const openThread = useOpenThread();
  const start = useStartJob();
  if (!job) return null;
  const failed = job.status === "error";
  return (
    <>
      <div className="flex items-center gap-[7px] text-[11.5px]">
        <AgentAvatar id={job.agent} size={18} />
        <span
          className={`min-w-0 grow truncate ${failed ? "text-danger" : "text-text-2"}`}
          title={jobText(job)}
        >
          {jobText(job)}
        </span>
        {job.status === "running" && (
          <span className="font-mono text-meta">
            {clock(now - Date.parse(job.started_at))}
          </span>
        )}
      </div>
      {job.status === "running" && (
        <div className="h-[3px] overflow-hidden rounded-sm bg-line">
          <div className="h-full w-1/3 animate-[job-slide_1.6s_ease-in-out_infinite] rounded-sm bg-ink" />
        </div>
      )}
      {job.status === "done" && job.summary && (
        <div className="line-clamp-3 text-[12px] leading-[1.55] text-text-3">
          {job.summary}
        </div>
      )}
      {!active(job) && (
        <div className="flex gap-1.5">
          {job.status === "done" && job.trigger_message_id ? (
            <button
              type="button"
              {...stop}
              className="h-[30px] cursor-pointer rounded-full border border-line bg-card px-3 text-[12px] font-medium text-ink"
              onClick={(e) => {
                e.stopPropagation();
                if (job.trigger_message_id) openThread(job.trigger_message_id);
              }}
            >
              결과 보기
            </button>
          ) : (
            <button
              type="button"
              {...stop}
              disabled={start.isPending || !job.instructions}
              className="h-[30px] cursor-pointer rounded-full border border-line bg-card px-3 text-[12px] font-medium text-ink disabled:opacity-50"
              onClick={(e) => {
                e.stopPropagation();
                start.mutate({
                  id: task.id,
                  agent: job.agent,
                  instructions: job.instructions ?? "",
                  directory: job.workspace,
                });
              }}
            >
              다시 실행
            </button>
          )}
          {job.status !== "done" && (
            <button
              type="button"
              {...stop}
              className="h-[30px] cursor-pointer rounded-full px-2.5 text-[12px] text-text-3 hover:text-ink"
              onClick={(e) => {
                e.stopPropagation();
                onOpen(task.id);
              }}
            >
              로그
            </button>
          )}
        </div>
      )}
    </>
  );
}

/** TaskPanel section: start a job on this card, or follow and retry the latest one. */
export function JobSection({ task }: { task: Task }) {
  const job = task.job;
  const config = useConfig();
  const start = useStartJob();
  const openThread = useOpenThread();
  const now = useNow(job?.status === "running");
  const [agent, setAgent] = useState(job?.agent ?? "codex");
  const [instructions, setInstructions] = useState(
    job?.instructions ?? task.description ?? task.title,
  );
  const [directory, setDirectory] = useState(job?.workspace ?? "");
  const roots = config.data?.job_roots ?? [];

  return (
    <section aria-label="코딩 잡" className="flex flex-col gap-3">
      <div className={label}>코딩 잡</div>
      {job && (
        <div className="flex flex-col gap-2 rounded-xl bg-page p-3.5">
          <div className="flex items-center gap-2 text-[13px]">
            <AgentAvatar id={job.agent} size={22} />
            <span
              className={`grow ${job.status === "error" ? "text-danger" : "text-text"}`}
            >
              {jobText(job)}
            </span>
            {job.status === "running" && (
              <span className="font-mono text-[12px] text-meta">
                {clock(now - Date.parse(job.started_at))}
              </span>
            )}
            {active(job) && (
              <button
                type="button"
                className="h-[26px] cursor-pointer rounded-full border border-line bg-card px-2.5 text-[11.5px] text-text-2 hover:text-ink"
                onClick={() => void cancelRun(job.run_id)}
              >
                중단
              </button>
            )}
          </div>
          {job.workspace && (
            <div className="font-mono text-[11.5px] break-all text-text-3">
              {job.workspace}
            </div>
          )}
          {job.trigger_message_id && (
            <button
              type="button"
              className="cursor-pointer self-start text-[12.5px] font-medium text-text-2 underline underline-offset-[3px]"
              onClick={() =>
                job.trigger_message_id && openThread(job.trigger_message_id)
              }
            >
              스레드에서 보기
            </button>
          )}
          {job.log && (
            <details>
              <summary className="cursor-pointer text-[12px] text-text-3">
                로그 {job.log.split("\n").length}줄
              </summary>
              <pre className="mt-2 max-h-60 overflow-auto rounded-lg bg-inset p-2.5 font-mono text-[11px] leading-[1.6] whitespace-pre-wrap text-text-2">
                {job.log}
              </pre>
            </details>
          )}
        </div>
      )}
      {!(job && active(job)) && (
        <form
          className="flex flex-col gap-3 rounded-xl bg-page p-3.5"
          onSubmit={(e) => {
            e.preventDefault();
            start.mutate({
              id: task.id,
              agent,
              instructions,
              directory: directory.trim() || null,
            });
          }}
        >
          <label className="flex flex-col gap-1">
            <span className={label}>에이전트</span>
            <select
              className={field}
              value={agent}
              onChange={(e) => setAgent(e.target.value)}
            >
              {JOB_AGENTS.map((a) => (
                <option key={a.name} value={a.name}>
                  {a.label}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className={label}>맡길 일</span>
            <textarea
              rows={3}
              required
              className={`${field} h-auto resize-y py-2`}
              value={instructions}
              onChange={(e) => setInstructions(e.target.value)}
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className={label}>작업 디렉터리 (비우면 새 폴더)</span>
            <input
              className={`${field} font-mono text-[12.5px]`}
              value={directory}
              placeholder={roots[0] ? `${roots[0]}/…` : ""}
              onChange={(e) => setDirectory(e.target.value)}
            />
            {roots.length > 0 && (
              <span className="text-[11.5px] text-meta">
                허용: {roots.join(", ")}
              </span>
            )}
          </label>
          <ErrorText error={start.error} />
          <div>
            <button
              type="submit"
              className={btn.cta}
              disabled={start.isPending || !instructions.trim()}
            >
              {job ? "다시 실행" : "잡 시작"}
            </button>
          </div>
        </form>
      )}
    </section>
  );
}
