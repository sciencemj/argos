import { useState } from "react";
import { type LiveStep, useLive } from "./agentStream";
import {
  type Message,
  type RunStep,
  usePendingApprovals,
  useResolveApproval,
  useRunSteps,
} from "./api";
import { tr } from "./i18n";
import { ShieldIcon, WrenchIcon } from "./icons";
import { btn, ErrorText } from "./ui";

/** Persisted steps (GET /runs/{id}/steps) in the live form: one entry per call, its
 * result and the approval it waited for attached. */
export function stepsFromRun(rows: RunStep[]): LiveStep[] {
  const steps: LiveStep[] = [];
  for (const row of rows) {
    if (row.kind === "tool_call") {
      steps.push({
        id: row.call_id ?? row.id,
        name: row.tool_name ?? "",
        args: row.args_json ?? {},
      });
    } else if (row.kind === "tool_result") {
      const step = steps.find((s) => s.id === row.call_id);
      if (step) {
        step.output = row.output ?? "";
        step.isError = row.is_error;
      }
    } else if (row.kind === "approval") {
      const step = [...steps]
        .reverse()
        .find((s) => s.name === row.tool_name && s.output === undefined);
      if (step) {
        step.approvalId = row.approval_id ?? undefined;
        step.summary = row.output ?? undefined;
      }
    }
  }
  return steps;
}

/** The tool calls of a native agent's answer: live while it streams, stored after. */
export function ToolSteps({ message }: { message: Message }) {
  const live = useLive(message.id);
  const run = message.run;
  const running = run?.status === "running" || run?.status === "queued";
  // Only runs that logged steps (native harness, jobs) have any to fetch.
  const stored = useRunSteps(run?.id, !running && Boolean(run?.log));
  const steps = running ? (live?.steps ?? []) : stepsFromRun(stored.data ?? []);
  if (steps.length === 0) return null;
  return (
    <section
      className="flex flex-col gap-1.5"
      aria-label={tr("도구 사용", "Tool calls")}
    >
      {steps.map((step) => (
        <ToolStepCard key={step.id} step={step} />
      ))}
    </section>
  );
}

function stateText(step: LiveStep, waiting: boolean): string {
  if (waiting) return tr("승인 대기", "Waiting for approval");
  if (step.output === undefined) return tr("실행 중…", "Running…");
  return step.isError ? tr("실패", "Failed") : tr("완료", "Done");
}

export function ToolStepCard({ step }: { step: LiveStep }) {
  const pending = usePendingApprovals();
  const resolve = useResolveApproval();
  const waiting =
    Boolean(step.approvalId) &&
    step.output === undefined &&
    (pending.data ?? []).some((a) => a.id === step.approvalId);
  // The user's own open/close wins; until they touch it, a call waiting for them opens.
  const [toggled, setToggled] = useState<boolean | null>(null);
  const open = toggled ?? waiting;
  const tone = step.isError
    ? "text-danger"
    : step.output === undefined
      ? "text-text-3"
      : "text-meta";
  return (
    <details
      open={open}
      onToggle={(e) => {
        // Only a change the user made: our own `open` updates fire toggle too.
        if (e.currentTarget.open !== open) setToggled(e.currentTarget.open);
      }}
      className={`group rounded-2xl border bg-inset/60 text-[12.5px] ${waiting ? "border-danger-line" : "border-line-soft"}`}
    >
      <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2 select-none">
        {waiting ? (
          <span className="text-danger">
            <ShieldIcon size={14} />
          </span>
        ) : (
          <span className="text-meta">
            <WrenchIcon size={14} />
          </span>
        )}
        <span className="font-mono text-[12px] text-ink">{step.name}</span>
        <span className="min-w-0 grow truncate text-meta">
          {step.summary ?? argsPreview(step.args)}
        </span>
        <span
          className={`shrink-0 text-[11.5px] ${waiting ? "text-danger" : tone}`}
        >
          {stateText(step, waiting)}
        </span>
      </summary>
      <div className="flex flex-col gap-2 px-3 pb-3">
        <pre className="m-0 max-h-48 overflow-auto rounded-lg bg-card p-2 font-mono text-[11px] leading-[1.55] whitespace-pre-wrap text-text-2 select-text">
          {JSON.stringify(step.args, null, 2)}
        </pre>
        {step.output !== undefined && (
          <pre
            className={`m-0 max-h-64 overflow-auto rounded-lg bg-card p-2 font-mono text-[11px] leading-[1.55] whitespace-pre-wrap select-text ${step.isError ? "text-danger" : "text-text-2"}`}
          >
            {step.output || tr("(출력 없음)", "(no output)")}
          </pre>
        )}
        {waiting && step.approvalId && (
          <div className="flex items-center gap-2">
            <button
              type="button"
              disabled={resolve.isPending}
              onClick={() =>
                resolve.mutate({ id: step.approvalId ?? "", approve: true })
              }
              className="inline-flex h-8 cursor-pointer items-center rounded-full bg-danger px-4 text-[12.5px] font-medium text-on-dark disabled:opacity-50"
            >
              {tr("승인", "Approve")}
            </button>
            <button
              type="button"
              disabled={resolve.isPending}
              onClick={() =>
                resolve.mutate({ id: step.approvalId ?? "", approve: false })
              }
              className={`${btn.outline} h-8`}
            >
              {tr("거절", "Reject")}
            </button>
            <ErrorText error={resolve.error} />
          </div>
        )}
      </div>
    </details>
  );
}

function argsPreview(args: Record<string, unknown>): string {
  const text = Object.entries(args)
    .map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`)
    .join(" ");
  return text.length > 120 ? `${text.slice(0, 120)}…` : text;
}
