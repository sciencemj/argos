import { useSyncExternalStore } from "react";

export type ThemePref = "system" | "light" | "dark";

const KEY = "argos.theme";
const media = () => matchMedia("(prefers-color-scheme: dark)");

export function readThemePref(): ThemePref {
  try {
    const saved = localStorage.getItem(KEY);
    return saved === "light" || saved === "dark" ? saved : "system";
  } catch {
    return "system";
  }
}

/** Mirrors the inline script in index.html; also follows OS changes while on "system". */
export function applyTheme(pref: ThemePref) {
  const dark = pref === "dark" || (pref === "system" && media().matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  try {
    if (pref === "system") localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, pref);
  } catch {
    // Storage can be unavailable (private mode); the theme still applies for this visit.
  }
}

// One shared preference for every control (status bar, settings).
let current = readThemePref();
const listeners = new Set<() => void>();

export function setThemePref(pref: ThemePref) {
  current = pref;
  applyTheme(pref);
  for (const l of listeners) l();
}

/** The saved preference; re-renders when any control changes it. */
export function useThemePref(): ThemePref {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => current,
  );
}

// While on "system", follow the OS switching between light and dark.
if (typeof matchMedia === "function")
  media().addEventListener("change", () => {
    if (current === "system") applyTheme("system");
  });
