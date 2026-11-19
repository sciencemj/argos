import { expect, test } from "vitest";
import { personalRedirect } from "./ChannelPage";

const at = (pathname: string, search = "", hash = "") => ({
  pathname,
  search,
  hash,
});

test("a personal session link lands in 내 공간's chat tab", () => {
  expect(personalRedirect(at("/c/p1", "?session=m1"), "p1", "inbox")).toBe(
    "/c/inbox/chat?session=m1",
  );
});

test("other personal links keep their tab", () => {
  expect(personalRedirect(at("/c/p1"), "p1", "inbox")).toBe("/c/inbox");
  expect(personalRedirect(at("/c/p1/kanban", "?task=t"), "p1", "inbox")).toBe(
    "/c/inbox/kanban?task=t",
  );
  expect(personalRedirect(at("/c/p1/chat", "?session=m1"), "p1", "inbox")).toBe(
    "/c/inbox/chat?session=m1",
  );
});
