import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { onAgentEvent } from "./agentStream";
import { invalidateFor } from "./api";

export type LinkState = "connecting" | "open" | "closed";

type ServerEvent = {
  type: string;
  data: { object_type?: string; id?: string } & Record<string, unknown>;
  ts: string;
};

/** A system notification for a new Argos notice, when the user allowed them (settings)
 * and this page is in the background. */
function showBrowserNotice(data: Record<string, unknown>) {
  if (!("Notification" in window) || Notification.permission !== "granted")
    return;
  if (document.visibilityState === "visible") return; // the badge is enough on screen
  try {
    new Notification(String(data.title ?? "Argos"), {
      body: data.body ? String(data.body) : undefined,
      tag: String(data.id ?? ""),
    });
  } catch {
    // some browsers (iPad Safari without a home-screen app) cannot show them
  }
}

/** Keeps one WebSocket to /ws open. object.* events refresh the queries they affect;
 * after a reconnect everything is refetched, since events may have been missed (PLAN §7.2). */
export function useRealtime(): LinkState {
  const qc = useQueryClient();
  const [state, setState] = useState<LinkState>("connecting");

  useEffect(() => {
    let ws: WebSocket | null = null;
    let retry = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let stopped = false;
    let everOpened = false;

    const connect = () => {
      const scheme = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${scheme}://${location.host}/ws`);
      setState("connecting");
      ws.onopen = () => {
        retry = 0;
        setState("open");
        if (everOpened) void qc.invalidateQueries();
        everOpened = true;
      };
      ws.onmessage = (msg) => {
        const event = JSON.parse(String(msg.data)) as ServerEvent;
        if (event.type === "notification.created") {
          void qc.invalidateQueries({ queryKey: ["notifications"] });
          showBrowserNotice(event.data);
          return;
        }
        if (event.type === "notification.read") {
          void qc.invalidateQueries({ queryKey: ["notifications"] });
          return;
        }
        if (event.type === "usage.updated") {
          void qc.invalidateQueries({ queryKey: ["usage"] });
          return;
        }
        if (event.type.startsWith("agent.")) {
          onAgentEvent(event.type, event.data);
          if (event.type === "agent.done" || event.type === "agent.error") {
            invalidateFor(qc, "agent_run");
            void qc.invalidateQueries({ queryKey: ["usage"] }); // job counts
          }
          return;
        }
        if (
          (event.type.startsWith("object.") ||
            event.type === "message.created") &&
          event.data.object_type
        ) {
          invalidateFor(qc, event.data.object_type, event.data.id);
        }
      };
      ws.onclose = () => {
        setState("closed");
        if (stopped) return;
        timer = setTimeout(connect, Math.min(1000 * 2 ** retry, 10_000));
        retry += 1;
      };
    };

    connect();
    return () => {
      stopped = true;
      clearTimeout(timer);
      ws?.close();
    };
  }, [qc]);

  return state;
}
