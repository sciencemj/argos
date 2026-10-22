import { expect, test } from "vitest";
import tauriConf from "../../desktop/src-tauri/tauri.conf.json?raw";
import {
  compareVersions,
  parseChangelog,
  RELEASES,
  releasesAfter,
} from "./changelog";

const SAMPLE = `# 변경 내역

## 0.1.10 · 2026-10-20

- 마감이 **캘린더**에 보여요.
- 설정에 \`변경 내역\` 버튼

## 다음

- 버전이 아닌 제목은 무시

## 0.1.9 · 2026-10-10

- 첫 항목
`;

test("reads versions, dates and items in file order", () => {
  expect(parseChangelog(SAMPLE)).toEqual([
    {
      version: "0.1.10",
      date: "2026-10-20",
      items: ["마감이 **캘린더**에 보여요.", "설정에 `변경 내역` 버튼"],
    },
    { version: "0.1.9", date: "2026-10-10", items: ["첫 항목"] },
  ]);
});

test("compares versions as numbers", () => {
  expect(compareVersions("0.1.9", "0.1.10")).toBeLessThan(0);
  expect(compareVersions("1.0.0", "0.9.9")).toBeGreaterThan(0);
  expect(compareVersions("0.1.16", "0.1.16")).toBe(0);
});

test("picks every release since the one seen, newest first", () => {
  const list = parseChangelog(SAMPLE);
  expect(releasesAfter(list, "0.1.8", "0.1.10").map((r) => r.version)).toEqual([
    "0.1.10",
    "0.1.9",
  ]);
  expect(releasesAfter(list, "0.1.10", "0.1.10")).toEqual([]);
  expect(releasesAfter(list, "0.2.0", "0.1.10")).toEqual([]); // seen is newer
  expect(releasesAfter(list, "0.1.8", "0.1.9").map((r) => r.version)).toEqual([
    "0.1.9",
  ]);
});

test("the bundled changelog covers the app version", () => {
  const { version } = JSON.parse(tauriConf) as { version: string };
  expect(RELEASES.length).toBeGreaterThan(0);
  expect(compareVersions(RELEASES[0].version, version)).toBeGreaterThanOrEqual(
    0,
  );
  for (const r of RELEASES) expect(r.items.length).toBeGreaterThan(0);
});
