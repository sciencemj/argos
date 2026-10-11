/** Where the app is mounted, without a trailing slash: "" at the root, "/argos" under a
 * path. In development Vite's base says so (vite.config.ts); the built app learns it from
 * the meta tag the server adds when the page is opened through Tailscale serve
 * (backend remote.py). */
const served =
  typeof document === "undefined"
    ? null
    : document.querySelector<HTMLMetaElement>('meta[name="argos-base"]')
        ?.content;

export const BASE = (served ?? import.meta.env.BASE_URL).replace(/\/$/, "");
