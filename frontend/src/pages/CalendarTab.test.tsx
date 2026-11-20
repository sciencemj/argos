import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, expect, test, vi } from "vitest";
import { selectValue } from "../test-select";
import { EventDialog } from "./CalendarTab";

const mocks = vi.hoisted(() => ({ update: vi.fn() }));
vi.mock("../api", () => ({
  useChannels: () => ({
    data: {
      channels: [
        { id: "inbox", name: "inbox", kind: "system" },
        { id: "os", name: "운영체제", kind: "course" },
        { id: "ai", name: "인공지능", kind: "course" },
        { id: "me", name: "일상", kind: "personal" },
        { id: "dm", name: "dm-claude", kind: "dm" },
      ],
    },
  }),
  useUpdateEvent: () => ({
    mutate: mocks.update,
    isPending: false,
    error: null,
  }),
  useDeleteEvent: () => ({ mutate: vi.fn(), isPending: false, error: null }),
}));

beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function showModal() {
    this.open = true;
  };
  HTMLDialogElement.prototype.close = function close() {
    this.open = false;
  };
});
beforeEach(() => {
  localStorage.setItem("argos-language", "ko");
  mocks.update.mockReset();
});
afterEach(cleanup);

const event = {
  id: "e1",
  channel_id: "os",
  title: "중간고사",
  all_day: true,
  start_date: "2026-10-20",
  read_only: false,
} as never;

test("moves an event to another channel", () => {
  render(<EventDialog event={event} onClose={() => {}} />);
  const picker = screen.getByLabelText("채널");
  expect(picker.dataset.value).toBe("os");
  selectValue(picker, "ai");
  fireEvent.click(screen.getByRole("button", { name: "저장" }));
  expect(mocks.update).toHaveBeenCalledWith(
    { id: "e1", title: "중간고사", channel_id: "ai" },
    expect.anything(),
  );
});

test("only real channels are offered, and an unchanged one is not sent", () => {
  render(<EventDialog event={event} onClose={() => {}} />);
  fireEvent.click(screen.getByLabelText("채널"));
  const values = [
    ...document.querySelectorAll<HTMLElement>("[role=option]"),
  ].map((o) => o.dataset.value);
  expect(values).toEqual(["os", "ai", "me"]);
  fireEvent.keyDown(document.activeElement ?? document.body, { key: "Escape" });
  fireEvent.click(screen.getByRole("button", { name: "저장" }));
  expect(mocks.update).toHaveBeenCalledWith(
    { id: "e1", title: "중간고사" },
    expect.anything(),
  );
});
