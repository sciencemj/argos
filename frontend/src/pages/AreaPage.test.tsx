import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeAll, beforeEach, expect, test, vi } from "vitest";
import { AreaPage } from "./AreaPage";

const mocks = vi.hoisted(() => ({
  channels: [] as { id: string; area_id: string; name: string }[],
  create: vi.fn(),
}));
vi.mock("../api", () => ({
  useChannels: () => ({
    isLoading: false,
    data: {
      areas: [{ id: "a1", name: "연구", icon: "flask", sort_order: 1 }],
      channels: mocks.channels,
    },
  }),
  useCreateChannel: () => ({
    mutate: mocks.create,
    isPending: false,
    error: null,
  }),
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
  mocks.channels = [];
});
afterEach(cleanup);

const at = (path: string) =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/area/:areaId" element={<AreaPage />} />
        <Route path="/c/:channelId" element={<p>channel page</p>} />
      </Routes>
    </MemoryRouter>,
  );

test("an empty area stays put and offers to make a channel", () => {
  at("/area/a1");
  expect(screen.getByRole("heading", { name: "연구" })).toBeTruthy();
  expect(
    screen.getByRole("button", { name: "프로젝트 채널 만들기" }),
  ).toBeTruthy();
  expect(screen.queryByRole("button", { name: "과목 채널 만들기" })).toBeNull();
  expect(screen.queryByText("channel page")).toBeNull();
});

test("the one button makes a project channel in this area", () => {
  at("/area/a1");
  fireEvent.click(screen.getByRole("button", { name: "프로젝트 채널 만들기" }));
  fireEvent.change(screen.getByLabelText("이름"), {
    target: { value: "#argos" },
  });
  fireEvent.click(screen.getByRole("button", { name: "추가하기" }));
  expect(mocks.create).toHaveBeenCalledWith(
    { name: "argos", kind: "project", area_id: "a1" },
    expect.anything(),
  );
});

test("an area with channels opens its first one", () => {
  mocks.channels = [{ id: "c1", area_id: "a1", name: "x" }];
  at("/area/a1");
  expect(screen.getByText("channel page")).toBeTruthy();
});
