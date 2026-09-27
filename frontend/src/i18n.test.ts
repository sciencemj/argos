import { afterEach, expect, test } from "vitest";
import { ApiError } from "./api";
import { tr, tt } from "./i18n";

afterEach(() => localStorage.setItem("argos-language", "ko"));

test("switches static labels and dates to English", () => {
  localStorage.setItem("argos-language", "en");
  expect(tr("iCloud 캘린더")).toBe("iCloud Calendar");
  expect(tt`${3}일 이내`).toBe("Within 3 days");
});

test("preserves user text when translating a template", () => {
  localStorage.setItem("argos-language", "en");
  expect(tt`${"회의 메모"}에게 메시지`).toBe("Message 회의 메모");
});

test("keeps Korean copy when Korean is selected", () => {
  expect(tr("iCloud 캘린더")).toBe("iCloud 캘린더");
  expect(tr("수집함")).toBe("인박스");
  expect(tt`${3}일 이내`).toBe("3일 이내");
});

test("translates common server errors", () => {
  localStorage.setItem("argos-language", "en");
  expect(
    new ApiError(401, "auth", "애플 ID나 앱 전용 암호가 맞지 않아요").message,
  ).toBe("Check your Apple ID and app-specific password.");
});
