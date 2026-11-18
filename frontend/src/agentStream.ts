import { useSyncExternalStore } from "react";

/** Live text of agent replies while they stream (PLAN §7.2 agent.token, buffered per
 * reply message). The stored message body takes over once the run finishes. */
export type LiveStep = {
  id: string; // the tool call id (pairs call and result)
  name: string;
  args: Record<string, unknown>;
  output?: string;
  isError?: boolean;
  approvalId?: string; // the run waited for the user on this call
  summary?: string;
};
type Live = {
  text: string;
  status: string;
  runId: string;
  agentId: string;
  steps: LiveStep[]; // native harness tool calls, in order (v0.2)
};

const live = new Map<string, Live>();
const listeners = new Set<() => void>();
let version = 0;

function emit() {
  version += 1;
  for (const l of listeners) l();
}

export function onAgentEvent(type: string, data: Record<string, unknown>) {
  const messageId = String(data.message_id ?? "");
  if (!messageId) return;
  const current = live.get(messageId) ?? {
    text: "",
    status: "",
    runId: String(data.run_id ?? ""),
    agentId: String(data.agent_id ?? ""),
    steps: [],
  };
  if (type === "agent.token") {
    live.set(messageId, {
      ...current,
      text: current.text + String(data.text ?? ""),
    });
  } else if (type === "agent.status") {
    live.set(messageId, { ...current, status: String(data.status ?? "") });
  } else if (type === "agent.tool_call") {
    live.set(messageId, {
      ...current,
      steps: [
        ...current.steps,
        {
          id: String(data.id ?? ""),
          name: String(data.name ?? ""),
          args: (data.args ?? {}) as Record<string, unknown>,
        },
      ],
    });
  } else if (type === "agent.tool_result") {
    live.set(messageId, {
      ...current,
      steps: current.steps.map((s) =>
        s.id === String(data.id ?? "")
          ? {
              ...s,
              output: String(data.output ?? ""),
              isError: Boolean(data.is_error),
            }
          : s,
      ),
    });
  } else if (type === "agent.approval") {
    // The approval belongs to the newest call of that tool still waiting for a result.
    const steps = [...current.steps];
    for (let i = steps.length - 1; i >= 0; i--) {
      if (steps[i].name === data.tool_name && steps[i].output === undefined) {
        steps[i] = {
          ...steps[i],
          approvalId: String(data.approval_id ?? ""),
          summary: String(data.summary ?? ""),
        };
        break;
      }
    }
    live.set(messageId, { ...current, steps });
  } else if (type === "agent.done" || type === "agent.error") {
    live.delete(messageId);
  }
  emit();
}

const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => listeners.delete(listener);
};

export function useLive(
  messageId: string | null | undefined,
): Live | undefined {
  useSyncExternalStore(subscribe, () => version);
  return messageId ? live.get(messageId) : undefined;
}

/** Agents with a reply streaming right now (sidebar "입력 중…"). */
export function useBusyAgents(): Set<string> {
  useSyncExternalStore(subscribe, () => version);
  return new Set([...live.values()].map((l) => l.agentId));
}
