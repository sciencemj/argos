import { AgentAvatar } from "./agents";
import {
  type Agent,
  type Channel,
  type PermissionMode,
  useUpdateChannel,
} from "./api";
import { tr, tt } from "./i18n";
import { SelectMenu, type SelectOption, useIsMobile } from "./ui";

/** The chat composer's bottom bar (v0.2, Orca-style): attach and permission mode on
 * the left; agent, context ring and send on the right. */

const pill =
  "h-8 max-w-full rounded-full border border-line-soft bg-inset px-3 text-[12.5px] font-medium text-text-2 outline-none hover:text-ink focus-visible:border-ink";

const BACKEND_TEXT: Record<string, string> = {
  claude_code: "Claude Code",
  codex: "Codex",
  hermes: "Hermes",
  ollama: tr("모델 연결", "Model connection"),
  native: tr("Argos 하네스", "Argos harness"),
};

function ShieldMini() {
  return (
    <svg
      width="15"
      height="15"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      aria-hidden="true"
    >
      <path d="M12 3 4 6v6c0 5 3.5 8 8 9 4.5-1 8-4 8-9V6z" />
    </svg>
  );
}

function PencilMini() {
  return (
    <svg
      width="15"
      height="15"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      aria-hidden="true"
    >
      <path d="M4 20h4L19 9l-4-4L4 16z" />
    </svg>
  );
}

/** Auto-Edit / Ask for the native harness in this channel (stored on the channel). */
export function PermissionSelect({ channel }: { channel: Channel }) {
  const update = useUpdateChannel();
  const mode: PermissionMode = channel.permission_mode ?? "auto_edit";
  const narrow = useIsMobile(); // phones: "Auto" leaves room for the agent's name
  const options: SelectOption<PermissionMode>[] = [
    {
      value: "auto_edit",
      label: "Auto-Edit",
      icon: <PencilMini />,
      description: tr(
        "워크스페이스 안의 읽기·파일 편집은 자동으로 허용해요. 삭제·명령 실행은 승인을 받아요.",
        "Reads and file edits in the workspace run on their own; deletes and commands ask first.",
      ),
    },
    {
      value: "ask",
      label: "Ask",
      icon: <ShieldMini />,
      description: tr(
        "모든 편집·명령 전에 승인을 요청합니다.",
        "Asks before every edit and command.",
      ),
    },
  ];
  return (
    <span className="inline-flex min-w-0 shrink-0 items-center gap-1.5">
      <SelectMenu
        ariaLabel={tr("에이전트 실행 권한", "Agent permissions")}
        value={mode}
        options={options}
        placement="top"
        menuWidth={300}
        className={pill}
        disabled={update.isPending}
        renderValue={(o) =>
          o?.value === "auto_edit" && narrow ? "Auto" : o?.label
        }
        onChange={(value) =>
          update.mutate({ id: channel.id, permission_mode: value })
        }
      />
      {update.error && (
        <span
          role="alert"
          title={update.error.message}
          className="max-w-[180px] truncate text-[11.5px] text-danger"
        >
          {tr("권한을 바꾸지 못했어요", "Could not change it")}:{" "}
          {update.error.message}
        </span>
      )}
    </span>
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
  const options: SelectOption[] = agents.map((a) => ({
    value: a.name,
    label: a.display_name,
    text: a.display_name,
    icon: <AgentAvatar id={a.name} size={18} />,
    disabled: !a.available,
    description: a.available
      ? [BACKEND_TEXT[a.backend] ?? a.backend, a.model]
          .filter(Boolean)
          .join(" · ")
      : (a.problem ?? tr("지금은 쓸 수 없어요", "Unavailable right now")),
  }));
  return (
    <SelectMenu
      ariaLabel={tr("답할 에이전트", "Agent")}
      value={value}
      options={options}
      placement="top"
      menuWidth={300}
      className={`${pill} shrink`}
      onChange={onChange}
    />
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
