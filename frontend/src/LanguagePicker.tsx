import { language, setLanguage } from "./i18n";

export function LanguagePicker() {
  return (
    <label className="flex items-center gap-2 text-[13px] text-text-3">
      <span>{language() === "ko" ? "언어" : "Language"}</span>
      <select
        aria-label="Language / 언어"
        value={language()}
        onChange={(event) =>
          void setLanguage(event.target.value as "ko" | "en")
        }
        className="rounded-lg border border-line bg-card px-2 py-1 text-ink"
      >
        <option value="ko">한국어</option>
        <option value="en">English</option>
      </select>
    </label>
  );
}
