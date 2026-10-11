/** Where the app is mounted, without a trailing slash: "" at the root, "/argos" when
 * the dev server runs under a path (vite.config.ts, `tailscale serve --set-path`). */
export const BASE = import.meta.env.BASE_URL.replace(/\/$/, "");
