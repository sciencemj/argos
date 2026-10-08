// Serialized by chrome.scripting: helpers must stay inside this function.
export async function collectLms(mode = "sync", materials = false) {
  const origin = location.origin;
  const hosts = ["lms.korea.ac.kr", "mylms.korea.ac.kr"];
  if (location.protocol !== "https:" || !hosts.includes(location.hostname)) {
    throw new Error("고려대 LMS 탭을 열어 주세요.");
  }
  const warnings = [];
  const text = (html) => {
    const doc = new DOMParser().parseFromString(String(html ?? ""), "text/html");
    for (const node of doc.querySelectorAll("script,style")) node.remove();
    return (doc.body.textContent ?? "").trim().slice(0, 20000);
  };
  const safeUrl = (value, fallback) => {
    try {
      const url = new URL(value || fallback, origin);
      if (url.protocol === "https:" && hosts.includes(url.hostname) && !url.username && !url.password &&
          (!url.port || url.port === "443") && url.href.length <= 2000) {
        return url.href;
      }
    } catch { /* Use a known LMS URL below. */ }
    return new URL(fallback, origin).href;
  };
  async function get(path) {
    const url = new URL(path, origin);
    // Never follow a Link header or redirect to SSO/another host with credentials.
    if (url.origin !== origin || !url.pathname.startsWith("/api/v1/")) {
      throw new Error("LMS가 예상하지 못한 조회 주소를 반환했어요.");
    }
    const response = await fetch(url.href, {
      credentials: "same-origin", redirect: "error", cache: "no-store",
      headers: { Accept: "application/json" }, signal: AbortSignal.timeout(15000),
    });
    if (response.status === 401) throw new Error("LMS에 다시 로그인해 주세요.");
    if (!response.ok) throw new Error(`LMS 조회 실패 (${response.status})`);
    if (!response.headers.get("content-type")?.includes("json")) {
      throw new Error("LMS에 로그인한 뒤 다시 수집해 주세요.");
    }
    return { data: await response.json(), link: response.headers.get("link") };
  }
  async function list(path) {
    const rows = [];
    const seen = new Set();
    let next = path;
    for (let page = 0; next && page < 50; page++) {
      if (seen.has(next)) throw new Error("LMS 페이지 목록이 반복돼요.");
      seen.add(next);
      const { data, link } = await get(next);
      if (!Array.isArray(data)) throw new Error("LMS 목록 형식을 확인할 수 없어요.");
      rows.push(...data);
      next = "";
      for (const part of (link ?? "").split(",")) {
        const match = part.match(/<([^>]+)>;\s*rel="next"/);
        if (match) next = match[1];
      }
    }
    if (next) throw new Error("조회량이 너무 많아요. 목록 전체를 가져오지 못했어요.");
    return rows;
  }
  const profile = (await get("/api/v1/users/self/profile")).data;
  if (!profile.id) throw new Error("로그인 계정을 확인할 수 없어요.");
  const rawCourses = await list("/api/v1/courses?enrollment_type=student&enrollment_state=active&per_page=100");
  const courses = rawCourses.filter((c) => c.name).map((c) => ({ id: String(c.id), name: String(c.name).slice(0, 200) }));
  const items = [];
  function add(kind, row, course, title, body, fallback) {
    items.push({
      kind, id: String(row.id), course_id: course?.id ?? null,
      title: String(title || "제목 없음").slice(0, 500), text: text(body),
      url: safeUrl(row.html_url, fallback), due_at: kind === "assignment" ? row.due_at ?? null : null,
    });
  }
  if (mode === "attendance") {
    const match = location.pathname.match(/^\/courses\/(\d+)(?:\/|$)/);
    const course = courses.find((c) => c.id === match?.[1]);
    if (!course) throw new Error("과목의 출결 현황 화면에서 실행해 주세요.");
    const tables = [...document.querySelectorAll("table")].filter((table) =>
      /출결|출석/.test([...table.querySelectorAll("th")].map((cell) => cell.textContent).join(" ")),
    );
    if (!tables.length) throw new Error("출결 표를 찾지 못했어요. 출결 현황 화면을 열어 주세요. iframe이나 별도 앱 화면은 아직 지원하지 않아요.");
    const rows = tables.map((table) => [...table.querySelectorAll("tr")].map((row) =>
      [...row.querySelectorAll("th,td")].map((cell) => (cell.innerText ?? cell.textContent ?? "").trim()).join(" | "),
    ).join("\n")).join("\n\n");
    if (!/출석|지각|결석|미결/.test(rows)) throw new Error("출결 상태가 표시된 표를 찾지 못했어요.");
    add("attendance", { id: location.pathname + location.search }, course, "출결 현황 (화면에서 가져옴)", rows, location.href);
  } else {
    for (const course of courses) {
      for (const kind of ["assignment", "announcement"]) {
        try {
          const path = kind === "assignment"
            ? `/api/v1/courses/${encodeURIComponent(course.id)}/assignments?include[]=submission&per_page=100`
            : `/api/v1/courses/${encodeURIComponent(course.id)}/discussion_topics?only_announcements=true&per_page=100`;
          for (const row of await list(path)) {
            const submitted = kind === "assignment" && row.submission
              ? `\n\n제출 상태: ${row.submission.workflow_state ?? "알 수 없음"}` : "";
            add(kind, row, course, row.name ?? row.title, (row.description ?? row.message ?? "") + submitted,
              `/courses/${course.id}/${kind === "assignment" ? "assignments" : "discussion_topics"}/${row.id}`);
          }
        } catch (error) {
          warnings.push(`${course.name} ${kind === "assignment" ? "과제" : "공지"}: ${error.message}`);
        }
      }
      if (materials) {
        try {
          const modules = await list(`/api/v1/courses/${encodeURIComponent(course.id)}/modules?include[]=items&per_page=100`);
          for (const module of modules) {
            const rows = Array.isArray(module.items) && module.items.length === module.items_count
              ? module.items
              : await list(`/api/v1/courses/${encodeURIComponent(course.id)}/modules/${encodeURIComponent(module.id)}/items?per_page=100`);
            for (const row of rows) {
              if (row.type !== "File") continue; // Videos/external tools are never launched.
              try {
                if (!/^\d+$/.test(String(row.content_id))) throw new Error("파일 식별자가 올바르지 않아요.");
                const file = (await get(`/api/v1/courses/${encodeURIComponent(course.id)}/files/${row.content_id}`)).data;
                if (file.locked_for_user || file.hidden_for_user) continue;
                if (!Number.isInteger(file.size) || file.size <= 0 || file.size > 25 * 1024 * 1024) {
                  throw new Error("파일은 25MB까지 저장할 수 있어요.");
                }
                if (String(file.id) !== String(row.content_id) || !file.updated_at || !file.display_name) {
                  throw new Error("파일 정보를 확인할 수 없어요.");
                }
                add("material", row, course, row.title ?? file.display_name, `주차/단원: ${module.name}`, `/courses/${course.id}/modules/items/${row.id}`);
                items[items.length - 1].file = { id: String(file.id), name: String(file.display_name).slice(0, 200), size: file.size, updated_at: file.updated_at };
              } catch (error) { warnings.push(`${course.name} ${row.title ?? "학습자료"}: ${error.message}`); }
            }
          }
        } catch (error) { warnings.push(`${course.name} 주차학습: ${error.message}`); }
      }
    }
    for (const [kind, path] of [
      ["activity", "/api/v1/users/self/activity_stream?only_active_courses=true&per_page=100"],
      ["conversation", "/api/v1/conversations?per_page=100"],
    ]) {
      try {
        for (const row of await list(path)) {
          const course = courses.find((c) => c.id === String(row.course_id));
          add(kind, { ...row, id: `${row.type ?? kind}:${row.id}` }, course,
            row.title ?? row.subject ?? row.type, row.message ?? row.last_message,
            kind === "conversation" ? "/conversations" : course ? `/courses/${course.id}` : "/");
        }
      } catch (error) { warnings.push(`${kind === "activity" ? "최근 활동" : "메시지 요약"}: ${error.message}`); }
    }
  }
  // Some Canvas streams repeat an item. Keep the last copy within this batch.
  const unique = new Map(items.map((item) => [JSON.stringify([item.kind, item.course_id, item.id]), item]));
  if (courses.length > 200 || unique.size > 5000) throw new Error("수집량이 지원 범위를 초과했어요.");
  if (!unique.size && warnings.length) throw new Error(warnings.join("\n"));
  return { payload: { account_id: String(profile.id), courses, items: [...unique.values()] }, warnings };
}

// Each file is read separately to avoid returning an entire course's bytes at once.
// Serialized by chrome.scripting: no outer helpers or Argos pairing token here.
export async function downloadMaterial(accountId, courseId, expected) {
  try {
    const origin = location.origin;
    if (location.protocol !== "https:" || !["lms.korea.ac.kr", "mylms.korea.ac.kr"].includes(location.hostname) ||
        !/^\d+$/.test(courseId) || !/^\d+$/.test(expected.id)) throw new Error("LMS 파일 주소를 확인해 주세요.");
    async function json(path) {
      const response = await fetch(`${origin}${path}`, { credentials: "same-origin", redirect: "error",
        headers: { Accept: "application/json" }, signal: AbortSignal.timeout(15000), cache: "no-store" });
      if (!response.ok || !response.headers.get("content-type")?.includes("json")) throw new Error("LMS에 다시 로그인해 주세요.");
      return response.json();
    }
    const profile = await json("/api/v1/users/self/profile");
    if (String(profile.id) !== accountId) throw new Error("LMS 계정이 바뀌었어요. 다시 수집해 주세요.");
    const file = await json(`/api/v1/courses/${courseId}/files/${expected.id}`);
    if (String(file.id) !== expected.id || file.size !== expected.size || file.updated_at !== expected.updated_at ||
        file.locked_for_user || file.hidden_for_user) throw new Error("자료가 변경되거나 잠겼어요. 다시 수집해 주세요.");
    const preview = await json(`/api/v1/files/${expected.id}/public_url`);
    let url = new URL(preview.public_url || file.url, origin);
    // Return only a file-specific signed URL to local Argos; it downloads without
    // cookies and never follows redirects. Extension host permissions stay unchanged.
    if (url.protocol === "https:" && url.hostname === "kr.object.gov-ncloudstorage.com" &&
        !url.username && !url.password && (!url.port || url.port === "443") &&
        url.pathname.startsWith("/korea-canvas-contents/") &&
        decodeURIComponent(url.pathname).split("/").includes(expected.id)) {
      return { download_url: url.href };
    }
    // Self-hosted Canvas may return an inline preview route. Keep its original
    // download route for the existing same-origin byte download path.
    if (url.origin === origin) url = new URL(file.url, origin);
    if (url.origin !== origin || url.username || url.password ||
        !/^\/(?:courses\/\d+\/)?files\/\d+\/download\/?$/.test(url.pathname)) {
      throw new Error("외부 저장소의 파일은 아직 지원하지 않아요.");
    }
    const response = await fetch(url.href, { credentials: "same-origin", redirect: "error", cache: "no-store",
      signal: AbortSignal.timeout(30000) });
    if (!response.ok || !response.body || response.headers.get("content-type")?.includes("text/html")) {
      throw new Error("학습자료 다운로드에 실패했어요. LMS 로그인을 확인해 주세요.");
    }
    const limit = 25 * 1024 * 1024;
    if (expected.size < 1 || expected.size > limit || Number(response.headers.get("content-length")) > limit) {
      await response.body.cancel(); throw new Error("파일은 25MB까지 저장할 수 있어요.");
    }
    const reader = response.body.getReader();
    const chunks = [];
    let size = 0;
    try {
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        size += value.byteLength;
        if (size > limit || size > expected.size) throw new Error("파일 크기가 목록과 달라요.");
        chunks.push(value);
      }
    } finally { await reader.cancel(); reader.releaseLock(); }
    if (size !== expected.size) throw new Error("파일이 일부만 내려받아졌어요.");
    const bytes = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
    let binary = "";
    for (let start = 0; start < size; start += 32768) binary += String.fromCharCode(...bytes.subarray(start, start + 32768));
    return { base64: btoa(binary) };
  } catch (error) {
    // Chrome serializes a rejected injected promise as null, losing its message.
    return { error: error.message };
  }
}
