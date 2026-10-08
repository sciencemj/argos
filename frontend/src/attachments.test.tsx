import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import type { Attachment } from "./api";
import {
  AttachmentList,
  canSend,
  type Draft,
  draftsReducer,
  formatSize,
  pastedFiles,
} from "./attachments";

const file = (name: string, type = "image/png") =>
  new File(["x"], name, { type });

const transfer = (files: File[], text = "") =>
  ({
    files,
    getData: (t: string) => (t === "text/plain" ? text : ""),
  }) as unknown as DataTransfer;

test("a screenshot paste is a file", () => {
  expect(pastedFiles(transfer([file("Screenshot.png")]))).toHaveLength(1);
});

test("copied spreadsheet cells stay text even with a picture on the clipboard", () => {
  expect(pastedFiles(transfer([file("image.png")], "a\tb\n1\t2"))).toEqual([]);
});

test("a Finder copy names its files in the text and is still a file", () => {
  const pdf = file("강의.pdf", "application/pdf");
  expect(pastedFiles(transfer([pdf], "강의.pdf"))).toHaveLength(1);
});

test("plain text paste has no files", () => {
  expect(pastedFiles(transfer([], "hello"))).toEqual([]);
  expect(pastedFiles(null)).toEqual([]);
});

const draft = (key: string, status: Draft["status"]): Draft => ({
  key,
  file: file(`${key}.png`),
  status,
});

test("send waits for every upload and needs text or a file", () => {
  expect(canSend("", [])).toBe(false);
  expect(canSend("hi", [])).toBe(true);
  expect(canSend("", [draft("a", "done")])).toBe(true);
  expect(canSend("hi", [draft("a", "done"), draft("b", "uploading")])).toBe(
    false,
  );
  expect(canSend("hi", [draft("a", "error")])).toBe(false);
});

test("reducer tracks upload results, retries and removal", () => {
  const uploaded = { id: "1", name: "a.png" } as Attachment;
  let state = draftsReducer([], {
    type: "add",
    drafts: [draft("a", "uploading"), draft("b", "uploading")],
  });
  state = draftsReducer(state, {
    type: "done",
    key: "a",
    attachment: uploaded,
  });
  state = draftsReducer(state, {
    type: "error",
    key: "b",
    error: "25MB까지 올릴 수 있어요",
  });
  expect(state.map((d) => d.status)).toEqual(["done", "error"]);
  expect(state[1].error).toBe("25MB까지 올릴 수 있어요");
  state = draftsReducer(state, { type: "retry", key: "b" });
  expect(state[1]).toMatchObject({ status: "uploading", error: undefined });
  state = draftsReducer(state, { type: "remove", key: "a" });
  expect(state.map((d) => d.key)).toEqual(["b"]);
  expect(draftsReducer(state, { type: "clear" })).toEqual([]);
});

test("formats sizes", () => {
  expect(formatSize(512)).toBe("512 B");
  expect(formatSize(2048)).toBe("2.0 KB");
  expect(formatSize(3 * 1024 * 1024)).toBe("3.0 MB");
});

const attachment = (over: Partial<Attachment>): Attachment => ({
  id: "1",
  name: "a.png",
  mime: "image/png",
  size: 10,
  kind: "image",
  width: 40,
  height: 30,
  missing: false,
  ...over,
});

test("images show as pictures, files as cards, missing files greyed", () => {
  render(
    <AttachmentList
      attachments={[
        attachment({ id: "i" }),
        attachment({
          id: "f",
          name: "강의.pdf",
          kind: "pdf",
          mime: "application/pdf",
          width: null,
          height: null,
        }),
        attachment({ id: "m", name: "gone.png", missing: true }),
      ]}
    />,
  );
  expect(screen.getByRole("img", { name: "a.png" })).toHaveAttribute(
    "src",
    "/api/v1/attachments/i/content",
  );
  expect(screen.getByRole("link", { name: /강의\.pdf/ })).toHaveAttribute(
    "download",
    "강의.pdf",
  );
  expect(screen.getByText("파일 없음")).toBeInTheDocument();
});
