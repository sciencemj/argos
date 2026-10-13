import { describe, expect, test } from "vitest";
import { placeInSlots } from "./channelOrder";

const ch = (id: string) => ({ id });

describe("placeInSlots", () => {
  test("reorders a section without moving the others", () => {
    const all = ["a", "x", "b", "y", "c"].map(ch);
    const out = placeInSlots(all, ["c", "a", "b"]);
    expect(out.map((c) => c.id)).toEqual(["c", "x", "a", "y", "b"]);
  });

  test("ignores unknown ids", () => {
    const all = ["a", "b"].map(ch);
    expect(placeInSlots(all, ["b", "zz", "a"]).map((c) => c.id)).toEqual([
      "b",
      "a",
    ]);
  });
});
