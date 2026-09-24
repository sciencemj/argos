import { describe, expect, test } from "vitest";
import {
  dday,
  ddayLabel,
  ddayTone,
  localInputToIso,
  relativeDue,
} from "./dates";

describe("dates (Asia/Seoul)", () => {
  // 2026-09-24 23:30 KST is still the 24th in Seoul but 14:30 UTC.
  const now = new Date("2026-09-24T14:30:00Z");

  test("dday counts Seoul calendar days, not 24h blocks", () => {
    expect(dday("2026-09-24T15:10:00Z", now)).toBe(1); // 00:10 KST on the 25th
    expect(dday("2026-09-24T14:59:00Z", now)).toBe(0);
    expect(dday("2026-09-23T10:00:00Z", now)).toBe(-1);
  });

  test("labels and tones follow the token rules", () => {
    expect(ddayLabel(0)).toBe("D-day");
    expect(ddayLabel(2)).toBe("D-2");
    expect(ddayLabel(-3)).toBe("D+3");
    expect(ddayTone(1)).toBe("danger");
    expect(ddayTone(-2)).toBe("danger");
    expect(ddayTone(3)).toBe("strong");
    expect(ddayTone(6)).toBe("muted");
  });

  test("relative due text", () => {
    expect(relativeDue("2026-09-24T14:59:00Z", now)).toBe("오늘 23:59");
    expect(relativeDue("2026-09-25T14:59:00Z", now)).toBe("내일 23:59");
  });

  test("datetime-local input is read as Seoul wall-clock time", () => {
    expect(localInputToIso("2026-09-26T23:59")).toBe(
      "2026-09-26T14:59:00.000Z",
    );
  });
});

import { weekdaysLabel } from "./routines";

test("weekday labels", () => {
  expect(weekdaysLabel("0123456")).toBe("매일");
  expect(weekdaysLabel("01234")).toBe("평일");
  expect(weekdaysLabel("024")).toBe("월·수·금");
});
