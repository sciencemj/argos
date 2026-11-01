import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { PrivatePart } from "./api";
import { usePrivateChat } from "./privateChat";

const encoder = new TextEncoder();
const line = (value: unknown) => encoder.encode(`${JSON.stringify(value)}\n`);

type Call = { url: string; body: unknown };
let calls: Call[] = [];

/** A model that answers with the given NDJSON lines; `hold` keeps the stream open. */
function stubModel(
  lines: unknown[],
  {
    hold = false,
    parts,
  }: { hold?: boolean; parts?: (name: string) => PrivatePart[] } = {},
) {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit) => {
      const body = JSON.parse(String(init.body));
      calls.push({ url, body });
      if (url.endsWith("/private/files")) {
        const files = body.files as { name: string; data: string }[];
        const out = files.flatMap((f): PrivatePart[] =>
          parts
            ? parts(f.name)
            : [
                {
                  kind: "text",
                  name: f.name,
                  text: new TextDecoder().decode(
                    Uint8Array.from(atob(f.data), (c) => c.charCodeAt(0)),
                  ),
                  data: null,
                  mime: null,
                },
              ],
        );
        return new Response(JSON.stringify(out), {
          headers: { "Content-Type": "application/json" },
        });
      }
      const stream = new ReadableStream<Uint8Array>({
        start(controller) {
          for (const l of lines) controller.enqueue(line(l));
          init.signal?.addEventListener("abort", () =>
            controller.error(new DOMException("Aborted", "AbortError")),
          );
          if (!hold) controller.close();
        },
      });
      return new Response(stream, {
        headers: { "Content-Type": "application/x-ndjson" },
      });
    }),
  );
}

let agent = 0;
let name = "";
beforeEach(() => {
  agent += 1;
  name = `local-${agent}`;
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

test("streams tokens into an assistant turn", async () => {
  stubModel([{ token: "안" }, { token: "녕" }, { done: true }]);
  const { result } = renderHook(() => usePrivateChat(name));
  act(() => {
    result.current.send("안녕");
  });
  expect(result.current.running).toBe(true);
  await waitFor(() => expect(result.current.running).toBe(false));
  expect(result.current.turns).toEqual([
    { role: "user", text: "안녕", parts: [] },
    { role: "assistant", text: "안녕", parts: [] },
  ]);
  expect(calls[0].url).toContain(`/api/v1/agents/${name}/private`);
});

test("sends prior turns and parts", async () => {
  stubModel([{ token: "첫 답" }, { done: true }]);
  const { result } = renderHook(() => usePrivateChat(name));
  act(() => result.current.addFiles([new File(["내용"], "a.txt")]));
  await waitFor(() => expect(result.current.drafts[0]?.status).toBe("done"));
  act(() => {
    result.current.send("파일 봐 줘");
  });
  await waitFor(() => expect(result.current.running).toBe(false));
  expect(result.current.drafts).toEqual([]);

  stubModel([{ token: "둘째" }, { done: true }]);
  act(() => {
    result.current.send("더");
  });
  await waitFor(() => expect(result.current.running).toBe(false));
  const sent = calls[0].body as { turns: unknown[] };
  expect(sent.turns).toEqual([
    {
      role: "user",
      text: "파일 봐 줘",
      parts: [
        { kind: "text", name: "a.txt", text: "내용", data: null, mime: null },
      ],
    },
    { role: "assistant", text: "첫 답", parts: [] },
    { role: "user", text: "더", parts: [] },
  ]);
});

test("abort stops the stream and keeps partial text", async () => {
  stubModel([{ token: "부분" }], { hold: true });
  const { result } = renderHook(() => usePrivateChat(name));
  act(() => {
    result.current.send("길게");
  });
  await waitFor(() => expect(result.current.turns[1]?.text).toBe("부분"));
  act(() => result.current.stop());
  await waitFor(() => expect(result.current.running).toBe(false));
  expect(result.current.turns[1]).toEqual({
    role: "assistant",
    text: "부분",
    parts: [],
  });
  expect(result.current.error).toBeNull();
});

test("error line becomes the turn error", async () => {
  stubModel([{ error: "GPU에 연결할 수 없어요 (127.0.0.1)" }]);
  const { result } = renderHook(() => usePrivateChat(name));
  act(() => {
    result.current.send("안녕");
  });
  await waitFor(() => expect(result.current.running).toBe(false));
  expect(result.current.turns[1].error).toBe(
    "GPU에 연결할 수 없어요 (127.0.0.1)",
  );
});

test("clear empties the store", async () => {
  stubModel([{ token: "답" }], { hold: true });
  const { result } = renderHook(() => usePrivateChat(name));
  act(() => {
    result.current.send("안녕");
  });
  await waitFor(() => expect(result.current.turns[1]?.text).toBe("답"));
  act(() => result.current.clear());
  expect(result.current.turns).toEqual([]);
  expect(result.current.drafts).toEqual([]);
  expect(result.current.running).toBe(false);
  expect(result.current.size).toBe(0);
});

test("state survives remount (same module)", async () => {
  stubModel([{ token: "기억" }, { done: true }]);
  const first = renderHook(() => usePrivateChat(name));
  act(() => {
    first.result.current.send("남아 있어?");
  });
  await waitFor(() => expect(first.result.current.running).toBe(false));
  first.unmount();
  const second = renderHook(() => usePrivateChat(name));
  expect(second.result.current.turns.map((t) => t.text)).toEqual([
    "남아 있어?",
    "기억",
  ]);
  const other = renderHook(() => usePrivateChat(`${name}-other`));
  expect(other.result.current.turns).toEqual([]);
});

test("files become drafts then parts", async () => {
  stubModel([{ done: true }], {
    parts: (n) => [
      { kind: "image", name: n, text: null, data: "QUJD", mime: "image/jpeg" },
    ],
  });
  const created = vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:x");
  const revoked = vi.spyOn(URL, "revokeObjectURL").mockReturnValue();
  const { result } = renderHook(() => usePrivateChat(name));
  act(() =>
    result.current.addFiles([new File(["x"], "p.png", { type: "image/png" })]),
  );
  expect(result.current.drafts[0]).toMatchObject({
    status: "uploading",
    preview: "blob:x",
  });
  expect(created).toHaveBeenCalled();
  await waitFor(() => expect(result.current.drafts[0].status).toBe("done"));
  expect(result.current.drafts[0].parts?.[0].kind).toBe("image");
  const upload = calls[0].body as { files: { name: string; data: string }[] };
  expect(upload.files).toEqual([{ name: "p.png", data: btoa("x") }]);

  act(() => {
    result.current.send("");
  });
  await waitFor(() => expect(result.current.running).toBe(false));
  expect(result.current.turns[0].parts[0].data).toBe("QUJD");
  expect(revoked).toHaveBeenCalledWith("blob:x");
});

test("upload failure marks the draft", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            error: { code: "too_large", message: "25MB까지 올릴 수 있어요" },
          }),
          { status: 413 },
        ),
    ),
  );
  const { result } = renderHook(() => usePrivateChat(name));
  act(() => result.current.addFiles([new File(["x"], "big.pdf")]));
  await waitFor(() => expect(result.current.drafts[0].status).toBe("error"));
  expect(result.current.drafts[0].error).toBe("25MB까지 올릴 수 있어요");
});

test("too long conversation refuses to send", async () => {
  stubModel([{ done: true }]);
  // 1 MB limit -> 4 MB conversation.
  const { result } = renderHook(() => usePrivateChat(name, 1));
  act(() => {
    result.current.send("가".repeat(4 * 1024 * 1024 + 1));
  });
  expect(result.current.turns).toEqual([]);
  expect(result.current.running).toBe(false);
  expect(result.current.error).toBe(
    "대화가 너무 길어요 · 지우고 새로 시작해 주세요",
  );
  expect(calls).toEqual([]);
});

test("never writes storage", async () => {
  const local = vi.spyOn(Storage.prototype, "setItem");
  stubModel([{ token: "답" }, { done: true }]);
  const { result } = renderHook(() => usePrivateChat(name));
  act(() => result.current.addFiles([new File(["비밀"], "c.txt")]));
  await waitFor(() => expect(result.current.drafts[0]?.status).toBe("done"));
  act(() => {
    result.current.send("비밀 계약");
  });
  await waitFor(() => expect(result.current.running).toBe(false));
  act(() => result.current.clear());
  expect(local).not.toHaveBeenCalled();
});
