import { afterEach, expect, test, vi } from "vitest";

type Message = {
  type: string;
  server?: string;
  token?: string;
  auto?: boolean;
  materials?: boolean;
  mode?: string;
  tabId?: number;
};
type Reply = { ok: boolean; error?: string; counts?: { created: number } };
type Listener = (
  message: Message,
  sender: { id: string; tab?: object },
  reply: (value: Reply) => void,
) => boolean | undefined;

async function setup() {
  vi.resetModules();
  const stored: Record<string, unknown> = {};
  let listener: Listener | undefined;
  const chrome = {
    storage: {
      local: {
        setAccessLevel: vi.fn(async () => {}),
        get: vi.fn(async () => stored),
        set: vi.fn(async (values: Record<string, unknown>) => {
          Object.assign(stored, values);
        }),
        remove: vi.fn(async (keys: string[]) => {
          for (const key of keys) delete stored[key];
        }),
      },
    },
    runtime: {
      id: "argos-test-extension",
      onMessage: {
        addListener: vi.fn((value: Listener) => {
          listener = value;
        }),
      },
      onStartup: { addListener: vi.fn() },
    },
    tabs: {
      query: vi.fn(async () => [{ id: 7, active: true }]),
      onUpdated: { addListener: vi.fn() },
    },
    alarms: {
      clear: vi.fn(async () => {}),
      create: vi.fn(async () => {}),
      onAlarm: { addListener: vi.fn() },
    },
    scripting: {
      executeScript: vi.fn(async () => [
        {
          result: {
            payload: { account_id: "42", courses: [], items: [] },
            warnings: [],
          },
        },
      ]),
    },
  };
  vi.stubGlobal("chrome", chrome);
  const fetch = vi.fn(
    async () =>
      new Response(JSON.stringify({ created: 1, updated: 0, unchanged: 0 }), {
        headers: { "Content-Type": "application/json" },
      }),
  );
  vi.stubGlobal("fetch", fetch);
  // @ts-expect-error The unpacked extension is plain JavaScript.
  await import("../../integrations/learningx-extension/background.js");
  function send(message: Message): Promise<Reply> {
    return new Promise((resolve) =>
      listener?.(message, { id: chrome.runtime.id }, resolve),
    );
  }
  return { chrome, fetch, stored, send, getListener: () => listener };
}

afterEach(() => vi.unstubAllGlobals());

test("worker pairs, sends only data to loopback and clears credentials on disconnect", async () => {
  const { chrome, fetch, stored, send } = await setup();
  expect(
    await send({
      type: "configure",
      server: "http://127.0.0.1:8100",
      token: "a".repeat(43),
      auto: true,
    }),
  ).toEqual({ ok: true });
  expect(chrome.storage.local.setAccessLevel).toHaveBeenCalledWith({
    accessLevel: "TRUSTED_CONTEXTS",
  });
  expect(chrome.alarms.create).toHaveBeenCalledWith("lms-sync", {
    periodInMinutes: 15,
  });
  const result = await send({ type: "sync", mode: "sync" });
  expect(result.ok).toBe(true);
  expect(fetch).toHaveBeenCalledTimes(1);
  const [url, options] = fetch.mock.calls[0] as unknown as [
    string,
    RequestInit,
  ];
  expect(url).toBe("http://127.0.0.1:8100/api/v1/lms/import");
  expect(options.credentials).toBe("omit");
  expect(JSON.parse(String(options.body))).toEqual({
    account_id: "42",
    courses: [],
    items: [],
  });
  await send({ type: "disconnect" });
  expect(stored.config).toBeUndefined();
  expect(stored.status).toBeUndefined();
  expect(chrome.alarms.clear).toHaveBeenCalledWith("lms-sync");
});

test("worker rejects remote server addresses and messages from page content scripts", async () => {
  const { stored, send, getListener, chrome } = await setup();
  const result = await send({
    type: "configure",
    server: "https://attacker.example",
    token: "a".repeat(43),
  });
  expect(result.ok).toBe(false);
  expect(stored.config).toBeUndefined();
  const reply = vi.fn();
  getListener()?.(
    { type: "configure" },
    { id: chrome.runtime.id, tab: {} },
    reply,
  );
  expect(reply).not.toHaveBeenCalled();
});

test("worker makes expired pairing visible and does not fall back to unauthenticated writes", async () => {
  const { fetch, send } = await setup();
  await send({
    type: "configure",
    server: "http://127.0.0.1:8100",
    token: "a".repeat(43),
  });
  fetch.mockImplementation(async () => new Response("", { status: 401 }));
  const result = await send({ type: "sync", mode: "sync" });
  expect(result.ok).toBe(false);
  expect(result.error).toContain("연결 코드");
  expect(fetch).toHaveBeenCalledTimes(1);
});

test("worker uploads only missing material bytes as paired multipart and retains partial failures", async () => {
  const { chrome, fetch, send } = await setup();
  await send({
    type: "configure",
    server: "http://127.0.0.1:8100",
    token: "a".repeat(43),
    materials: true,
  });
  const makeItem = (id: string) => ({
    id,
    course_id: "7",
    kind: "material",
    title: `lecture ${id}`,
    url: `https://lms.korea.ac.kr/courses/7/modules/items/${id}`,
    file: {
      id,
      name: "lecture.pdf",
      size: 12,
      updated_at: "2026-10-08T07:00:00Z",
    },
  });
  chrome.scripting.executeScript
    .mockResolvedValueOnce([
      {
        result: {
          payload: {
            account_id: "42",
            courses: [],
            items: [makeItem("50"), makeItem("51"), makeItem("52")],
          },
          warnings: [],
        },
      },
    ] as never)
    .mockResolvedValueOnce([
      { result: { base64: btoa("%PDF-1.7\nabc") } },
    ] as never)
    .mockRejectedValueOnce(new Error("download failed"));
  fetch
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ created: 3, updated: 0, unchanged: 0 })),
    )
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ item_ids: ["50", "52"] })),
    )
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ created: 1, updated: 0, unchanged: 0 })),
    );
  const result = await send({ type: "sync" });
  expect(result.ok).toBe(true);
  expect(result).toMatchObject({
    files: { created: 1, updated: 0, unchanged: 1 },
    warnings: ["lecture 52: download failed"],
  });
  expect(chrome.scripting.executeScript).toHaveBeenCalledTimes(3);
  const [url, options] = fetch.mock.calls[2] as unknown as [
    string,
    RequestInit,
  ];
  expect(url).toBe("http://127.0.0.1:8100/api/v1/lms/materials/file");
  const form = options.body as FormData;
  expect(JSON.parse(String(form.get("metadata")))).toEqual({
    account_id: "42",
    item: makeItem("50"),
  });
  expect((form.get("file") as File).size).toBe(12);
  expect(options.credentials).toBe("omit");
  expect(new Headers(options.headers).get("Authorization")).toBe(
    `Bearer ${"a".repeat(43)}`,
  );
  expect(
    JSON.stringify(chrome.scripting.executeScript.mock.calls),
  ).not.toContain("a".repeat(43));
});

test("worker sends signed storage URLs only to paired local Argos and keeps them out of status", async () => {
  const { chrome, fetch, send, stored } = await setup();
  await send({
    type: "configure",
    server: "http://127.0.0.1:18100",
    token: "a".repeat(43),
    materials: true,
  });
  const item = {
    id: "50",
    course_id: "7",
    kind: "material",
    title: "lecture.pdf",
    file: {
      id: "100",
      name: "lecture.pdf",
      size: 12,
      updated_at: "2026-10-08T07:00:00Z",
    },
  };
  const signedUrl =
    "https://kr.object.gov-ncloudstorage.com/korea-canvas-contents/100/file.pdf?X-Amz-Signature=temporary-secret";
  chrome.scripting.executeScript
    .mockResolvedValueOnce([
      {
        result: {
          payload: { account_id: "42", courses: [], items: [item] },
          warnings: [],
        },
      },
    ] as never)
    .mockResolvedValueOnce([{ result: { download_url: signedUrl } }] as never);
  fetch
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ created: 1, updated: 0, unchanged: 0 })),
    )
    .mockResolvedValueOnce(new Response(JSON.stringify({ item_ids: ["50"] })))
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ created: 1, updated: 0, unchanged: 0 })),
    );
  expect((await send({ type: "sync" })).ok).toBe(true);
  const [url, options] = fetch.mock.calls[2] as unknown as [
    string,
    RequestInit,
  ];
  expect(url).toBe("http://127.0.0.1:18100/api/v1/lms/materials/download");
  expect(JSON.parse(String(options.body))).toEqual({
    account_id: "42",
    item,
    download_url: signedUrl,
  });
  expect(new Headers(options.headers).get("Content-Type")).toBe(
    "application/json",
  );
  expect(options.credentials).toBe("omit");
  expect(JSON.stringify(stored.status)).not.toContain("temporary-secret");
  expect(
    (fetch.mock.calls as unknown as [string, RequestInit][]).every(([url]) =>
      url.startsWith("http://127.0.0.1:18100/"),
    ),
  ).toBe(true);
});
