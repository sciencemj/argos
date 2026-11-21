import { useSyncExternalStore } from "react";

/** The Today-page dog: watches the cursor, stays put, or is hidden. Kept per device,
 * like the theme. */
export type MascotPref = "follow" | "still" | "off";

const KEY = "argos.mascot";

export function readMascotPref(): MascotPref {
  try {
    const saved = localStorage.getItem(KEY);
    return saved === "still" || saved === "off" ? saved : "follow";
  } catch {
    return "follow";
  }
}

let current = readMascotPref();
const listeners = new Set<() => void>();

export function setMascotPref(pref: MascotPref) {
  current = pref;
  try {
    if (pref === "follow") localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, pref);
  } catch {
    // Storage can be unavailable (private mode); the choice still holds for this visit.
  }
  for (const l of listeners) l();
}

export function useMascotPref(): MascotPref {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => current,
  );
}
