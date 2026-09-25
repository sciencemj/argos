import { useSyncExternalStore } from "react";

/** Short messages at the bottom of the screen, with an optional action (e.g. undo). */
type Toast = {
  id: number;
  text: string;
  action?: { label: string; run: () => void };
};

let toasts: Toast[] = [];
let nextId = 1;
const listeners = new Set<() => void>();
const emit = () => {
  for (const l of listeners) l();
};

export function toast(text: string, action?: Toast["action"], ms = 5000) {
  const id = nextId++;
  toasts = [...toasts.slice(-2), { id, text, action }];
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
      className="pointer-events-none fixed bottom-12 left-1/2 z-50 flex -translate-x-1/2 flex-col items-center gap-2"
    >
      {list.map((t) => (
        <div
          key={t.id}
          className="toast-in pointer-events-auto flex items-center gap-3 rounded-full bg-step-5 py-2 pr-2 pl-4 text-[13px] text-on-dark shadow-lift"
        >
          <span>{t.text}</span>
          {t.action && (
            <button
              type="button"
              onClick={() => {
                t.action?.run();
                dismiss(t.id);
              }}
              className="h-7 cursor-pointer rounded-full px-3 text-[12.5px] font-medium text-on-dark underline-offset-[3px] hover:underline"
            >
              {t.action.label}
            </button>
          )}
        </div>
      ))}
    </div>
  );
}
