import { language, setLanguage, tr } from "./i18n";
import { setThemePref, type ThemePref, useThemePref } from "./theme";
import { SelectMenu } from "./ui";

const small = "rounded-lg border border-line bg-card px-2 py-1 text-ink";

export function LanguagePicker() {
  return (
    <div className="flex items-center gap-2 text-[13px] text-text-3">
      <span>{language() === "ko" ? "언어" : "Language"}</span>
      <SelectMenu
        ariaLabel="Language / 언어"
        value={language()}
        onChange={(value) => void setLanguage(value)}
        className={small}
        menuWidth={160}
        options={[
          { value: "ko", label: "한국어" },
          { value: "en", label: "English" },
        ]}
      />
    </div>
  );
}

/** Light / dark / follow the system. The status bar cycles the same setting; phones
 * have no status bar, so settings carry it too. */
export function ThemePicker() {
  const pref = useThemePref();
  return (
    <div className="flex items-center gap-2 text-[13px] text-text-3">
      <span>{tr("화면")}</span>
      <SelectMenu<ThemePref>
        ariaLabel={tr("화면 테마")}
        value={pref}
        onChange={setThemePref}
        className={small}
        menuWidth={160}
        options={[
          { value: "system", label: tr("시스템") },
          { value: "light", label: tr("라이트") },
          { value: "dark", label: tr("다크") },
        ]}
      />
    </div>
  );
}
