import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The dev backend (make dev) runs on 8100; 8000 belongs to the desktop app.
// ARGOS_BACKEND lets a second dev stack (e.g. an E2E run) proxy to another port.
const backend = process.env.ARGOS_BACKEND ?? "http://127.0.0.1:8100";

// The dev server lives under a path so one tailnet address can host several apps:
// `tailscale serve --bg --set-path /argos http://127.0.0.1:5273/argos`
// (serve keeps the path, so the app sees /argos/...). ARGOS_BASE_PATH=/ serves at the
// root. Builds (the desktop app, served by the backend at its root) always use "/".
const devBase =
  `/${(process.env.ARGOS_BASE_PATH ?? "/argos").replace(/^\/+|\/+$/g, "")}/`.replace(
    /^\/\/$/,
    "/",
  );
const prefix = devBase.replace(/\/$/, "");

export default defineConfig(({ command }) => ({
  base: command === "serve" ? devBase : "/",
  plugins: [
    react(),
    tailwindcss(),
    {
      // Vite only answers under "/argos/"; the bare "/argos" someone types gets there too.
      name: "argos-base-redirect",
      configureServer(server) {
        server.middlewares.use((req, res, next) => {
          if (prefix && req.url === prefix) {
            res.writeHead(302, { Location: `${prefix}/` }).end();
            return;
          }
          next();
        });
      },
    },
  ],
  server: {
    // IPv4 loopback explicitly: `localhost` can resolve to ::1 only, which
    // `tailscale serve` (proxying to 127.0.0.1:5273) cannot reach.
    host: "127.0.0.1",
    port: 5273,
    strictPort: true,
    // Reachable from other devices on the user's tailnet via `tailscale serve`
    // (https://<machine>.<tailnet>.ts.net); other Host headers stay blocked.
    allowedHosts: [".ts.net"],
    proxy: {
      [`${prefix}/api`]: {
        target: backend,
        rewrite: (path) => path.slice(prefix.length),
      },
      [`${prefix}/ws`]: {
        target: backend,
        ws: true,
        rewrite: (path) => path.slice(prefix.length),
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
  },
}));
