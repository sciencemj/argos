import { tr } from "./i18n";
import { AREA_ICON_KEYS, AREA_ICONS, AreaIcon } from "./icons";
import { field, label } from "./ui";

/** Whether an area's stored `icon` is one of the curated SVGs (else it is letters). */
export const isIconKey = (icon: string | null | undefined) =>
  Boolean(icon && Object.hasOwn(AREA_ICONS, icon));

/** An area's rail mark: up to two letters typed in, or one of the curated icons.
 * `value` is what `area.icon` stores ("" = the name's first letter). */
export function AreaIconPicker({
  value,
  name,
  onChange,
}: {
  value: string;
  name: string;
  onChange: (icon: string) => void;
}) {
  const letters = isIconKey(value) ? "" : value;
  const cell =
    "flex size-9 cursor-pointer items-center justify-center rounded-xl border border-line-soft text-[14px] text-text-3 transition-colors hover:bg-inset hover:text-ink aria-pressed:border-ink aria-pressed:bg-inset aria-pressed:text-ink";
  return (
    <div className="flex flex-col gap-3">
      <label className="flex flex-col gap-1">
        <span className={label}>
          {tr(
            "레일 글자 (1~2글자, 비우면 이름 첫 글자)",
            "Rail letters (1–2, empty: first letter of the name)",
          )}
        </span>
        <input
          className={field}
          value={letters}
          maxLength={2}
          placeholder={name.trim().slice(0, 1)}
          onChange={(e) => onChange(e.target.value)}
        />
      </label>
      <fieldset className="m-0 flex min-w-0 flex-col gap-1.5 border-0 p-0">
        <legend className={`${label} mb-1.5 p-0`}>
          {tr("또는 아이콘", "Or an icon")}
        </legend>
        <div className="flex flex-wrap gap-1.5">
          <button
            type="button"
            aria-pressed={!isIconKey(value)}
            aria-label={tr("글자로 표시", "Show letters")}
            title={tr("글자로 표시", "Show letters")}
            className={cell}
            onClick={() => isIconKey(value) && onChange("")}
          >
            {letters || name.trim().slice(0, 1) || "A"}
          </button>
          {AREA_ICON_KEYS.map((key) => (
            <button
              key={key}
              type="button"
              aria-pressed={value === key}
              aria-label={key}
              title={key}
              className={cell}
              onClick={() => onChange(key)}
            >
              <AreaIcon icon={key} size={17} />
            </button>
          ))}
        </div>
      </fieldset>
    </div>
  );
}
