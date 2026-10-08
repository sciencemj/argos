const server = document.getElementById("server");
const token = document.getElementById("token");
const auto = document.getElementById("auto");
const materials = document.getElementById("materials");
const output = document.getElementById("status");
function display(status) {
  output.textContent = !status ? "" : status.ok && status.counts
    ? `수집 완료 · 새 항목 ${status.counts.created} · 변경 ${status.counts.updated} · 동일 ${status.counts.unchanged}${status.files ? `\n자료 파일 · 새 파일 ${status.files.created} · 변경 ${status.files.updated} · 동일 ${status.files.unchanged}` : ""}\n${new Date(status.at).toLocaleString()}${status.warnings?.length ? `\n일부 수집 실패:\n${status.warnings.join("\n")}` : ""}`
    : status.error ?? "연결을 저장했어요.";
}
const saved = await chrome.storage.local.get(["config", "status"]);
if (saved.config) {
  server.value = saved.config.server;
  token.value = saved.config.token;
  auto.checked = saved.config.auto;
  materials.checked = Boolean(saved.config.materials);
}
display(saved.status);
async function send(message) {
  const buttons = [...document.querySelectorAll("button")];
  for (const button of buttons) button.disabled = true;
  output.textContent = message.type === "sync" ? "LMS에서 수집 중이에요… 창을 닫아도 계속 진행해요." : "저장 중이에요…";
  try { display(await chrome.runtime.sendMessage(message)); }
  catch (error) { output.textContent = error.message; }
  finally { for (const button of buttons) button.disabled = false; }
}
document.getElementById("settings").addEventListener("submit", async (event) => {
  event.preventDefault();
  await send({ type: "configure", server: server.value.trim(), token: token.value.trim(), auto: auto.checked, materials: materials.checked });
});
document.getElementById("sync").addEventListener("click", () => send({ type: "sync", mode: "sync" }));
document.getElementById("attendance").addEventListener("click", async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  await send({ type: "sync", mode: "attendance", tabId: tab?.id });
});
document.getElementById("disconnect").addEventListener("click", async () => {
  await send({ type: "disconnect" });
  token.value = "";
  output.textContent = "확장 프로그램 연결을 해제했어요. Argos 설정에서도 해제하면 기존 코드가 폐기돼요.";
});
chrome.storage.onChanged.addListener((changes) => { if (changes.status) display(changes.status.newValue); });
