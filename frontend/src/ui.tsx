import { type ReactNode, useEffect, useRef } from "react";
import { ddayLabel, ddayTone } from "./dates";

// Shared class strings (Tokens: pill buttons, 24px cards, CTA orange only on actions).
export const btn = {
  cta: "inline-flex h-9 items-center gap-1.5 rounded-full bg-cta px-[18px] text-[13.5px] font-medium text-on-cta shadow-raised cursor-pointer disabled:opacity-50",
  outline:
    "inline-flex h-9 items-center gap-1.5 rounded-full border border-line bg-card px-4 text-[13.5px] font-medium text-text cursor-pointer disabled:opacity-50",
  ghost:
    "inline-flex h-9 items-center gap-1.5 rounded-full px-2.5 text-[13.5px] text-text-3 cursor-pointer hover:text-ink",
  icon: "inline-flex size-8 items-center justify-center rounded-full text-text-3 cursor-pointer hover:bg-inset hover:text-ink",
  danger:
    "inline-flex h-9 items-center gap-1.5 rounded-full border border-danger-line bg-danger-bg px-4 text-[13.5px] font-medium text-danger cursor-pointer",
};

export const card = "rounded-3xl border border-line-soft bg-card shadow-sm";

export const field =
  "h-10 w-full border-0 border-b border-line bg-transparent px-0.5 text-[14px] text-text outline-none placeholder:text-meta focus:border-ink";

export const label = "text-[11.5px] font-medium text-meta";

export function Chip({ children }: { children: ReactNode }) {
  return (
    <span className="rounded-full bg-inset px-[9px] py-0.5 text-[11.5px] font-medium whitespace-nowrap text-text-2">
      {children}
    </span>
  );
}

export function DdayBadge({ days }: { days: number }) {
  const tone = ddayTone(days);
  const cls = {
    danger: "bg-danger-bg text-danger font-medium px-[9px] py-0.5",
    strong: "bg-inset text-text border border-line font-semibold px-2 py-px",
    muted: "bg-inset text-text-2 font-medium px-[9px] py-0.5",
  }[tone];
  return (
    <span
      className={`rounded-full font-mono text-[11.5px] whitespace-nowrap ${cls}`}
    >
      {ddayLabel(days)}
    </span>
  );
}

/** Native <dialog>: focus trap, Esc to close and backdrop come from the browser. */
export function Dialog({
  open,
  onClose,
  title,
  children,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);
  return (
    <dialog
      ref={ref}
      onClose={onClose}
      aria-label={title}
      className="m-auto w-[420px] max-w-[calc(100vw-32px)] rounded-3xl border border-line-soft bg-card p-6 text-text shadow-lift backdrop:bg-black/30"
    >
      <h2 className="m-0 mb-5 text-[22px] font-light tracking-[-0.02em] text-ink">
        {title}
      </h2>
      {children}
    </dialog>
  );
}

export function ErrorText({ error }: { error: unknown }) {
  if (!error) return null;
  return (
    <p role="alert" className="m-0 text-[12.5px] text-danger">
      {error instanceof Error ? error.message : String(error)}
    </p>
  );
}
