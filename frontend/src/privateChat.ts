import { useSyncExternalStore } from "react";
import {
  type PrivatePart,
  type PrivateTurn,
  privateFiles,
  streamPrivate,
} from "./api";
import { MAX_FILES } from "./attachments";
import { tr } from "./i18n";

export type { PrivatePart, PrivateTurn } from "./api";

/** A file in the private composer. Shaped like attachments.Draft so DraftChips can draw
 * it, but its content comes back as parts held only in memory (no attachment row). */
export type PrivateDraft = {
  key: string;
  file: File;
  status: "uploading" | "done" | "error";
  parts?: PrivatePart[];
  error?: string;
  preview?: string; // object URL of an image, revoked when the draft goes
};

type State = {
  turns: PrivateTurn[];
  drafts: PrivateDraft[];
  running: boolean;
  error: string | null;
};

const TOO_MANY = "첨부는 10개까지 붙일 수 있어요"; // same copy as attachments.tsx
const TOO_LONG = "대화가 너무 길어요 · 지우고 새로 시작해 주세요";
const EMPTY: State = { turns: [], drafts: [], running: false, error: null };

// App-lifetime only: a module-level Map (never localStorage, sessionStorage, IndexedDB
// or the React Query cache), so a page reload or quitting the app forgets it.
const chats = new Map<string, State>();
const controllers = new Map<string, AbortController>();
const listeners = new Set<() => void>();

const read = (agent: string) => chats.get(agent) ?? EMPTY;

function write(agent: string, change: (s: State) => Partial<State>) {
  const current = read(agent);
  chats.set(agent, { ...current, ...change(current) });
  for (const l of listeners) l();
}

const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => listeners.delete(listener);
};

const forget = (d: PrivateDraft) => {
  if (d.preview) URL.revokeObjectURL(d.preview);
};

/** Characters of text plus base64 sent with every turn. */
function sizeOf(turns: PrivateTurn[]): number {
  let total = 0;
  for (const t of turns) {
    total += t.text.length;
    for (const p of t.parts)
      total += (p.text?.length ?? 0) + (p.data?.length ?? 0);
  }
  return total;
}

function addFiles(agent: string, files: File[]) {
  const room = MAX_FILES - read(agent).drafts.length;
  const fresh: PrivateDraft[] = files.map((file, index) => ({
    key: crypto.randomUUID(),
    file,
    status: index < room ? "uploading" : "error",
    error: index < room ? undefined : tr(TOO_MANY),
    preview: file.type.startsWith("image/")
      ? URL.createObjectURL(file)
      : undefined,
  }));
  write(agent, (s) => ({ drafts: [...s.drafts, ...fresh] }));
  for (const d of fresh) if (d.status === "uploading") upload(agent, d);
}

function patchDraft(agent: string, key: string, change: Partial<PrivateDraft>) {
  // A draft removed or cleared while its file was being read stays gone.
  if (!read(agent).drafts.some((d) => d.key === key)) return;
  write(agent, (s) => ({
    drafts: s.drafts.map((d) => (d.key === key ? { ...d, ...change } : d)),
  }));
}

function upload(agent: string, draft: PrivateDraft) {
  privateFiles(agent, [draft.file]).then(
    (parts) => patchDraft(agent, draft.key, { status: "done", parts }),
    (error: unknown) =>
      patchDraft(agent, draft.key, {
        status: "error",
        error: error instanceof Error ? error.message : String(error),
      }),
  );
}

function retryDraft(agent: string, key: string) {
  const d = read(agent).drafts.find((x) => x.key === key);
  if (d?.status !== "error" || d.error === tr(TOO_MANY)) return;
  patchDraft(agent, key, { status: "uploading", error: undefined });
  upload(agent, d);
}

function removeDraft(agent: string, key: string) {
  const d = read(agent).drafts.find((x) => x.key === key);
  if (!d) return;
  forget(d);
  write(agent, (s) => ({ drafts: s.drafts.filter((x) => x.key !== key) }));
}

/** Last turn (the streaming answer) changed in place. */
function patchAnswer(agent: string, change: (t: PrivateTurn) => PrivateTurn) {
  write(agent, (s) => {
    const last = s.turns.at(-1);
    if (last?.role !== "assistant") return {};
    return { turns: [...s.turns.slice(0, -1), change(last)] };
  });
}

/** Returns whether the message went out (false: busy, empty or too long). */
function send(agent: string, text: string, maxMb: number): boolean {
  const state = read(agent);
  if (state.running || state.drafts.some((d) => d.status === "uploading"))
    return false;
  const ready = state.drafts.filter((d) => d.status === "done");
  const parts = ready.flatMap((d) => d.parts ?? []);
  if (text.trim() === "" && parts.length === 0) return false;
  const turns: PrivateTurn[] = [...state.turns, { role: "user", text, parts }];
  if (sizeOf(turns) > maxMb * 4 * 1024 * 1024) {
    write(agent, () => ({ error: tr(TOO_LONG) }));
    return false;
  }
  for (const d of state.drafts) forget(d);
  const controller = new AbortController();
  controllers.set(agent, controller);
  write(agent, () => ({
    turns: [...turns, { role: "assistant", text: "", parts: [] }],
    drafts: [],
    running: true,
    error: null,
  }));
  // Failed or empty answers are not part of the conversation the model sees.
  const history = turns.filter(
    (t) => !(t.role === "assistant" && (t.error || t.text === "")),
  );
  streamPrivate(
    agent,
    history,
    (token) => patchAnswer(agent, (t) => ({ ...t, text: t.text + token })),
    controller.signal,
  )
    .catch((error: unknown) => {
      if (controller.signal.aborted) return;
      const message = error instanceof Error ? error.message : String(error);
      patchAnswer(agent, (t) => ({ ...t, error: message }));
    })
    .finally(() => {
      if (controllers.get(agent) !== controller) return; // cleared meanwhile
      controllers.delete(agent);
      write(agent, () => ({ running: false }));
    });
  return true;
}

/** Stops the answer; what has arrived so far stays. */
function stop(agent: string) {
  const controller = controllers.get(agent);
  if (!controller) return;
  controllers.delete(agent);
  controller.abort();
  write(agent, () => ({ running: false }));
}

function clear(agent: string) {
  stop(agent);
  for (const d of read(agent).drafts) forget(d);
  chats.delete(agent);
  for (const l of listeners) l();
}

/** The private conversation with one agent, kept while the app runs. `maxMb` is the
 * per-file attachment limit; the whole conversation may be four times that. */
export function usePrivateChat(agentName: string, maxMb = 25) {
  const state = useSyncExternalStore(subscribe, () => read(agentName));
  return {
    turns: state.turns,
    drafts: state.drafts,
    running: state.running,
    error: state.error,
    size: sizeOf(state.turns),
    send: (text: string) => send(agentName, text, maxMb),
    addFiles: (files: File[]) => addFiles(agentName, files),
    removeDraft: (key: string) => removeDraft(agentName, key),
    retryDraft: (key: string) => retryDraft(agentName, key),
    stop: () => stop(agentName),
    clear: () => clear(agentName),
  };
}
