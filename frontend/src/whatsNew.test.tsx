import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { parseChangelog } from "./changelog";
import { WhatsNew } from "./whatsNew";

const mocks = vi.hoisted(() => ({
  seen: { data: undefined as { seen: string | null } | undefined },
  save: { mutate: vi.fn() },
  current: "0.1.10" as string | undefined,
}));
vi.mock("./api", () => ({
  useChangelogSeen: () => mocks.seen,
  useSaveChangelogSeen: () => mocks.save,
}));
vi.mock("./desktop", () => ({
  inDesktopApp: () => mocks.current !== undefined,
  updateStatus: () =>
    mocks.current ? Promise.resolve({ current: mocks.current }) : undefined,
}));

const RELEASES = parseChangelog(
  "## 0.1.10 · 2026-10-20\n\n- 새 기능\n\n## 0.1.9 · 2026-10-10\n\n- 이전 기능\n",
);

// jsdom has no modal dialogs; ui.Dialog only needs `open` to follow the calls.
HTMLDialogElement.prototype.showModal = function (this: HTMLDialogElement) {
  this.open = true;
};
HTMLDialogElement.prototype.close = function (this: HTMLDialogElement) {
  this.open = false;
};

afterEach(cleanup);
beforeEach(() => {
  localStorage.setItem("argos-language", "ko");
  mocks.save.mutate.mockReset();
  mocks.current = "0.1.10";
});

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

test("a new install records the version quietly", async () => {
  mocks.seen.data = { seen: null };
  render(<WhatsNew releases={RELEASES} />);
  await waitFor(() => expect(mocks.save.mutate).toHaveBeenCalledWith("0.1.10"));
  expect(screen.queryByRole("dialog")).toBeNull();
});

test("after an update the new notes show once", async () => {
  mocks.seen.data = { seen: "0.1.8" };
  render(<WhatsNew releases={RELEASES} />);
  expect(await screen.findByRole("dialog")).toBeInTheDocument();
  expect(screen.getByText("새 기능")).toBeInTheDocument();
  expect(screen.getByText("이전 기능")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "확인" }));
  expect(mocks.save.mutate).toHaveBeenCalledWith("0.1.10");
  expect(screen.queryByRole("dialog")).toBeNull();
});

test("nothing shows when up to date", async () => {
  mocks.seen.data = { seen: "0.1.10" };
  render(<WhatsNew releases={RELEASES} />);
  await settle();
  expect(screen.queryByRole("dialog")).toBeNull();
});

test("a newer seen version is left alone", async () => {
  mocks.seen.data = { seen: "0.2.0" };
  render(<WhatsNew releases={RELEASES} />);
  await settle();
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(mocks.save.mutate).not.toHaveBeenCalled();
});

test("nothing shows outside the desktop app", async () => {
  mocks.current = undefined;
  mocks.seen.data = { seen: "0.1.8" };
  render(<WhatsNew releases={RELEASES} />);
  await settle();
  expect(screen.queryByRole("dialog")).toBeNull();
});
