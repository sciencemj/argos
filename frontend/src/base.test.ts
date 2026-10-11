import { afterEach, expect, test, vi } from "vitest";

afterEach(() => {
  document.head.innerHTML = "";
  vi.resetModules();
});

test("the built app opened through Tailscale serve takes its base from the page", async () => {
  document.head.innerHTML = '<meta name="argos-base" content="/argos" />';
  expect((await import("./base")).BASE).toBe("/argos");
});

test("otherwise the base comes from Vite", async () => {
  const fromVite = import.meta.env.BASE_URL.replace(/\/$/, "");
  expect((await import("./base")).BASE).toBe(fromVite);
});
