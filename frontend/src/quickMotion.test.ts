import { act, renderHook } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { QUICK_SHOW_EVENT, useExit, useShowCount } from "./quickMotion";

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

const reducedMotion = (reduce: boolean) =>
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: reduce && query.includes("reduce"),
  }));

test("each time the app shows the window, the entrance plays again", () => {
  const { result } = renderHook(() => useShowCount());
  expect(result.current).toBe(0);
  act(() => {
    window.dispatchEvent(new Event(QUICK_SHOW_EVENT));
  });
  act(() => {
    window.dispatchEvent(new Event(QUICK_SHOW_EVENT));
  });
  expect(result.current).toBe(2);
});

test("leaving plays the exit, then runs the close", () => {
  reducedMotion(false);
  vi.useFakeTimers();
  const done = vi.fn();
  const { result } = renderHook(() => useExit(120));
  act(() => result.current.leave(done));
  expect(result.current.leaving).toBe(true);
  expect(done).not.toHaveBeenCalled();
  act(() => {
    vi.advanceTimersByTime(120);
  });
  expect(done).toHaveBeenCalledOnce();
});

test("with reduced motion the window closes at once", () => {
  reducedMotion(true);
  const done = vi.fn();
  const { result } = renderHook(() => useExit(120));
  act(() => result.current.leave(done));
  expect(done).toHaveBeenCalledOnce();
  expect(result.current.leaving).toBe(false);
});

test("showing again after an exit clears the leaving state", () => {
  reducedMotion(false);
  vi.useFakeTimers();
  const { result } = renderHook(() => useExit(120));
  act(() => result.current.leave(() => {}));
  act(() => {
    vi.advanceTimersByTime(120);
  });
  act(() => result.current.reset());
  expect(result.current.leaving).toBe(false);
});
