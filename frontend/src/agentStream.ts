import { useSyncExternalStore } from "react";

/** Live text of agent replies while they stream (PLAN §7.2 agent.token, buffered per
 * reply message). The stored message body takes over once the run finishes. */
type Live = { text: string; status: string; runId: string; agentId: string };

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
  };
  if (type === "agent.token") {
    live.set(messageId, {
      ...current,
      text: current.text + String(data.text ?? ""),
    });
  } else if (type === "agent.status") {
    live.set(messageId, { ...current, status: String(data.status ?? "") });
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
