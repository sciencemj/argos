import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { invalidateFor } from "./api";

export type LinkState = "connecting" | "open" | "closed";

type ServerEvent = {
  type: string;
  data: { object_type?: string; id?: string };
  ts: string;
};

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
        if (event.type.startsWith("object.") && event.data.object_type) {
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
