import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { MaterialsTab } from "./MaterialsTab";

const mocks = vi.hoisted(() => ({
  channel: { id: "c1", vault_path: null as string | null },
  lms: {
    data: [] as Record<string, unknown>[],
    error: null,
    isSuccess: true,
  },
  vault: { data: [] as Record<string, unknown>[], error: null },
}));
vi.mock("../api", () => ({
  attachmentUrl: (id: string) => `/api/v1/attachments/${id}/content`,
  vaultFileUrl: (path: string) => `/vault/${path}`,
  useChannelLmsMaterials: () => mocks.lms,
  useMaterials: () => mocks.vault,
}));
vi.mock("./ChannelPage", () => ({ useChannel: () => mocks.channel }));
vi.mock("../desktop", () => ({
  inDesktopApp: () => false,
  openAttachment: vi.fn(),
}));

afterEach(cleanup);

beforeEach(() => {
  localStorage.setItem("argos-language", "ko");
  mocks.channel.vault_path = null;
  mocks.lms.data = [];
});

test("LMS weekly files show by week even without a vault folder", () => {
  mocks.lms.data = [
    {
      attachment_id: "a2",
      message_id: "m2",
      name: "week2.pdf",
      size: 2048,
      title: "2주차 슬라이드",
      module: "2주차",
      url: "https://lms.school.ac.kr/courses/7/modules/items/2",
      posted_at: "2026-09-08T03:00:00Z",
    },
    {
      attachment_id: "a1",
      message_id: "m1",
      name: "week1.pdf",
      size: 1024,
      title: "1주차 슬라이드",
      module: "1주차",
      url: "https://lms.school.ac.kr/courses/7/modules/items/1",
      posted_at: "2026-09-01T03:00:00Z",
    },
  ];
  render(<MaterialsTab />);
  expect(
    screen.getByRole("region", { name: "LMS 주차학습" }),
  ).toBeInTheDocument();
  const week = screen.getByRole("region", { name: "1주차" });
  expect(week).toHaveTextContent("1주차 슬라이드");
  expect(screen.getByRole("link", { name: "1주차 슬라이드" })).toHaveAttribute(
    "href",
    "/api/v1/attachments/a1/content",
  );
  expect(
    screen.getAllByRole("link", { name: "LMS에서 열기" })[0],
  ).toHaveAttribute(
    "href",
    "https://lms.school.ac.kr/courses/7/modules/items/2",
  );
  expect(screen.queryByText(/옵시디언 폴더가 없어요/)).not.toBeInTheDocument();
});

test("without LMS files or a vault folder the folder hint stays", () => {
  render(<MaterialsTab />);
  expect(screen.getByText(/옵시디언 폴더가 없어요/)).toBeInTheDocument();
});
