import { afterEach, expect, test, vi } from "vitest";
// @ts-expect-error The unpacked extension is shipped as plain JavaScript.
import * as collector from "../../integrations/learningx-extension/collector.js";

const { collectLms, downloadMaterial } = collector;

const collect = collectLms as (mode?: string) => Promise<{
  payload: {
    account_id: string;
    courses: { id: string; name: string }[];
    items: {
      kind: string;
      id: string;
      text: string;
      url: string;
      file?: { id: string; size: number };
    }[];
  };
  warnings: string[];
}>;

function setup() {
  vi.stubGlobal(
    "location",
    new URL("https://lms.korea.ac.kr/courses/7/attendance"),
  );
  const requests: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string, init: RequestInit) => {
      const url = new URL(input);
      requests.push(url.href);
      expect(init.credentials).toBe("same-origin");
      expect(init.redirect).toBe("error");
      expect(init.method).toBeUndefined(); // GET only: no messages read/attendance writes.
      let data: unknown = [];
      const headers: Record<string, string> = {
        "Content-Type": "application/json",
      };
      if (url.pathname.endsWith("/profile")) data = { id: 42 };
      else if (url.pathname === "/api/v1/courses")
        data = [{ id: 7, name: "알고리즘" }];
      else if (url.pathname.endsWith("/assignments")) {
        data = [
          {
            id: url.searchParams.has("page") ? 2 : 1,
            name: "과제",
            description: "<p>문제</p><script>secret()</script>",
            html_url: "https://attacker.example/",
            due_at: null,
          },
        ];
        if (!url.searchParams.has("page"))
          headers.Link =
            '<https://lms.korea.ac.kr/api/v1/courses/7/assignments?page=2>; rel="next"';
      } else if (url.pathname.endsWith("/conversations"))
        data = [{ id: 5, subject: "안내", last_message: "메시지 요약" }];
      return new Response(JSON.stringify(data), { headers });
    }),
  );
  return requests;
}

afterEach(() => {
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

test("collector paginates, uses browser credentials, strips HTML and keeps URLs on LMS", async () => {
  const requests = setup();
  const result = await collect();
  expect(result.payload.account_id).toBe("42");
  expect(
    result.payload.items.filter((item) => item.kind === "assignment"),
  ).toHaveLength(2);
  expect(result.payload.items[0].text).toBe("문제");
  expect(result.payload.items[0].url).toBe(
    "https://lms.korea.ac.kr/courses/7/assignments/1",
  );
  expect(result.payload.items.some((item) => item.text === "메시지 요약")).toBe(
    true,
  );
  expect(
    requests.every((url) => url.startsWith("https://lms.korea.ac.kr/api/v1/")),
  ).toBe(true);
  expect(JSON.stringify(result)).not.toMatch(/cookie|Authorization|secret\(\)/);
});

test("login HTML stops collection before an import can be sent", async () => {
  setup();
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response("<html>Login</html>", {
          headers: { "Content-Type": "text/html" },
        }),
    ),
  );
  await expect(collect()).rejects.toThrow("로그인");
});

test("foreign pagination links are rejected while successful data is retained", async () => {
  setup();
  const original = globalThis.fetch;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string, init: RequestInit) => {
      if (input.includes("/assignments"))
        return new Response("[]", {
          headers: {
            "Content-Type": "application/json",
            Link: '<https://attacker.example/api/v1/courses>; rel="next"',
          },
        });
      return original(input, init);
    }),
  );
  const result = await collect();
  expect(result.warnings).toHaveLength(1);
  expect(
    result.payload.items.some((item) => item.kind === "conversation"),
  ).toBe(true);
  expect(result.payload.items.some((item) => item.kind === "assignment")).toBe(
    false,
  );
});

test("attendance is a current-page snapshot and refuses unrelated tables", async () => {
  setup();
  document.body.innerHTML =
    "<table><tr><th>주차</th><th>출결</th></tr><tr><td>1주차</td><td>출석</td></tr></table>";
  const result = await collect("attendance");
  expect(result.payload.items).toHaveLength(1);
  expect(result.payload.items[0].kind).toBe("attendance");
  expect(result.payload.items[0].text).toContain("1주차 | 출석");
  document.body.innerHTML = "<table><tr><th>이름</th></tr></table>";
  await expect(collect("attendance")).rejects.toThrow("출결 표");
});

test("weekly materials use per-file access, recover omitted module items and skip locked files and videos", async () => {
  setup();
  const original = globalThis.fetch;
  const requests: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string, init: RequestInit) => {
      requests.push(input);
      const path = new URL(input).pathname;
      let data: unknown;
      if (path.endsWith("/modules")) data = [{ id: 8, name: "1주차" }];
      else if (path.endsWith("/modules/8/items"))
        data = [
          { id: 50, type: "File", content_id: 100, title: "lecture.pdf" },
          { id: 51, type: "File", content_id: 101, title: "locked.pdf" },
          { id: 52, type: "ExternalTool", title: "video" },
          { id: 53, type: "File", content_id: 102, title: "too large.pdf" },
        ];
      else if (path.includes("/files/"))
        data = {
          id: Number(path.split("/").pop()),
          display_name: "lecture.pdf",
          size: path.endsWith("102") ? 26 * 1024 * 1024 : 12,
          locked_for_user: path.endsWith("101"),
          updated_at: "2026-10-08T07:00:00Z",
          url: "https://secret.example/download?token=secret",
        };
      else return original(input, init);
      return new Response(JSON.stringify(data), {
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
  const result = await collectLms("sync", true);
  const materials = result.payload.items.filter(
    (item: { kind: string }) => item.kind === "material",
  );
  expect(materials).toHaveLength(1);
  expect(materials[0].file).toEqual({
    id: "100",
    name: "lecture.pdf",
    size: 12,
    updated_at: "2026-10-08T07:00:00Z",
  });
  expect(materials[0].text).toContain("1주차");
  expect(result.warnings).toHaveLength(1);
  expect(JSON.stringify(result)).not.toContain("token=secret");
  expect(
    requests.some((url) => /\/courses\/7\/files$/.test(new URL(url).pathname)),
  ).toBe(false);
  expect(requests.some((url) => url.includes("/download"))).toBe(false);
});

test("material download checks account, version, host and bounded bytes without exposing credentials", async () => {
  vi.stubGlobal("location", new URL("https://lms.korea.ac.kr/courses/7"));
  const expected = {
    id: "100",
    name: "lecture.pdf",
    size: 12,
    updated_at: "2026-10-08T07:00:00Z",
  };
  let account = 42;
  let downloadUrl =
    "https://lms.korea.ac.kr/files/100/download?verifier=temporary";
  let bytes = "%PDF-1.7\nabc";
  const requests: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string, init: RequestInit) => {
      requests.push(input);
      expect(init.credentials).toBe("same-origin");
      expect(init.redirect).toBe("error");
      expect(new Headers(init.headers).has("Authorization")).toBe(false);
      expect(init.method).toBeUndefined();
      if (input.includes("/download"))
        return new Response(bytes, {
          headers: { "Content-Type": "application/pdf" },
        });
      return new Response(
        JSON.stringify(
          input.endsWith("/profile")
            ? { id: account }
            : input.endsWith("/public_url")
              ? {
                  public_url:
                    "https://lms.korea.ac.kr/files/100?verifier=temporary",
                }
              : { ...expected, id: 100, url: downloadUrl },
        ),
        { headers: { "Content-Type": "application/json" } },
      );
    }),
  );
  const downloaded = await downloadMaterial("42", "7", expected);
  expect(atob(downloaded.base64)).toBe(bytes);
  expect(JSON.stringify(downloaded)).not.toContain("temporary");
  account = 43;
  expect((await downloadMaterial("42", "7", expected)).error).toContain("계정");
  account = 42;
  downloadUrl = "https://external.example/files/100/download";
  expect((await downloadMaterial("42", "7", expected)).error).toContain(
    "외부 저장소",
  );
  expect(
    requests.every((url) => url.startsWith("https://lms.korea.ac.kr/")),
  ).toBe(true);
  downloadUrl = "https://lms.korea.ac.kr/files/100/download";
  bytes = "short";
  expect((await downloadMaterial("42", "7", expected)).error).toContain(
    "일부만",
  );
  bytes = "a".repeat(13);
  expect((await downloadMaterial("42", "7", expected)).error).toContain("크기");
});

test("storage URL comes from Canvas public_url without fetching storage or forwarding cookies", async () => {
  vi.stubGlobal("location", new URL("https://mylms.korea.ac.kr/courses/7"));
  const expected = {
    id: "100",
    name: "lecture.pdf",
    size: 12,
    updated_at: "2026-10-08T07:00:00Z",
  };
  let url =
    "https://kr.object.gov-ncloudstorage.com/korea-canvas-contents/account_1/tmp/nas_files/100/lecture.pdf?X-Amz-Signature=temporary";
  const requests: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string, init: RequestInit) => {
      requests.push(input);
      expect(init.credentials).toBe("same-origin");
      expect(init.redirect).toBe("error");
      return new Response(
        JSON.stringify(
          input.endsWith("/profile")
            ? { id: 42 }
            : input.endsWith("/public_url")
              ? { public_url: url }
              : {
                  ...expected,
                  id: 100,
                  url: "https://mylms.korea.ac.kr/files/100/download",
                },
        ),
        { headers: { "Content-Type": "application/json" } },
      );
    }),
  );
  expect(await downloadMaterial("42", "7", expected)).toEqual({
    download_url: url,
  });
  expect(requests).toHaveLength(3);
  expect(
    requests.every((request) =>
      request.startsWith("https://mylms.korea.ac.kr/api/v1/"),
    ),
  ).toBe(true);
  for (const unsafe of [
    "https://kr.object.gov-ncloudstorage.com/other-bucket/100/file",
    "https://kr.object.gov-ncloudstorage.com/korea-canvas-contents/999/file",
    "https://kr.object.gov-ncloudstorage.com.attacker.example/korea-canvas-contents/100/file",
  ]) {
    url = unsafe;
    expect((await downloadMaterial("42", "7", expected)).error).toContain(
      "외부 저장소",
    );
  }
});
