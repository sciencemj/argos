import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { AgentAvatar, agentInfo } from "../agents";
import {
  type ProviderUsage,
  useAgentSettings,
  useICloud,
  usePendingApprovals,
  useUsage,
  useVaultSettings,
} from "../api";
import { fmt } from "../dates";
import { CheckIcon, MoonIcon, SunIcon } from "../icons";
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

/** "4d15h" / "2h13m" / "38m" until `iso`; null once it has passed. */
function until(iso: string | null | undefined, now: number): string | null {
  if (!iso) return null;
  const minutes = Math.floor((Date.parse(iso) - now) / 60_000);
  if (minutes < 0) return null;
  if (minutes >= 48 * 60) {
    return `${Math.floor(minutes / 1440)}d${Math.floor((minutes % 1440) / 60)}h`;
  }
  return minutes >= 60
    ? `${Math.floor(minutes / 60)}h${minutes % 60}m`
    : `${minutes}m`;
}

function ago(iso: string | null | undefined, now: number): string {
  if (!iso) return "";
  const minutes = Math.max(0, Math.floor((now - Date.parse(iso)) / 60_000));
  if (minutes < 1) return "방금";
  if (minutes < 60) return `${minutes}분 전`;
  return `${Math.floor(minutes / 60)}시간 전`;
}

const tone = (pct: number) =>
  pct >= 90 ? "var(--danger)" : pct >= 70 ? "var(--warn)" : undefined;

/** A window counts only until it resets (PLAN Phase 9: a passed reset shows "—"). */
function current(p: ProviderUsage | undefined, name: string, now: number) {
  const w = p?.windows.find((x) => x.name === name);
  if (!w || (w.resets_at && Date.parse(w.resets_at) <= now)) return null;
  return w;
}

const PROVIDER_AGENT: Record<string, string> = {
  claude: "claude",
  codex: "codex",
};

function UsageSlot({ usage, now }: { usage: ProviderUsage; now: number }) {
  const agent = agentInfo(PROVIDER_AGENT[usage.provider]);
  const five = current(usage, "5h", now);
  const week = current(usage, "7d", now);
  if (!five && !week) {
    return (
      <span
        className="flex items-center gap-[7px]"
        title={usage.message ?? undefined}
      >
        <span className="font-medium" style={{ color: agent.text }}>
          {agent.name}
        </span>
        —
      </span>
    );
  }
  const reset = until(five?.resets_at, now);
  return (
    <span className="flex items-center gap-[7px]">
      <span className="font-medium" style={{ color: agent.text }}>
        {agent.name}
      </span>
      {five && (
        <>
          <span>5h</span>
          <span className="flex h-[3px] w-9 overflow-hidden rounded-sm bg-line">
            <span
              className="bg-step-4"
              style={{
                width: `${Math.min(100, five.used_percent)}%`,
                background: tone(five.used_percent),
              }}
            />
          </span>
          <span
            className={five.used_percent >= 70 ? "font-medium" : "text-ink"}
            style={{ color: tone(five.used_percent) }}
          >
            {Math.round(five.used_percent)}%
          </span>
        </>
      )}
      <span>
        {[reset && `⏱${reset}`, week && `7d ${Math.round(week.used_percent)}%`]
          .filter(Boolean)
          .join(" · ")}
      </span>
    </span>
  );
}

/** Click-through details: reset times, when each value was seen, today's jobs. */
function UsagePopover({ now, onClose }: { now: number; onClose: () => void }) {
  const usage = useUsage().data;
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const away = (e: PointerEvent) =>
      ref.current && !ref.current.contains(e.target as Node) && onClose();
    const key = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("pointerdown", away);
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("pointerdown", away);
      document.removeEventListener("keydown", key);
    };
  }, [onClose]);
  if (!usage) return null;
  return (
    <div
      ref={ref}
      role="dialog"
      aria-label="사용량 자세히"
      className="absolute bottom-9 left-0 z-20 flex w-[340px] flex-col gap-3 rounded-2xl border border-line-soft bg-card p-4 font-sans text-[12.5px] text-text-2 shadow-lg"
    >
      {usage.providers.map((p) => (
        <div key={p.provider} className="flex flex-col gap-1">
          <span className="font-medium text-ink">
            {agentInfo(PROVIDER_AGENT[p.provider]).name}
            {p.plan && <span className="ml-1.5 text-meta">{p.plan}</span>}
          </span>
          {p.windows.length === 0 && (
            <span className="text-text-3">{p.message ?? "정보 없음"}</span>
          )}
          {p.windows.map((w) => {
            const left = until(w.resets_at, now);
            return (
              <span key={w.name} className="font-mono text-[11.5px]">
                {w.name} {left ? `${Math.round(w.used_percent)}%` : "—"}
                {w.resets_at &&
                  (left
                    ? ` · ${left} 뒤 초기화 (${fmt(w.resets_at, "M/d HH:mm")})`
                    : " · 초기화됨")}
              </span>
            );
          })}
          {p.observed_at && (
            <span className="text-[11px] text-meta">
              {ago(p.observed_at, now)} 확인
            </span>
          )}
        </div>
      ))}
      <div className="border-t border-line-soft pt-2 text-[12px]">
        코딩 잡: 실행 중 {usage.jobs_running} · 오늘 {usage.jobs_today}
      </div>
    </div>
  );
}

/** Sync health: the newest of calendar and vault syncs, or the first error. */
function SyncSlot({ now }: { now: number }) {
  const icloud = useICloud().data;
  const vault = useVaultSettings().data;
  const parts = [
    icloud?.connected
      ? {
          name: "캘린더",
          at: icloud.status.last_sync_at,
          error: icloud.status.last_error,
        }
      : null,
    vault?.path
      ? {
          name: "볼트",
          at: vault.status.last_run_at,
          error: vault.status.last_error,
        }
      : null,
  ].filter((x) => x !== null);
  if (parts.length === 0) return null;
  const failed = parts.find((p) => p.error);
  const latest = parts
    .map((p) => p.at)
    .filter(Boolean)
    .sort()
    .at(-1);
  const detail = parts
    .map((p) => `${p.name}: ${p.error ?? (p.at ? ago(p.at, now) : "아직")}`)
    .join("\n");
  return (
    <Link
      to="/settings"
      title={detail}
      className={`flex items-center gap-1.5 hover:underline ${failed ? "text-danger" : ""}`}
    >
      {failed ? (
        `${failed.name} 동기화 오류`
      ) : (
        <>
          <CheckIcon size={12} />
          동기화 {latest ? ago(latest, now) : "대기"}
        </>
      )}
    </Link>
  );
}

function useNow(ms: number) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(timer);
  }, [ms]);
  return now;
}

/** Bottom bar (Main design): default agent, plan usage, approvals, sync, theme. */
export function StatusBar({ link }: { link: LinkState }) {
  const [pref, setPref] = useState<ThemePref>(readThemePref);
  const approvals = usePendingApprovals().data?.length ?? 0;

  useEffect(() => watchSystemTheme(() => pref), [pref]);

  const cycle = () => {
    const next = NEXT[pref];
    setPref(next);
    applyTheme(next);
  };

  const now = useNow(30_000);
  const usage = useUsage().data;
  const defaultAgent = useAgentSettings().data?.default_agent;
  const [details, setDetails] = useState(false);
  const divider = <span className="h-3 w-px bg-line" aria-hidden="true" />;

  return (
    <footer className="relative col-span-full flex min-w-0 items-center gap-4 border-t border-line-soft bg-rail px-[18px] font-mono text-[11.5px] whitespace-nowrap text-text-3">
      <span
        className="flex items-center gap-1.5"
        role="status"
        title={LINK_TEXT[link]}
        aria-label={LINK_TEXT[link]}
      >
        <span
          className={`size-1.5 rounded-full ${link === "open" ? "bg-step-4" : "bg-danger"}`}
          aria-hidden="true"
        />
        {link !== "open" && (
          <span className={link === "closed" ? "text-danger" : undefined}>
            {LINK_TEXT[link]}
          </span>
        )}
      </span>
      {defaultAgent && (
        <span className="flex items-center gap-[7px]">
          <AgentAvatar id={defaultAgent} size={16} />
          <span
            className="font-medium"
            style={{ color: agentInfo(defaultAgent).text }}
          >
            {agentInfo(defaultAgent).name}
          </span>
          <span>기본</span>
        </span>
      )}
      {usage && (
        <>
          {divider}
          <button
            type="button"
            aria-label="사용량 자세히 보기"
            aria-expanded={details}
            onClick={() => setDetails((v) => !v)}
            className="flex cursor-pointer items-center gap-4 text-text-3 hover:text-ink"
          >
            {usage.providers.map((p, i) => (
              <span key={p.provider} className="flex items-center gap-4">
                {i > 0 && divider}
                <UsageSlot usage={p} now={now} />
              </span>
            ))}
          </button>
          {details && (
            <UsagePopover now={now} onClose={() => setDetails(false)} />
          )}
        </>
      )}
      <span className="grow" />
      {approvals > 0 && (
        <>
          <Link to="/approvals" className="text-danger hover:underline">
            승인 대기 {approvals}
          </Link>
          <span className="h-3 w-px bg-line" />
        </>
      )}
      <SyncSlot now={now} />
      {divider}
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
