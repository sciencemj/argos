import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { NotifySection } from "./OpsSettings";

const mocks = vi.hoisted(() => ({
  settings: { data: {} as Record<string, unknown> },
  save: { mutate: vi.fn(), error: null },
  send: { mutate: vi.fn(), isPending: false, error: null },
}));
vi.mock("../api", () => ({
  useNotifySettings: () => mocks.settings,
  useSaveNotify: () => mocks.save,
  useTestNotify: () => ({ mutate: vi.fn(), error: null }),
  useNotifyTargets: () => ({ data: [], error: null }),
  useSendDigest: () => mocks.send,
  useAgents: () => ({
    data: [
      { name: "hermes", display_name: "Hermes" },
      { name: "claude", display_name: "Claude" },
    ],
  }),
  useBackupKeep: vi.fn(),
  useBackupNow: vi.fn(),
  useInstallService: vi.fn(),
  useOps: vi.fn(),
  usePrepareUninstall: vi.fn(),
  useUninstallService: vi.fn(),
}));
vi.mock("../desktop", () => ({ inDesktopApp: () => false }));

afterEach(cleanup);
beforeEach(() => {
  localStorage.setItem("argos-language", "ko");
  mocks.save.mutate.mockReset();
  mocks.send.mutate.mockReset();
  mocks.settings.data = {
    hermes_target: null,
    hermes_available: false,
    weekly_review_weekday: 6,
    weekly_review_hour: 20,
    digest_hour: 9,
    last_run: null,
    morning_digest_enabled: false,
    morning_digest_hour: 7,
    morning_digest_agent: "",
  };
});

test("turning the morning digest on keeps the other notice settings", () => {
  render(<NotifySection />);
  fireEvent.click(screen.getByRole("checkbox", { name: /아침 요약/ }));
  expect(mocks.save.mutate).toHaveBeenCalledWith(
    expect.objectContaining({
      morning_digest_enabled: true,
      weekly_review_hour: 20,
    }),
  );
});

test("hour and agent pickers save their choice", () => {
  render(<NotifySection />);
  fireEvent.change(screen.getByLabelText("아침 요약 시각"), {
    target: { value: "8" },
  });
  expect(mocks.save.mutate).toHaveBeenLastCalledWith(
    expect.objectContaining({ morning_digest_hour: 8 }),
  );
  fireEvent.change(screen.getByLabelText("아침 요약 에이전트"), {
    target: { value: "claude" },
  });
  expect(mocks.save.mutate).toHaveBeenLastCalledWith(
    expect.objectContaining({ morning_digest_agent: "claude" }),
  );
});

test("the hour picker offers only morning hours", () => {
  render(<NotifySection />);
  const hours = screen
    .getByLabelText("아침 요약 시각")
    .querySelectorAll("option");
  expect(hours).toHaveLength(12);
});

test("send now asks for a digest", () => {
  render(<NotifySection />);
  fireEvent.click(screen.getByRole("button", { name: "지금 보내기" }));
  expect(mocks.send.mutate).toHaveBeenCalled();
});
