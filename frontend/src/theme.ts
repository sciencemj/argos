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

export function watchSystemTheme(getPref: () => ThemePref): () => void {
  const mq = media();
  const onChange = () => {
    if (getPref() === "system") applyTheme("system");
  };
  mq.addEventListener("change", onChange);
  return () => mq.removeEventListener("change", onChange);
}
