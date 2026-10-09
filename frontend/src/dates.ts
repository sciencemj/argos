import { TZDate } from "@date-fns/tz";
import { differenceInCalendarDays, format } from "date-fns";
import { enUS, ko } from "date-fns/locale";
import { language, t } from "./i18n";

/** Display zone (PLAN §5: stored UTC, shown in Asia/Seoul). Set from /config on load. */
let zone = "Asia/Seoul";

export function setZone(tz: string) {
  zone = tz;
}

export const inZone = (value: string | Date) =>
  new TZDate(new Date(value).getTime(), zone);

export const nowInZone = () => TZDate.tz(zone);

/** Midnight of an ISO calendar date ("2026-10-09") in the display zone. */
export function dateInZone(isoDate: string): Date {
  const [y, m, d] = isoDate.split("-").map(Number);
  return new TZDate(y, m - 1, d, zone);
}

/** Calendar days from today to `due` in the display zone: 0 = today, negative = overdue. */
export function dday(due: string, now: Date = new Date()): number {
  return differenceInCalendarDays(inZone(due), inZone(now));
}

export function ddayLabel(days: number): string {
  if (days === 0) return "D-day";
  return days > 0 ? `D-${days}` : `D+${-days}`;
}

/** Rose only for D-1 and closer (Tokens rule), bold outline for D-2..3, grey otherwise. */
export function ddayTone(days: number): "danger" | "strong" | "muted" {
  if (days <= 1) return "danger";
  if (days <= 3) return "strong";
  return "muted";
}

export const fmt = (value: string | Date, pattern: string) =>
  format(inZone(value), pattern, { locale: language() === "ko" ? ko : enUS });

/** "오늘 23:59", "내일 23:59", "금 23:59" within a week, else "9/30 (수) 23:59". */
export function relativeDue(due: string, now: Date = new Date()): string {
  const days = dday(due, now);
  const time = fmt(due, "HH:mm");
  if (days === 0) return `${t("오늘", "Today")} ${time}`;
  if (days === 1) return `${t("내일", "Tomorrow")} ${time}`;
  if (days > 1 && days < 7) return `${fmt(due, "EEE")} ${time}`;
  return fmt(due, "M/d (EEE) HH:mm");
}

/** ISO string for a local wall-clock value from <input type="datetime-local">. */
export function localInputToIso(value: string): string {
  const [date, time] = value.split("T");
  const [y, m, d] = date.split("-").map(Number);
  const [hh, mm] = time.split(":").map(Number);
  return new Date(
    new TZDate(y, m - 1, d, hh, mm, zone).getTime(),
  ).toISOString();
}

export const isoToLocalInput = (iso: string) => fmt(iso, "yyyy-MM-dd'T'HH:mm");
