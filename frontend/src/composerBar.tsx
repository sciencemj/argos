import type { ReactNode } from "react";
import {
  type Agent,
  type Channel,
  type PermissionMode,
  useUpdateChannel,
} from "./api";
import { tr, tt } from "./i18n";
import { useIsMobile } from "./ui";

/** The chat composer's bottom bar (v0.2, Orca-style): attach and permission mode on
 * the left; agent, context ring and send on the right. */

const pill =
  "h-8 max-w-full cursor-pointer appearance-none truncate rounded-full border border-line-soft bg-inset pr-7 pl-3 text-[12.5px] font-medium text-text-2 outline-none hover:text-ink focus-visible:border-ink disabled:opacity-50";

function Chevron() {
  return (
    <svg
      aria-hidden="true"
      width="12"
      height="12"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      className="pointer-events-none absolute top-1/2 right-2.5 -translate-y-1/2 text-meta"
    >
      <path d="m6 9 6 6 6-6" />
    </svg>
  );
}

function Select({
  label,
  value,
  onChange,
  disabled,
  title,
  fixed = false,
  children,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  title?: string;
  fixed?: boolean; // keeps its full width on narrow screens
  children: ReactNode;
}) {
  return (
    <span
      className={`relative inline-flex min-w-0 ${fixed ? "shrink-0" : "shrink"}`}
      title={title}
    >
      <select
        aria-label={label}
        className={pill}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
      >
        {children}
      </select>
      <Chevron />
    </span>
  );
}

/** Auto-Edit / Ask for the native harness in this channel (stored on the channel). */
export function PermissionSelect({ channel }: { channel: Channel }) {
  const update = useUpdateChannel();
  const mode: PermissionMode = channel.permission_mode ?? "auto_edit";
  const narrow = useIsMobile(); // phones: "Auto" leaves room for the agent's name
  return (
    <Select
      label={tr("에이전트 실행 권한", "Agent permissions")}
      value={mode}
      fixed
      disabled={update.isPending}
      title={tr(
        "Auto-Edit: 읽기와 작업 폴더 안 수정은 바로, 삭제·명령 실행은 승인 후 · Ask: 모든 쓰기·명령에 승인",
        "Auto-Edit: reads and workspace edits run at once; deletes and commands ask · Ask: every write and command asks",
      )}
      onChange={(value) =>
        update.mutate({
          id: channel.id,
          permission_mode: value as PermissionMode,
        })
      }
    >
      <option value="auto_edit">{narrow ? "Auto" : "Auto-Edit"}</option>
      <option value="ask">Ask</option>
    </Select>
  );
}

/** Which agent answers this message (and, in a thread, from now on). */
export function AgentSelect({
  agents,
  value,
  onChange,
}: {
  agents: Agent[];
  value: string;
  onChange: (name: string) => void;
}) {
  return (
    <Select
      label={tr("답할 에이전트", "Agent")}
      value={value}
      onChange={onChange}
    >
      {agents.map((a) => (
        <option key={a.id} value={a.name} disabled={!a.available}>
          {a.display_name}
          {a.available ? "" : ` ${tr("(사용 불가)", "(unavailable)")}`}
        </option>
      ))}
    </Select>
  );
}

// Rough context windows by backend; a native/local model's is the smallest common one.
const WINDOW: Record<string, number> = {
  claude_code: 200_000,
  codex: 200_000,
  hermes: 128_000,
  native: 32_000,
  ollama: 32_000,
};
// What the agent gets besides the conversation: channel context (Settings.context_limit
// is 4000 characters), the system prompt and the tool schemas.
const SYSTEM_CHARS = 9_000;
const CHARS_PER_TOKEN = 3; // between English (~4) and Korean (~2)

export function contextUsage(
  chars: number,
  backend: string | undefined,
): { tokens: number; window: number; ratio: number } {
  const window = WINDOW[backend ?? ""] ?? 32_000;
  const tokens = Math.round((chars + SYSTEM_CHARS) / CHARS_PER_TOKEN);
  return { tokens, window, ratio: Math.min(1, tokens / window) };
}

/** A ring showing roughly how much of the agent's context this conversation fills. */
export function ContextRing({
  chars,
  backend,
}: {
  chars: number;
  backend: string | undefined;
}) {
  const { tokens, window, ratio } = contextUsage(chars, backend);
  const percent = Math.round(ratio * 100);
  const tone =
    ratio >= 0.95 ? "text-danger" : ratio >= 0.8 ? "text-warn" : "text-meta";
  const radius = 8;
  const length = 2 * Math.PI * radius;
  const label = tt`약 ${tokens.toLocaleString()} / ${window.toLocaleString()} tokens · ${percent}%`;
  return (
    <span
      role="img"
      aria-label={label}
      title={label}
      className={`inline-flex size-8 shrink-0 items-center justify-center max-md:size-6 ${tone}`}
    >
      <svg width="20" height="20" viewBox="0 0 20 20" aria-hidden="true">
        <circle
          cx="10"
          cy="10"
          r={radius}
          fill="none"
          stroke="currentColor"
          strokeOpacity="0.2"
          strokeWidth="2.5"
        />
        <circle
          cx="10"
          cy="10"
          r={radius}
          fill="none"
          stroke="currentColor"
          strokeWidth="2.5"
          strokeLinecap="round"
          strokeDasharray={`${Math.max(ratio * length, ratio > 0 ? 1.5 : 0)} ${length}`}
          transform="rotate(-90 10 10)"
        />
      </svg>
    </span>
  );
}
