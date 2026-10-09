import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";
import { LmsSection } from "./LmsSettings";

const mocks = vi.hoisted(() => ({
  status: { data: {} as Record<string, unknown> },
  prepare: {
    data: undefined as Record<string, unknown> | undefined,
    mutate: vi.fn(),
    isPending: false,
  },
  connect: { mutate: vi.fn(), isPending: false },
  disconnect: { mutate: vi.fn(), isPending: false },
}));
vi.mock("../api", () => ({
  useLmsStatus: () => mocks.status,
  usePrepareLmsExtension: () => mocks.prepare,
  useConnectLms: () => mocks.connect,
  useDisconnectLms: () => mocks.disconnect,
}));
vi.mock("../desktop", () => ({ inDesktopApp: () => false }));

beforeEach(() => {
  localStorage.setItem("argos-language", "ko");
  mocks.status.data = { connected: false };
  mocks.prepare.data = undefined;
  mocks.prepare.mutate.mockImplementation((_value, options) => {
    mocks.prepare.data = {
      extension_path:
        "/Users/test/Library/Application Support/Argos/browser-extensions/learningx",
    };
    options.onSuccess();
  });
});

test("installation guide prepares a stable folder and explains Chrome's manual steps", async () => {
  render(<LmsSection />);
  expect(screen.queryByLabelText("확장 프로그램 폴더")).not.toBeInTheDocument();
  fireEvent.click(
    screen.getByRole("button", { name: "확장 프로그램 설치 도우미" }),
  );
  expect(screen.getByText("chrome://extensions")).toBeInTheDocument();
  expect(
    screen.getByText("오른쪽 위 개발자 모드를 켜세요."),
  ).toBeInTheDocument();
  expect(screen.getByLabelText("확장 프로그램 폴더")).toHaveValue(
    String(mocks.prepare.data?.extension_path),
  );
  const writeText = vi.fn(async () => {});
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText },
  });
  fireEvent.click(screen.getByRole("button", { name: "폴더 경로 복사" }));
  await waitFor(() =>
    expect(writeText).toHaveBeenCalledWith(mocks.prepare.data?.extension_path),
  );
});

test("status distinguishes a minted code from a live connection and flags permission changes", () => {
  mocks.status.data = {
    connected: true,
    extension_seen_at: "2026-10-09T01:00:00Z",
    extension_manual_update: true,
  };
  render(<LmsSection />);
  expect(screen.getByText(/확장 프로그램 연결 확인/)).toBeInTheDocument();
  expect(screen.getByText(/권한 구성이 바뀌었어요/)).toBeInTheDocument();
});
