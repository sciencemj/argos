import { useEffect, useState } from "react";

type Health = { status: string; db: string; journal_mode: string };

type HealthState =
  | { kind: "loading" }
  | { kind: "ok"; health: Health }
  | { kind: "error"; message: string };

export default function App() {
  const [state, setState] = useState<HealthState>({ kind: "loading" });

  useEffect(() => {
    fetch("/api/v1/health")
      .then(async (res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        setState({ kind: "ok", health: (await res.json()) as Health });
      })
      .catch((err: unknown) => {
        setState({
          kind: "error",
          message: err instanceof Error ? err.message : String(err),
        });
      });
  }, []);

  return (
    <main>
      <h1>Argos</h1>
      {state.kind === "loading" && <p>서버 상태 확인 중…</p>}
      {state.kind === "ok" && (
        <p>
          서버 연결됨 · DB {state.health.db} ({state.health.journal_mode})
        </p>
      )}
      {state.kind === "error" && (
        <p role="alert">서버에 연결할 수 없음: {state.message}</p>
      )}
    </main>
  );
}
