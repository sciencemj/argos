import { en } from "./en";
import { enTemplates } from "./en-templates";

export type Language = "ko" | "en";

const STORAGE_KEY = "argos-language";
const polishedKorean: Record<string, string> = {
  수집함: "인박스",
  "수집함이 비었어요.": "인박스가 비었어요.",
  알아봤어요: "분류 제안",
  고치기: "수정",
  "지켜보는 중": "바로가기",
};

export function language(): Language {
  const saved = localStorage.getItem(STORAGE_KEY);
  if (saved === "ko" || saved === "en") return saved;
  return navigator.language.toLowerCase().startsWith("ko") ? "ko" : "en";
}

async function syncLanguage(next: Language) {
  await fetch("/api/v1/settings/language", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ language: next }),
  });
}

export function syncCurrentLanguage() {
  void syncLanguage(language()).catch(() => {});
}

export async function setLanguage(next: Language) {
  localStorage.setItem(STORAGE_KEY, next);
  document.documentElement.lang = next;
  try {
    await syncLanguage(next);
  } catch {
    // The local preference still applies when the API is temporarily unavailable.
  }
  window.location.reload();
}

export function t(korean: string, english?: string): string {
  return language() === "ko"
    ? (polishedKorean[korean] ?? korean)
    : (english ?? en[korean] ?? korean);
}

export const tr = t;

export function tt(
  strings: TemplateStringsArray,
  ...values: unknown[]
): string {
  const korean = strings.reduce(
    (result, segment, index) =>
      result + segment + (index < values.length ? String(values[index]) : ""),
    "",
  );
  if (language() === "ko") return korean;
  const key = strings.reduce(
    (result, segment, index) =>
      result + segment + (index < values.length ? `{${index}}` : ""),
    "",
  );
  const translated = enTemplates[key];
  return translated
    ? translated.replace(/\{(\d+)\}/g, (_, index: string) =>
        String(values[Number(index)]),
      )
    : korean;
}

document.documentElement.lang = language();
