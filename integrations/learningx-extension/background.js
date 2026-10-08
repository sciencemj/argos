import { collectLms, downloadMaterial } from "./collector.js";

const LMS_PATTERNS = ["https://lms.korea.ac.kr/*", "https://mylms.korea.ac.kr/*"];
let running = false;
// Register event handlers synchronously. Service worker modules cannot use top-level await.
const restrictedStorage = chrome.storage.local.setAccessLevel({ accessLevel: "TRUSTED_CONTEXTS" });

export function localServer(value) {
  const url = new URL(value);
  if (url.protocol !== "http:" || url.hostname !== "127.0.0.1" || url.username || url.password ||
      url.pathname !== "/" || url.search || url.hash) {
    throw new Error("Argos 주소는 http://127.0.0.1:포트 형식으로 입력해 주세요.");
  }
  return url.origin;
}

async function sync(mode = "sync", tabId) {
  if (running) throw new Error("이미 수집 중이에요.");
  running = true;
  try {
    await restrictedStorage;
    const { config } = await chrome.storage.local.get("config");
    if (!config?.token) throw new Error("Argos 연결 코드를 먼저 저장해 주세요.");
    const server = localServer(config.server);
    const tabs = await chrome.tabs.query({ url: LMS_PATTERNS });
    const tab = tabId === undefined ? tabs.find((t) => t.active) ?? tabs[0] : tabs.find((t) => t.id === tabId);
    if (!tab?.id) throw new Error("고려대 LMS에 로그인한 탭을 열어 주세요.");
    const results = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: collectLms, args: [mode, Boolean(config.materials)] });
    const collected = results[0]?.result;
    if (!collected?.payload) throw new Error("LMS 데이터를 읽지 못했어요. 탭에서 다시 로그인해 주세요.");
    const response = await fetch(`${server}/api/v1/lms/import`, {
      method: "POST", credentials: "omit", redirect: "error",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${config.token}` },
      body: JSON.stringify(collected.payload), signal: AbortSignal.timeout(30000),
    });
    if (response.status === 401) throw new Error("연결 코드가 만료됐어요. Argos에서 새 코드를 발급해 주세요.");
    if (!response.ok) throw new Error(`Argos 저장 실패 (${response.status}). Argos가 실행 중인지 확인해 주세요.`);
    const counts = await response.json();
    const warnings = [...collected.warnings];
    const files = { created: 0, updated: 0, unchanged: 0 };
    const materialItems = collected.payload.items.filter((item) => item.kind === "material");
    if (materialItems.length) {
      const neededResponse = await fetch(`${server}/api/v1/lms/materials/needed`, {
        method: "POST", credentials: "omit", redirect: "error",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${config.token}` },
        body: JSON.stringify({ ...collected.payload, items: materialItems }), signal: AbortSignal.timeout(30000),
      });
      if (!neededResponse.ok) throw new Error(`학습자료 저장 상태 조회 실패 (${neededResponse.status})`);
      const needed = new Set((await neededResponse.json()).item_ids);
      files.unchanged = materialItems.length - needed.size;
      const pending = materialItems.filter((item) => needed.has(item.id));
      if (pending.length > 50) warnings.push("자료는 한 번에 50개까지 저장해요. 다음 수집에서 이어서 저장합니다.");
      for (const item of pending.slice(0, 50)) {
        try {
          const result = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: downloadMaterial,
            args: [collected.payload.account_id, item.course_id, item.file] });
          const downloaded = result[0]?.result;
          if (downloaded?.error) throw new Error(downloaded.error);
          let body;
          let path;
          const headers = { Authorization: `Bearer ${config.token}` };
          if (downloaded?.download_url) {
            path = "/api/v1/lms/materials/download";
            headers["Content-Type"] = "application/json";
            body = JSON.stringify({ account_id: collected.payload.account_id, item, download_url: downloaded.download_url });
          } else {
            if (!downloaded?.base64) throw new Error("파일을 읽지 못했어요.");
            const bytes = Uint8Array.from(atob(downloaded.base64), (char) => char.charCodeAt(0));
            path = "/api/v1/lms/materials/file";
            body = new FormData();
            body.append("metadata", JSON.stringify({ account_id: collected.payload.account_id, item }));
            body.append("file", new Blob([bytes]), item.file.name);
          }
          const uploaded = await fetch(`${server}${path}`, {
            method: "POST", credentials: "omit", redirect: "error",
            body, headers, signal: AbortSignal.timeout(60000),
          });
          if (!uploaded.ok) throw new Error(`Argos 파일 저장 실패 (${uploaded.status})`);
          const saved = await uploaded.json();
          for (const key of Object.keys(files)) files[key] += saved[key];
        } catch (error) { warnings.push(`${item.title}: ${error.message}`); }
      }
    }
    const status = { ok: true, at: new Date().toISOString(), counts, files, warnings };
    await chrome.storage.local.set({ status });
    return status;
  } catch (error) {
    const status = { ok: false, at: new Date().toISOString(), error: error.message };
    await chrome.storage.local.set({ status });
    throw error;
  } finally { running = false; }
}

chrome.runtime.onMessage.addListener((message, sender, reply) => {
  if (sender.id !== chrome.runtime.id || sender.tab) return;
  if (message.type === "sync") {
    sync(message.mode, message.tabId).then(reply, (error) => reply({ ok: false, error: error.message }));
    return true;
  }
  if (message.type === "configure") {
    (async () => {
      await restrictedStorage;
      const server = localServer(message.server);
      if (!/^[A-Za-z0-9_-]{40,100}$/.test(message.token)) throw new Error("Argos 연결 코드를 확인해 주세요.");
      await chrome.storage.local.set({ config: { server, token: message.token, auto: Boolean(message.auto), materials: Boolean(message.materials) } });
      await chrome.alarms.clear("lms-sync");
      if (message.auto) await chrome.alarms.create("lms-sync", { periodInMinutes: 15 });
      reply({ ok: true });
    })().catch((error) => reply({ ok: false, error: error.message }));
    return true;
  }
  if (message.type === "disconnect") {
    (async () => {
      await chrome.alarms.clear("lms-sync");
      await chrome.storage.local.remove(["config", "status"]);
      reply({ ok: true });
    })();
    return true;
  }
});

async function automatic(tabId) {
  const { config } = await chrome.storage.local.get("config");
  if (!config?.auto || running) return;
  const tabs = await chrome.tabs.query({ url: LMS_PATTERNS });
  if (!tabs.length) return;
  // Avoid collecting repeatedly while the user moves between course pages.
  const { status } = await chrome.storage.local.get("status");
  if (status?.ok && Date.now() - Date.parse(status.at) < 5 * 60_000) return;
  try { await sync("sync", tabId); } catch { /* Popup shows the failed collection. */ }
}
chrome.alarms.onAlarm.addListener((alarm) => { if (alarm.name === "lms-sync") void automatic(); });
chrome.tabs.onUpdated.addListener((tabId, change, tab) => {
  if (change.status === "complete" && /^https:\/\/(my)?lms\.korea\.ac\.kr\//.test(tab.url ?? "") &&
      !tab.url.includes("/xn-sso/")) void automatic(tabId);
});
chrome.runtime.onStartup.addListener(async () => {
  const { config } = await chrome.storage.local.get("config");
  if (config?.auto) await chrome.alarms.create("lms-sync", { periodInMinutes: 15 });
});
