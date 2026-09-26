import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The dev backend (make dev) runs on 8100; 8000 belongs to the desktop app.
// ARGOS_BACKEND lets a second dev stack (e.g. an E2E run) proxy to another port.
const backend = process.env.ARGOS_BACKEND ?? "http://127.0.0.1:8100";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5273,
    strictPort: true,
    // Reachable from other devices on the user's tailnet via `tailscale serve`
    // (https://<machine>.<tailnet>.ts.net); other Host headers stay blocked.
    allowedHosts: [".ts.net"],
    proxy: {
      "/api": backend,
      "/ws": { target: backend, ws: true },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
  },
});
