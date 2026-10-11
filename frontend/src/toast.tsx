import { useSyncExternalStore } from "react";
import { tr } from "./i18n";
import { UndoIcon } from "./icons";

/** Short messages at the bottom of the screen: a headline, an optional detail (e.g. the
 * task's title on its own line) and an optional undo button. */
type Toast = {
  id: number;
  text: string;
  detail?: string;
  undo?: () => void;
};

let toasts: Toast[] = [];
let nextId = 1;
const listeners = new Set<() => void>();
const emit = () => {
  for (const l of listeners) l();
};

export function toast(
  text: string,
  {
    detail,
    undo,
    ms = 5000,
  }: { detail?: string; undo?: () => void; ms?: number } = {},
) {
  const id = nextId++;
  toasts = [...toasts.slice(-2), { id, text, detail, undo }];
  emit();
  setTimeout(() => dismiss(id), ms);
}

function dismiss(id: number) {
  toasts = toasts.filter((t) => t.id !== id);
  emit();
}

const subscribe = (l: () => void) => {
  listeners.add(l);
  return () => listeners.delete(l);
};

export function Toaster() {
  const list = useSyncExternalStore(subscribe, () => toasts);
  return (
    <div
      aria-live="polite"
      className="pointer-events-none fixed bottom-[calc(3rem+env(safe-area-inset-bottom))] left-1/2 z-50 flex w-max max-w-[min(440px,calc(100vw-32px))] -translate-x-1/2 flex-col items-center gap-2"
    >
      {list.map((t) => (
        <div
          key={t.id}
          className="toast-in pointer-events-auto flex max-w-full items-center gap-3 rounded-[20px] bg-step-5 py-2 pr-2 pl-4 text-[13px] text-on-dark shadow-lift"
        >
          <span className="flex min-w-0 flex-col py-0.5">
            <span className="font-medium">{t.text}</span>
            {t.detail && (
              <span className="line-clamp-2 break-words opacity-75">
                {t.detail}
              </span>
            )}
          </span>
          {t.undo && (
            <button
              type="button"
              aria-label={tr("되돌리기")}
              title={tr("되돌리기")}
              onClick={() => {
                t.undo?.();
                dismiss(t.id);
              }}
              className="flex size-8 shrink-0 cursor-pointer items-center justify-center rounded-full text-on-dark hover:bg-on-dark/10"
            >
              <UndoIcon />
            </button>
          )}
        </div>
      ))}
    </div>
  );
}
