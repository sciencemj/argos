import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import type { LmsNotice } from "./api";
import { LmsNoticeCard } from "./lmsNotice";

afterEach(cleanup);

const base: LmsNotice = {
  kind: "announcement",
  title: "중간고사 안내",
  text: "범위는 3~7장\n둘째 줄\n셋째 줄\n넷째 줄 https://lms.example/a",
  url: "https://lms.example/1",
  summary: null,
  summary_state: "skipped",
};

test("a summarized notice shows bullets and hides the original until asked", () => {
  render(
    <LmsNoticeCard
      notice={{ ...base, summary: "- 3~7장\n- 101호", summary_state: "done" }}
    />,
  );
  expect(screen.getByText("공지")).toBeTruthy();
  expect(screen.getByText("3~7장")).toBeTruthy();
  expect(screen.queryByText(/둘째 줄/)).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: /원문 보기/ }));
  expect(screen.getByText(/둘째 줄/)).toBeTruthy();
  expect(
    screen.getByRole("link", { name: /LMS에서 열기/ }).getAttribute("href"),
  ).toBe("https://lms.example/1");
});

test("a skipped notice shows the whole original without a toggle", () => {
  render(<LmsNoticeCard notice={base} />);
  expect(screen.getByText(/넷째 줄/)).toBeTruthy();
  expect(screen.queryByRole("button", { name: /원문 보기/ })).toBeNull();
  expect(
    screen.getByRole("link", { name: "https://lms.example/a" }),
  ).toBeTruthy();
});

test("a pending notice shows the first lines and a summarizing hint", () => {
  render(<LmsNoticeCard notice={{ ...base, summary_state: "pending" }} />);
  expect(screen.getByText("요약 중…")).toBeTruthy();
  expect(screen.queryByText(/넷째 줄/)).toBeNull();
});

test("markdown and script links in the original stay plain text", () => {
  render(
    <LmsNoticeCard
      notice={{
        ...base,
        kind: null,
        text: "[링크](javascript:alert(1)) **굵게**",
      }}
    />,
  );
  expect(screen.getByText("LMS")).toBeTruthy();
  expect(screen.getByText("[링크](javascript:alert(1)) **굵게**")).toBeTruthy();
  expect(screen.queryAllByRole("link")).toHaveLength(1); // only "LMS에서 열기"
});
