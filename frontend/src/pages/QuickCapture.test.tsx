import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const hideQuickWindow = vi.fn();
vi.mock("../desktop", () => ({
  hideQuickWindow: () => hideQuickWindow(),
  openMainWindow: vi.fn(),
  inDesktopApp: () => true,
  openAttachment: vi.fn(),
}));
vi.mock("../api", () => ({
  useChannels: () => ({
    data: { channels: [{ id: "inbox", kind: "system", name: "inbox" }] },
  }),
  usePostMessage: () => ({
    isPending: false,
    error: null,
    reset: vi.fn(),
    // The server accepts the message at once.
    mutate: (_body: unknown, options: { onSuccess: () => void }) =>
      options.onSuccess(),
  }),
  uploadAttachment: vi.fn(),
  deleteAttachment: vi.fn(),
  attachmentUrl: (id: string) => id,
}));

const { QuickCapture } = await import("./QuickCapture");

beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal("matchMedia", () => ({ matches: false }));
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  hideQuickWindow.mockClear();
});

test("sending closes right away, without first emptying the card", () => {
  render(<QuickCapture />);
  const input = screen.getByRole("textbox", { name: "빠른 입력" });
  fireEvent.change(input, { target: { value: "우유 사기" } });
  fireEvent.keyDown(input, { key: "Enter" });

  // The exit starts with the words still there (no blank card in between)…
  expect(input).toHaveValue("우유 사기");
  expect(input.closest(".quick-out")).not.toBeNull();
  // …and the window hides once the exit (120 ms) has played.
  act(() => {
    vi.advanceTimersByTime(120);
  });
  expect(hideQuickWindow).toHaveBeenCalledOnce();
});
