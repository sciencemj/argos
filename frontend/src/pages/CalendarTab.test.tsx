import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
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

test("cancelling and reopening the same event drops the unsaved draft", () => {
  const { rerender } = render(<EventDialog event={event} onClose={() => {}} />);
  fireEvent.change(screen.getByLabelText("제목"), {
    target: { value: "draft" },
  });
  selectValue(screen.getByLabelText("채널"), "ai");
  rerender(<EventDialog event={null} onClose={() => {}} />);
  rerender(<EventDialog event={event} onClose={() => {}} />);
  expect((screen.getByLabelText("제목") as HTMLInputElement).value).toBe(
    "중간고사",
  );
  expect(screen.getByLabelText("채널").dataset.value).toBe("os");
});

test("another calendar's event: only its channel changes", () => {
  const external = {
    id: "e2",
    channel_id: "me",
    title: "BA 회의",
    all_day: true,
    start_date: "2026-11-22",
    read_only: true,
    source: "직장",
  } as never;
  render(<EventDialog event={external} onClose={() => {}} />);
  expect(screen.getByText(/'직장' 캘린더의 일정이라/)).toBeTruthy();
  expect((screen.getByLabelText("제목") as HTMLInputElement).readOnly).toBe(
    true,
  );
  expect(screen.queryByRole("button", { name: "저장" })).toBeNull();
  selectValue(screen.getByLabelText("채널"), "ai");
  fireEvent.click(screen.getByRole("button", { name: "저장" }));
  expect(mocks.update).toHaveBeenCalledWith(
    { id: "e2", channel_id: "ai" },
    expect.anything(),
  );
  expect(screen.queryByRole("button", { name: "삭제" })).toBeNull();
});
