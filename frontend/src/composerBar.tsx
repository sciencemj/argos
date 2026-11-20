import { useEffect, useRef, useState } from "react";
import { AgentAvatar } from "./agents";
import {
  type Agent,
  type Channel,
  type ChatEffort,
  type ChatModel,
  type PermissionMode,
  useChatModels,
  useChatSettings,
  useSaveChatSettings,
  useUpdateChannel,
} from "./api";
import { tr, tt } from "./i18n";
import { CheckIcon, SearchIcon } from "./icons";
import { SelectMenu, type SelectOption, useIsMobile } from "./ui";

/** The chat composer's bottom bar (v0.2, Orca-style): attach and permission mode on
 * the left; agent, context ring and send on the right. */

const pill =
  "h-8 max-w-full rounded-full border border-line-soft bg-inset px-3 text-[12.5px] font-medium text-text-2 outline-none hover:text-ink focus-visible:border-ink";

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

const EFFORTS: { value: ChatEffort; label: string }[] = [
  { value: "low", label: tr("낮음", "Low") },
  { value: "medium", label: tr("중간", "Medium") },
  { value: "high", label: tr("높음", "High") },
];

/** The models in `list` whose name or connection contains `query`, case-insensitive. */
export function filterModels(list: ChatModel[], query: string): ChatModel[] {
  const key = query.trim().toLowerCase();
  if (!key) return list;
  return list.filter(
    (m) =>
      m.model.toLowerCase().includes(key) ||
      m.provider_name.toLowerCase().includes(key),
  );
}

/** Default chat's model (v0.2): what a message with no @mention runs on. Searchable
 * across every connection, with the reasoning effort below; the pick is the default
 * for all chats (also in Settings → 기본 채팅 모델). */
export function ModelPicker() {
  const chat = useChatSettings();
  const save = useSaveChatSettings();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const models = useChatModels(open);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const outside = (e: PointerEvent) => {
      if (!box.current?.contains(e.target as Node)) setOpen(false);
    };
    const onEscape = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", onEscape);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("keydown", onEscape);
    };
  }, [open]);

  const data = chat.data;
  const model = data?.resolved_model;
  const effort = EFFORTS.find((e) => e.value === data?.effort);
  const shown = filterModels(models.data?.models ?? [], query);
  const isCurrent = (m: ChatModel) =>
    m.model === model && m.provider_name === data?.provider_name;

  return (
    <div ref={box} className="relative min-w-0 shrink">
      <button
        type="button"
        aria-label={tr("채팅 모델", "Chat model")}
        aria-expanded={open}
        title={data?.problem ?? undefined}
        onClick={() => setOpen(!open)}
        className={`${pill} flex min-w-0 items-center gap-1.5 ${data?.problem ? "text-danger" : ""}`}
      >
        <span className="min-w-0 truncate">
          {model ?? tr("모델 고르기", "Pick a model")}
        </span>
        {model && effort && (
          <span className="shrink-0 text-meta">{effort.label}</span>
        )}
        <span aria-hidden className="shrink-0 text-meta">
          ⌄
        </span>
      </button>
      {open && (
        <div
          role="dialog"
          aria-label={tr("모델 고르기", "Pick a model")}
          className="absolute right-0 bottom-10 z-30 flex w-[320px] max-w-[calc(100vw-32px)] flex-col overflow-hidden rounded-2xl border border-line bg-card shadow-lift"
        >
          <label className="flex items-center gap-2 border-b border-line-soft px-3 py-2.5 text-meta">
            <SearchIcon size={15} />
            <input
              // biome-ignore lint/a11y/noAutofocus: the picker opens to search
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={tr("모델 검색…", "Search models…")}
              aria-label={tr("모델 검색", "Search models")}
              className="min-w-0 grow border-0 bg-transparent p-0 text-[13.5px] text-ink outline-none placeholder:text-meta"
            />
          </label>
          <div
            role="listbox"
            aria-label={tr("모델", "Models")}
            className="flex max-h-[280px] flex-col gap-px overflow-y-auto p-1.5"
          >
            {models.isLoading && (
              <p className="m-0 px-3 py-2 text-[12.5px] text-meta">
                {tr("모델을 불러오는 중…", "Loading models…")}
              </p>
            )}
            {shown.map((m) => (
              <button
                key={`${m.provider_id}:${m.model}`}
                type="button"
                role="option"
                aria-selected={isCurrent(m)}
                onClick={() => {
                  save.mutate({ provider_id: m.provider_id, model: m.model });
                  setOpen(false);
                }}
                className="flex cursor-pointer items-center gap-2 rounded-xl px-3 py-2 text-left hover:bg-inset aria-selected:bg-inset"
              >
                <span className="flex min-w-0 grow flex-col">
                  <span className="truncate text-[13.5px] text-ink">
                    {m.model}
                  </span>
                  <span className="truncate text-[11.5px] text-meta">
                    {m.provider_name}
                    {m.remote ? ` · ${tr("클라우드")}` : ""}
                  </span>
                </span>
                {isCurrent(m) && (
                  <span className="shrink-0 text-ink">
                    <CheckIcon size={15} />
                  </span>
                )}
              </button>
            ))}
            {models.data && shown.length === 0 && (
              <p className="m-0 px-3 py-2 text-[12.5px] text-meta">
                {tr("맞는 모델이 없어요", "No matching models")}
              </p>
            )}
            {models.data?.errors.map((e) => (
              <p key={e} className="m-0 px-3 py-1 text-[11.5px] text-meta">
                {e}
              </p>
            ))}
          </div>
          <div className="flex items-center gap-1 border-t border-line-soft px-3 py-2">
            <span className="grow text-[12.5px] text-text-3">
              {tr("추론 수준", "Reasoning")}
            </span>
            {EFFORTS.map((e) => (
              <button
                key={e.value}
                type="button"
                aria-pressed={data?.effort === e.value}
                onClick={() =>
                  save.mutate({
                    effort: data?.effort === e.value ? null : e.value,
                  })
                }
                className="h-7 cursor-pointer rounded-full px-2.5 text-[12.5px] text-text-3 hover:text-ink aria-pressed:bg-inset aria-pressed:font-medium aria-pressed:text-ink"
              >
                {e.label}
              </button>
            ))}
          </div>
          {data?.problem && (
            <p className="m-0 border-t border-line-soft px-3 py-2 text-[12px] text-danger">
              {data.problem}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

/** In place of the model picker when the message calls an agent: its own model. */
export function AgentBadge({ agent }: { agent: Agent }) {
  return (
    <span
      className={`${pill} flex min-w-0 items-center gap-1.5`}
      title={tr(
        "에이전트가 자기 모델로 답해요",
        "The agent answers with its own model",
      )}
    >
      <AgentAvatar id={agent.name} size={18} />
      <span className="truncate">@{agent.name}</span>
    </span>
  );
}
