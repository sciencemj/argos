export const LAST_CHANNEL = "argos-last-channel";

/** The channel the app opens on (`/`): the last one shown. */
export function lastChannel(): string | null {
  try {
    return localStorage.getItem(LAST_CHANNEL);
  } catch {
    return null;
  }
}
