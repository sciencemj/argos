import { useCallback, useEffect, useRef, useState } from "react";

/** Sent by the desktop app (lib.rs toggle_quick) each time it shows the quick-capture
 * window. The window is reused, so a focus event is no signal: clicking back into an
 * open window also focuses it. */
export const QUICK_SHOW_EVENT = "argos:quick-show";

const reduceMotion = () =>
  globalThis.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;

/** How many times the window has been shown: a key that replays the entrance. */
export function useShowCount() {
  const [count, setCount] = useState(0);
  useEffect(() => {
    const shown = () => setCount((c) => c + 1);
    window.addEventListener(QUICK_SHOW_EVENT, shown);
    return () => window.removeEventListener(QUICK_SHOW_EVENT, shown);
  }, []);
  return count;
}

/** Plays the exit (`leaving`) for `ms`, then runs the close. Without motion it closes
 * right away. */
export function useExit(ms: number) {
  const [leaving, setLeaving] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  useEffect(() => () => clearTimeout(timer.current), []);
  const leave = useCallback(
    (then: () => void) => {
      if (reduceMotion()) {
        then();
        return;
      }
      setLeaving(true);
      clearTimeout(timer.current);
      timer.current = setTimeout(then, ms);
    },
    [ms],
  );
  const reset = useCallback(() => {
    clearTimeout(timer.current);
    setLeaving(false);
  }, []);
  return { leaving, leave, reset };
}
