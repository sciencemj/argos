import { describe, expect, it } from "vitest";
import { obsidianToMarkdown } from "./pages/NotesTab";

describe("obsidianToMarkdown", () => {
  it("turns wiki links, embeds and block ids into plain Markdown", () => {
    const body = [
      "See [[Graph Theory|graphs]] and [[BFS]].",
      "![[diagram.png]]",
      "![[slides.pdf]]",
      "- [ ] read chapter 1 ^argos-abc123",
      "%%private comment%%",
    ].join("\n");
    expect(obsidianToMarkdown(body, "Courses/Algo/01.md")).toBe(
      [
        "See graphs and BFS.",
        "![diagram.png](/api/v1/vault/file?path=Courses%2FAlgo%2Fdiagram.png)",
        "*(첨부: slides.pdf)*",
        "- [ ] read chapter 1",
        "",
      ].join("\n"),
    );
  });
});
