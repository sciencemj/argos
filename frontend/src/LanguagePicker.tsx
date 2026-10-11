import { language, setLanguage, tr } from "./i18n";
import { setThemePref, type ThemePref, useThemePref } from "./theme";

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

/** Light / dark / follow the system. The status bar cycles the same setting; phones
 * have no status bar, so settings carry it too. */
export function ThemePicker() {
  const pref = useThemePref();
  return (
    <label className="flex items-center gap-2 text-[13px] text-text-3">
      <span>{tr("화면")}</span>
      <select
        aria-label={tr("화면 테마")}
        value={pref}
        onChange={(event) => setThemePref(event.target.value as ThemePref)}
        className="rounded-lg border border-line bg-card px-2 py-1 text-ink"
      >
        <option value="system">{tr("시스템")}</option>
        <option value="light">{tr("라이트")}</option>
        <option value="dark">{tr("다크")}</option>
      </select>
    </label>
  );
}
