import { useEffect, useState } from "react";
import { Link } from "react-router";
import { usePendingApprovals } from "../api";
import { MoonIcon, SunIcon } from "../icons";
import type { LinkState } from "../realtime";
import {
  applyTheme,
  readThemePref,
  type ThemePref,
  watchSystemTheme,
} from "../theme";

const LINK_TEXT: Record<LinkState, string> = {
  open: "실시간 연결됨",
  connecting: "연결 중…",
  closed: "연결 끊김 · 다시 시도 중",
};

const NEXT: Record<ThemePref, ThemePref> = {
  system: "light",
  light: "dark",
  dark: "system",
};
const THEME_TEXT: Record<ThemePref, string> = {
  system: "시스템",
  light: "라이트",
  dark: "다크",
};

/** Bottom bar. Agent usage and sync status slots fill in from Phases 7 and 9. */
export function StatusBar({ link }: { link: LinkState }) {
  const [pref, setPref] = useState<ThemePref>(readThemePref);
  const approvals = usePendingApprovals().data?.length ?? 0;

  useEffect(() => watchSystemTheme(() => pref), [pref]);

  const cycle = () => {
    const next = NEXT[pref];
    setPref(next);
    applyTheme(next);
  };

  return (
    <footer className="col-span-full flex items-center gap-4 border-t border-line-soft bg-rail px-[18px] font-mono text-[11.5px] text-text-3">
      <span className="flex items-center gap-1.5">
        <span
          className={`size-1.5 rounded-full ${link === "open" ? "bg-step-4" : "bg-danger"}`}
          aria-hidden="true"
        />
        <span className={link === "closed" ? "text-danger" : undefined}>
          {LINK_TEXT[link]}
        </span>
      </span>
      <span className="grow" />
      {approvals > 0 && (
        <>
          <Link to="/approvals" className="text-danger hover:underline">
            승인 대기 {approvals}
          </Link>
          <span className="h-3 w-px bg-line" />
        </>
      )}
      <button
        type="button"
        onClick={cycle}
        aria-label={`테마: ${THEME_TEXT[pref]} (눌러서 바꾸기)`}
        className="flex cursor-pointer items-center gap-1.5 text-text-3 hover:text-ink"
      >
        {document.documentElement.dataset.theme === "dark" ? (
          <MoonIcon size={13} />
        ) : (
          <SunIcon size={13} />
        )}
        {THEME_TEXT[pref]}
      </button>
    </footer>
  );
}
