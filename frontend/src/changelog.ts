import raw from "../../CHANGELOG.md?raw";

/** One version's notes in CHANGELOG.md (Phase 19). */
export type Release = { version: string; date: string; items: string[] };

const HEADING = /^## (\d+\.\d+\.\d+) · (\d{4}-\d{2}-\d{2})\s*$/;

/** `## x.y.z · YYYY-MM-DD` sections with `- ` items, in file order (newest first).
 * Other headings and their items are skipped. */
export function parseChangelog(text: string): Release[] {
  const releases: Release[] = [];
  let current: Release | null = null;
  for (const line of text.split(/\r?\n/)) {
    if (line.startsWith("## ")) {
      const match = HEADING.exec(line);
      current = match ? { version: match[1], date: match[2], items: [] } : null;
      if (current) releases.push(current);
    } else if (current && line.startsWith("- ")) {
      current.items.push(line.slice(2).trim());
    }
  }
  return releases;
}

export function compareVersions(a: string, b: string): number {
  const x = a.split(".").map(Number);
  const y = b.split(".").map(Number);
  for (let i = 0; i < 3; i++) {
    if (x[i] !== y[i]) return (x[i] ?? 0) - (y[i] ?? 0);
  }
  return 0;
}

/** Releases newer than the one seen, up to the running version. */
export function releasesAfter(
  list: Release[],
  seen: string,
  current: string,
): Release[] {
  return list.filter(
    (r) =>
      compareVersions(r.version, seen) > 0 &&
      compareVersions(r.version, current) <= 0,
  );
}

export const RELEASES = parseChangelog(raw);
