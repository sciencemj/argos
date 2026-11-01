import { afterEach, expect, test, vi } from "vitest";
import { onMainNavigate } from "./desktop";

afterEach(() => {
  delete (window as unknown as { __TAURI__?: unknown }).__TAURI__;
});

test("the app's navigate event routes the page to the path", () => {
  let handler: ((event: { payload: unknown }) => void) | undefined;
  const listen = vi.fn(
    (_event: string, cb: (event: { payload: unknown }) => void) => {
      handler = cb;
      return Promise.resolve(() => {});
    },
  );
  (window as unknown as { __TAURI__: unknown }).__TAURI__ = {
    core: { invoke: vi.fn() },
    event: { listen },
  };
  const cb = vi.fn();
  onMainNavigate(cb);
  expect(listen).toHaveBeenCalledWith("argos:navigate", expect.any(Function));
  handler?.({ payload: "/c/inbox" });
  expect(cb).toHaveBeenCalledWith("/c/inbox");
});

test("outside the app nothing listens", () => {
  const cb = vi.fn();
  expect(() => onMainNavigate(cb)).not.toThrow();
  expect(cb).not.toHaveBeenCalled();
});
