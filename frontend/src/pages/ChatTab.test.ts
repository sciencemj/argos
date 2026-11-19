import { expect, test } from "vitest";
import { hasChat } from "./ChatTab";

test("내 공간 (personal) chats like courses and projects; system/dm do not", () => {
  expect(hasChat("personal")).toBe(true);
  expect(hasChat("course")).toBe(true);
  expect(hasChat("project")).toBe(true);
  expect(hasChat("system")).toBe(false);
  expect(hasChat("dm")).toBe(false);
});
