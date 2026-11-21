import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { Rail } from "./Rail";

const idle = { mutate: vi.fn(), isPending: false, error: null };
vi.mock("../api", () => ({
  useChannels: () => ({ data: { areas: [], channels: [] } }),
  useUnread: () => ({ data: {} }),
  useCreateArea: () => idle,
  useDeleteArea: () => idle,
  useUpdateArea: () => idle,
}));

beforeEach(() => localStorage.setItem("argos-language", "ko"));
afterEach(cleanup);

// "/" sends the app to the channel last open (HomeRedirect); the logo is labelled
// 홈 · 오늘, so it must go to the Today page instead.
test("the logo opens the Today page, not the last channel", () => {
  render(
    <MemoryRouter initialEntries={["/c/some-channel"]}>
      <Rail areaId={null} onPickArea={() => {}} />
      <Routes>
        <Route path="/" element={<p>root redirect</p>} />
        <Route path="/today" element={<p>today page</p>} />
        <Route path="/c/:channelId" element={<p>channel page</p>} />
      </Routes>
    </MemoryRouter>,
  );
  const logo = screen
    .getAllByRole("link", { name: "홈 · 오늘" })
    .find((a) => a.querySelector("svg"));
  expect(logo).toBeDefined();
  fireEvent.click(logo as HTMLElement);
  expect(screen.getByText("today page")).toBeTruthy();
});
