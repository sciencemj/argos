import {
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
  type RefObject,
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { createPortal } from "react-dom";
import { ddayLabel, ddayTone } from "./dates";
import { tr } from "./i18n";

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

export function ContextMenu({
  x,
  y,
  label,
  onClose,
  onAction,
  items = [],
}: {
  x: number;
  y: number;
  label: string;
  onClose: () => void;
  onAction: () => void;
  /** Plain actions listed before the (destructive) `label` one. */
  items?: { label: string; onAction: () => void }[];
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.querySelector("button")?.focus();
    const outside = (event: PointerEvent) => {
      if (!ref.current?.contains(event.target as Node)) onClose();
    };
    const onEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", onEscape);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("keydown", onEscape);
    };
  }, [onClose]);

  return createPortal(
    <div
      ref={ref}
      role="menu"
      aria-label={tr("삭제 메뉴")}
      className="fixed z-50 min-w-40 rounded-xl border border-line bg-card p-1 shadow-lift"
      style={{
        left: Math.max(8, Math.min(x, window.innerWidth - 168)),
        // One ~40px row per item plus the destructive action, and the menu's padding.
        top: Math.max(
          8,
          Math.min(y, window.innerHeight - (items.length + 1) * 40 - 12),
        ),
      }}
      onContextMenu={(event) => event.preventDefault()}
    >
      {items.map((item) => (
        <button
          key={item.label}
          type="button"
          role="menuitem"
          className="w-full cursor-pointer rounded-lg px-3 py-2 text-left text-[13px] text-ink hover:bg-inset"
          onClick={() => {
            item.onAction();
            onClose();
          }}
        >
          {item.label}
        </button>
      ))}
      <button
        type="button"
        role="menuitem"
        className="w-full cursor-pointer rounded-lg px-3 py-2 text-left text-[13px] text-danger hover:bg-danger-bg"
        onClick={() => {
          onAction();
          onClose();
        }}
      >
        {label}
      </button>
    </div>,
    document.body,
  );
}

/** Plays a short fade-and-rise on `ref` whenever `key` changes (not on first render),
 * without remounting what is inside. Skipped when the system asks for less motion. */
export function useEnterOnChange(
  ref: RefObject<HTMLElement | null>,
  key: unknown,
  from = "0 6px",
) {
  const first = useRef(true);
  // biome-ignore lint/correctness/useExhaustiveDependencies: runs because `key` changed
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    ref.current?.animate(
      [
        { opacity: 0, translate: from },
        { opacity: 1, translate: "0 0" },
      ],
      { duration: 220, easing: "cubic-bezier(0.2, 0.8, 0.2, 1)" },
    );
  }, [key, ref, from]);
}

// Phones get the single-column shell (drawer navigation, full-screen panels).
const MOBILE_QUERY = "(max-width: 767px)";

/** True while the viewport is phone-sized. */
export function useIsMobile(): boolean {
  return useSyncExternalStore(
    (onChange) => {
      const query = window.matchMedia(MOBILE_QUERY);
      query.addEventListener("change", onChange);
      return () => query.removeEventListener("change", onChange);
    },
    () => window.matchMedia(MOBILE_QUERY).matches,
  );
}

// --- SelectMenu: the app's own dropdown (replaces the native select element) -----------------------

export type SelectOption<V extends string = string> = {
  value: V;
  label: ReactNode;
  /** One line under the label (e.g. what a mode does). */
  description?: ReactNode;
  icon?: ReactNode;
  disabled?: boolean;
  /** Plain text for type-ahead and the trigger when `label` is not a string. */
  text?: string;
};

/** A themed popover list: rounded floating card, icon + title + one-line description,
 * a dot on the chosen item. Rendered in a portal (into the open <dialog> when inside
 * one, so modals do not clip or cover it) with fixed positioning that flips and
 * clamps to stay on screen. Keyboard: ↑↓ / Home / End to move, Enter or Space to
 * pick, Escape to close; a click outside closes it. */
export function SelectMenu<V extends string>({
  value,
  onChange,
  options,
  id,
  ariaLabel,
  className = field,
  placement = "bottom",
  disabled = false,
  placeholder,
  title,
  menuWidth,
  renderValue,
}: {
  value: V;
  onChange: (value: V) => void;
  options: SelectOption<V>[];
  id?: string;
  ariaLabel?: string;
  /** The trigger's look; defaults to the form field underline. */
  className?: string;
  placement?: "top" | "bottom";
  disabled?: boolean;
  placeholder?: string;
  title?: string;
  /** Minimum menu width in px (defaults to the trigger's width, at least 220). */
  menuWidth?: number;
  /** What the closed trigger shows (defaults to the chosen option's label). */
  renderValue?: (option: SelectOption<V> | undefined) => ReactNode;
}) {
  const trigger = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [box, setBox] = useState<{
    left: number;
    top?: number;
    bottom?: number;
    width: number;
    maxHeight: number;
  } | null>(null);
  const listId = useId();
  const chosen = options.find((o) => o.value === value);
  const enabled = options
    .map((o, i) => (o.disabled ? -1 : i))
    .filter((i) => i >= 0);

  const place = useCallback(() => {
    const el = trigger.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const margin = 8;
    const width = Math.min(
      Math.max(r.width, menuWidth ?? 220),
      window.innerWidth - margin * 2,
    );
    const left = Math.max(
      margin,
      Math.min(r.left, window.innerWidth - width - margin),
    );
    const below = window.innerHeight - r.bottom - margin;
    const above = r.top - margin;
    const up =
      placement === "top"
        ? above >= 160 || above > below
        : below < 200 && above > below;
    setBox(
      up
        ? {
            left,
            bottom: window.innerHeight - r.top + 6,
            width,
            maxHeight: above - 6,
          }
        : { left, top: r.bottom + 6, width, maxHeight: below - 6 },
    );
  }, [placement, menuWidth]);

  const show = () => {
    if (disabled) return;
    const at = options.findIndex((o) => o.value === value);
    setActive(at >= 0 ? at : (enabled[0] ?? 0));
    place();
    setOpen(true);
  };
  const close = (refocus = true) => {
    setOpen(false);
    if (refocus) trigger.current?.focus();
  };
  const pick = (i: number) => {
    const option = options[i];
    if (!option || option.disabled) return;
    if (option.value !== value) onChange(option.value);
    close();
  };

  // biome-ignore lint/correctness/useExhaustiveDependencies: listeners only follow open
  useEffect(() => {
    if (!open) return;
    const outside = (e: PointerEvent) => {
      const target = e.target as Node;
      if (!list.current?.contains(target) && !trigger.current?.contains(target))
        close(false);
    };
    const reflow = () => place();
    document.addEventListener("pointerdown", outside, true);
    window.addEventListener("resize", reflow);
    window.addEventListener("scroll", reflow, true);
    list.current?.focus();
    return () => {
      document.removeEventListener("pointerdown", outside, true);
      window.removeEventListener("resize", reflow);
      window.removeEventListener("scroll", reflow, true);
    };
  }, [open, place]);

  const move = (step: number) => {
    if (enabled.length === 0) return;
    const at = enabled.indexOf(active);
    const next = enabled[(at + step + enabled.length) % enabled.length];
    setActive(next);
  };
  const onListKey = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    if (e.key === "ArrowDown") move(1);
    else if (e.key === "ArrowUp") move(-1);
    else if (e.key === "Home") setActive(enabled[0] ?? 0);
    else if (e.key === "End") setActive(enabled.at(-1) ?? 0);
    else if (e.key === "Enter" || e.key === " ") pick(active);
    else if (e.key === "Escape") {
      e.stopPropagation(); // a surrounding <dialog> stays open
      close();
    } else if (e.key === "Tab") close(false);
    else if (e.key.length === 1) {
      const key = e.key.toLowerCase();
      const hit = enabled.find((i) => {
        const o = options[i];
        const text = o.text ?? (typeof o.label === "string" ? o.label : "");
        return text.toLowerCase().startsWith(key);
      });
      if (hit !== undefined) setActive(hit);
      return;
    } else return;
    e.preventDefault();
  };
  const onTriggerKey = (e: ReactKeyboardEvent<HTMLButtonElement>) => {
    if (["ArrowDown", "ArrowUp", "Enter", " "].includes(e.key)) {
      e.preventDefault();
      show();
    }
  };

  const host =
    typeof document === "undefined"
      ? null
      : ((trigger.current?.closest("dialog[open]") as HTMLElement | null) ??
        document.body);

  return (
    <>
      <button
        ref={trigger}
        id={id}
        type="button"
        aria-label={ariaLabel}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        title={title}
        disabled={disabled}
        data-value={value}
        onClick={() => (open ? close() : show())}
        onKeyDown={onTriggerKey}
        className={`${className} inline-flex min-w-0 cursor-pointer items-center gap-2 text-left disabled:cursor-default disabled:opacity-50`}
      >
        {chosen?.icon && (
          <span className="shrink-0 text-text-3">{chosen.icon}</span>
        )}
        <span className={`min-w-0 grow truncate ${chosen ? "" : "text-meta"}`}>
          {renderValue
            ? renderValue(chosen)
            : (chosen?.label ?? placeholder ?? "")}
        </span>
        <svg
          aria-hidden="true"
          width="12"
          height="12"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          className={`shrink-0 text-meta transition-transform ${open ? "rotate-180" : ""}`}
        >
          <path d="m6 9 6 6 6-6" />
        </svg>
      </button>
      {open &&
        box &&
        host &&
        createPortal(
          <div
            ref={list}
            id={listId}
            role="listbox"
            tabIndex={-1}
            aria-label={
              ariaLabel ??
              (typeof chosen?.label === "string" ? chosen.label : undefined)
            }
            aria-activedescendant={`${listId}-${active}`}
            onKeyDown={onListKey}
            style={{
              position: "fixed",
              left: box.left,
              top: box.top,
              bottom: box.bottom,
              width: box.width,
              maxHeight: Math.max(120, Math.min(box.maxHeight, 360)),
            }}
            className="z-[1000] flex flex-col gap-0.5 overflow-y-auto rounded-2xl border border-line-soft bg-card p-1.5 text-text shadow-xl outline-none"
          >
            {options.map((o, i) => {
              const selected = o.value === value;
              return (
                <div
                  key={o.value || `empty-${i}`}
                  id={`${listId}-${i}`}
                  role="option"
                  aria-selected={selected}
                  aria-disabled={o.disabled || undefined}
                  data-value={o.value}
                  data-active={i === active || undefined}
                  ref={
                    i === active
                      ? (el) => el?.scrollIntoView?.({ block: "nearest" })
                      : undefined
                  }
                  tabIndex={-1}
                  onPointerMove={() => !o.disabled && setActive(i)}
                  onClick={() => pick(i)}
                  onKeyDown={onListKey}
                  className={`flex items-start gap-2.5 rounded-xl px-2.5 py-2 ${o.disabled ? "cursor-default opacity-45" : "cursor-pointer"} data-active:bg-inset`}
                >
                  {o.icon && (
                    <span className="mt-0.5 shrink-0 text-text-2">
                      {o.icon}
                    </span>
                  )}
                  <span className="flex min-w-0 grow flex-col">
                    <span
                      className={`truncate text-[13.5px] ${selected ? "font-medium text-ink" : "text-text"}`}
                    >
                      {o.label}
                    </span>
                    {o.description && (
                      <span className="text-[12px] leading-snug text-meta">
                        {o.description}
                      </span>
                    )}
                  </span>
                  <span
                    aria-hidden="true"
                    className={`mt-1.5 size-2 shrink-0 rounded-full ${selected ? "bg-ink" : "bg-transparent"}`}
                  />
                </div>
              );
            })}
          </div>,
          host,
        )}
    </>
  );
}

/** Asks before something irreversible, in the app's own dialog (never the browser's own confirm box). */
export function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel,
  danger = false,
  onConfirm,
  onClose,
}: {
  open: boolean;
  title: string;
  message: ReactNode;
  confirmLabel: string;
  danger?: boolean;
  onConfirm: () => void;
  onClose: () => void;
}) {
  return (
    <Dialog open={open} onClose={onClose} title={title}>
      <div className="flex flex-col gap-5">
        <p className="m-0 text-[13.5px] leading-relaxed text-text-2">
          {message}
        </p>
        <div className="flex justify-end gap-2">
          <button type="button" className={btn.ghost} onClick={onClose}>
            {tr("취소")}
          </button>
          <button
            type="button"
            className={danger ? btn.danger : btn.cta}
            onClick={() => {
              onConfirm();
              onClose();
            }}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </Dialog>
  );
}
