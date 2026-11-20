import { useState } from "react";
import { type LiveStep, useLive } from "./agentStream";
import {
  type Message,
  type RunStep,
  usePendingApprovals,
  useResolveApproval,
  useRunSteps,
} from "./api";
import { type ArtifactKind, ArtifactView } from "./artifact";
import { t, tr } from "./i18n";
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
      // Some models reuse call ids ("call_1") across turns: the open one is the latest.
      const step = [...steps]
        .reverse()
        .find((s) => s.id === row.call_id && s.output === undefined);
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
  return <ToolStepList steps={steps} />;
}

export type StepItem =
  | { kind: "artifact"; index: number; step: LiveStep }
  | { kind: "single"; index: number; step: LiveStep }
  | { kind: "group"; index: number; steps: LiveStep[] };

/** Runs of 2+ ordinary consecutive calls fold into one line; a drawn artifact and a
 * call waiting for the user's approval always stand on their own. */
export function groupSteps(
  steps: LiveStep[],
  waiting: (step: LiveStep) => boolean,
): StepItem[] {
  const items: StepItem[] = [];
  let run: { index: number; steps: LiveStep[] } | null = null;
  const flush = () => {
    if (!run) return;
    items.push(
      run.steps.length === 1
        ? { kind: "single", index: run.index, step: run.steps[0] }
        : { kind: "group", index: run.index, steps: run.steps },
    );
    run = null;
  };
  steps.forEach((step, index) => {
    if (artifactOf(step)) {
      flush();
      items.push({ kind: "artifact", index, step });
    } else if (waiting(step)) {
      flush();
      items.push({ kind: "single", index, step });
    } else {
      run ??= { index, steps: [] };
      run.steps.push(step);
    }
  });
  flush();
  return items;
}

/** Whether `step` is paused on an approval the user has not decided yet. */
function useWaiting(): (step: LiveStep) => boolean {
  const pending = usePendingApprovals();
  const ids = new Set((pending.data ?? []).map((a) => a.id));
  return (step) =>
    Boolean(step.approvalId) &&
    step.output === undefined &&
    ids.has(step.approvalId ?? "");
}

export function ToolStepList({ steps }: { steps: LiveStep[] }) {
  const waiting = useWaiting();
  if (steps.length === 0) return null;
  return (
    <section
      className="flex flex-col gap-1.5"
      aria-label={tr("도구 사용", "Tool calls")}
    >
      {groupSteps(steps, waiting).map((item) => {
        // Call ids can repeat within a run, so the position keeps keys unique.
        if (item.kind === "group")
          return (
            <ToolStepGroup
              key={`${item.index}:group`}
              steps={item.steps}
              first={item.index}
            />
          );
        const key = `${item.index}:${item.step.id}`;
        const artifact = item.kind === "artifact" && artifactOf(item.step);
        return artifact ? (
          <ArtifactView key={key} {...artifact} />
        ) : (
          <ToolStepCard key={key} step={item.step} />
        );
      })}
    </section>
  );
}

const SHOWN_NAMES = 3;

/** Consecutive tool calls in one line: how many, which tools, and how they went;
 * opened, each call with its arguments and result. Folded until the user opens it. */
export function ToolStepGroup({
  steps,
  first = 0,
}: {
  steps: LiveStep[];
  first?: number;
}) {
  const [open, setOpen] = useState(false);
  const names = [...new Set(steps.map((s) => s.name))];
  const running = steps.filter((s) => s.output === undefined).length;
  const failed = steps.filter((s) => s.isError).length;
  return (
    <details
      open={open}
      onToggle={(e) => setOpen(e.currentTarget.open)}
      className="group rounded-2xl border border-line-soft bg-inset/60 text-[12.5px]"
    >
      <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2 select-none [&::-webkit-details-marker]:hidden">
        <span className="text-meta">
          <WrenchIcon size={14} />
        </span>
        <span className="shrink-0 text-ink">
          {t(`도구 ${steps.length}개 실행`, `Ran ${steps.length} tools`)}
        </span>
        <span className="min-w-0 grow truncate font-mono text-[12px] text-meta">
          {names.slice(0, SHOWN_NAMES).join(", ")}
          {names.length > SHOWN_NAMES ? " …" : ""}
        </span>
        {failed > 0 && (
          <span className="shrink-0 rounded-full bg-danger-bg px-2 py-px text-[11px] font-medium text-danger">
            {t(`실패 ${failed}`, `${failed} failed`)}
          </span>
        )}
        <span
          className={`flex shrink-0 items-center gap-1.5 text-[11.5px] ${running ? "text-text-3" : "text-meta"}`}
        >
          {running > 0 && (
            <span
              aria-hidden
              className="size-3 animate-spin rounded-full border-[1.5px] border-line border-t-ink"
            />
          )}
          {running > 0
            ? tr("실행 중…", "Running…")
            : failed === steps.length
              ? tr("실패", "Failed")
              : failed > 0
                ? tr("일부 실패", "Some failed")
                : tr("완료", "Done")}
        </span>
        <span
          aria-hidden
          className="shrink-0 text-meta transition-transform group-open:rotate-90"
        >
          ›
        </span>
      </summary>
      {open && (
        <div className="flex flex-col gap-1.5 px-2 pb-2">
          {steps.map((step, i) => {
            // Call ids can repeat within a run: the run position keeps keys unique.
            const key = `${first + i}:${step.id}`;
            return <ToolStepCard key={key} step={step} />;
          })}
        </div>
      )}
    </details>
  );
}

const ARTIFACT_KINDS = ["html", "svg", "mermaid", "tsx"] as const;

/** A finished render_artifact call: draw its arguments (the code never went back to
 * the model). Anything else, or a failed call, stays an ordinary tool card. */
export function artifactOf(
  step: LiveStep,
): { title: string; kind: ArtifactKind; code: string } | null {
  if (
    step.name !== "render_artifact" ||
    step.output === undefined ||
    step.isError
  )
    return null;
  const { title, kind, code } = step.args as Record<string, unknown>;
  if (
    typeof code !== "string" ||
    !ARTIFACT_KINDS.includes(kind as ArtifactKind)
  )
    return null;
  return {
    title: typeof title === "string" && title ? title : "Artifact",
    kind: kind as ArtifactKind,
    code,
  };
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
      <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2 select-none [&::-webkit-details-marker]:hidden">
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
