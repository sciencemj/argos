import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { ICloudSection } from "./SettingsPage";

const { mocks, idle } = vi.hoisted(() => ({
  mocks: { options: { mutate: vi.fn(), isPending: false, error: null } },
  idle: () => ({ mutate: vi.fn(), isPending: false, error: null }),
}));
vi.mock("../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api")>()),
  useICloud: () => ({
    data: {
      username: "me@icloud.com",
      connected: true,
      write_calendar: "Argos",
      push_tasks: true,
      split_areas: true,
      poll_minutes: 10,
      calendars: [],
      conflicts: 0,
      status: {
        running: false,
        last_sync_at: null,
        last_error: null,
        last_result: null,
      },
    },
  }),
  useConnectICloud: idle,
  useDisconnectICloud: idle,
  useSyncCalendars: idle,
  useCalendarChannel: idle,
  useCalendarOptions: () => mocks.options,
  useChannels: () => ({ data: { channels: [] } }),
  useCalendarConflicts: () => ({ data: [] }),
  useResolveConflict: idle,
}));

afterEach(cleanup);
beforeEach(() => {
  localStorage.setItem("argos-language", "ko");
  mocks.options.mutate.mockReset();
});

test("turning off deadlines keeps the area split", () => {
  render(<ICloudSection />);
  fireEvent.click(screen.getByRole("checkbox", { name: /칸반 마감/ }));
  expect(mocks.options.mutate).toHaveBeenCalledWith({
    push_tasks: false,
    split_areas: true,
  });
});

test("the split toggle names the area calendars", () => {
  render(<ICloudSection />);
  expect(screen.getByRole("checkbox", { name: /영역별 캘린더/ })).toBeChecked();
  expect(screen.getAllByText(/Argos · 영역 이름/).length).toBeGreaterThan(0);
});

test("the English hint names the title the calendar shows", () => {
  localStorage.setItem("argos-language", "en");
  render(<ICloudSection />);
  expect(screen.getByText(/“Due · title”/)).toBeInTheDocument();
});
