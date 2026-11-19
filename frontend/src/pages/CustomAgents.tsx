import { type FormEvent, useState } from "react";
import { AgentAvatar } from "../agents";
import {
  type Agent,
  agentExportUrl,
  useAgentModels,
  useAgentSettings,
  useAgents,
  useAgentTools,
  useChannels,
  useClassifierSettings,
  useDeleteAgent,
  useImportAgent,
  useProviders,
  useSaveAgent,
  useSaveDefaultAgent,
  useSetAgentModel,
  useUsage,
} from "../api";
import { t, tr, tt } from "../i18n";
import {
  btn,
  ConfirmDialog,
  card,
  Dialog,
  ErrorText,
  field,
  label,
  SelectMenu,
} from "../ui";

const BACKENDS = [
  {
    id: "claude_code",
    label: "Claude",
    hint: tr("Claude Code 로그인으로 동작"),
  },
  { id: "codex", label: "Codex", hint: tr("Codex 로그인으로 동작") },
  {
    id: "ollama",
    label: tr("모델 연결"),
    hint: tr(
      "Ollama·OpenAI 호환·Anthropic·Google(Gemini) 연결의 모델",
      "A model from an Ollama, OpenAI-compatible, Anthropic or Google (Gemini) connection",
    ),
  },
  {
    id: "native",
    label: tr("Argos 하네스", "Argos harness"),
    hint: tr(
      "모델 연결 + Argos 자체 도구 루프 (파일·셸·플러그인, 인라인 승인)",
      "A model connection in Argos's own tool loop (files, shell, plugins, inline approval)",
    ),
  },
] as const;

const KIND_TEXT = {
  read: tr("읽기"),
  write: tr("쓰기"),
  approval: tr("삭제 (허용해도 항상 사용자 승인)"),
} as const;

const BACKEND_TEXT: Record<string, string> = {
  hermes: tr("Hermes 게이트웨이 · 자체 도구와 메모리를 가진 에이전트"),
  claude_code: tr("Claude Code CLI · Argos 도구만 사용 · Claude 사용량을 씀"),
  codex: tr("Codex CLI · Argos 도구만 사용 · Codex 사용량을 씀"),
  ollama: tr("모델 연결 · 인박스 분류와 같은 연결과 모델 사용"),
};

/** Plan usage the status bar reads, per built-in backend (PLAN Phase 9). */
const USAGE_PROVIDER: Record<string, string> = {
  claude_code: "claude",
  codex: "codex",
};

/** The model a built-in agent answers with (Hermes picks its own in its gateway). */
function BuiltinModelPicker({ agent }: { agent: Agent }) {
  // The built-in local agent runs where the inbox classifier runs (Phase 20).
  const classifierSettings = useClassifierSettings();
  const models = useAgentModels(
    agent.backend,
    agent.backend === "ollama"
      ? classifierSettings.data?.provider_id || undefined
      : undefined,
  );
  const save = useSetAgentModel();
  const current = agent.model ?? "";
  const known = models.data?.models ?? [];
  const fallback =
    agent.backend === "ollama"
      ? tr("인박스 분류 모델과 같게")
      : tr("도구 기본값");
  return (
    <div className="flex flex-col gap-1">
      <SelectMenu
        ariaLabel={tt`${agent.display_name} 모델`}
        className={`${field} w-full`}
        value={current}
        disabled={save.isPending}
        onChange={(value) =>
          save.mutate({ name: agent.name, model: value || null })
        }
        options={[
          { value: "", label: fallback },
          ...known.map((m) => ({
            value: m.id,
            label: (
              <>
                {m.label}
                {m.description ? ` · ${m.description}` : ""}
              </>
            ),
          })),
          ...(current && !known.some((m) => m.id === current)
            ? [{ value: current, label: current }]
            : []),
        ]}
      />
      {models.data?.error && (
        <span className="text-[12px] text-meta">{models.data.error}</span>
      )}
      <ErrorText error={save.error} />
    </div>
  );
}

/** Every agent in one list (v0.2 settings): built-in and custom, with the app-wide
 * default (/ask), each built-in's model and plan usage, and "봇 만들기", edit,
 * export and delete for custom ones. */
export function AgentsSection() {
  const agents = useAgents();
  const providers = useProviders();
  const settings = useAgentSettings();
  const saveDefault = useSaveDefaultAgent();
  const usage = useUsage();
  const remove = useDeleteAgent();
  const [editing, setEditing] = useState<Agent | "new" | null>(null);
  const [importing, setImporting] = useState(false);
  const [deleting, setDeleting] = useState<Agent | null>(null);
  const all = agents.data ?? [];
  const builtin = all.filter((a) => a.is_builtin);
  const custom = all.filter((a) => !a.is_builtin);
  const active = settings.data?.default_agent;

  const usageText = (a: Agent) => {
    const id = USAGE_PROVIDER[a.backend];
    const p = id ? usage.data?.providers.find((x) => x.provider === id) : null;
    if (!p) return null;
    return p.windows.length
      ? p.windows
          .map((w) => `${w.name} ${Math.round(w.used_percent)}%`)
          .join(" · ")
      : p.message;
  };

  const defaultControl = (a: Agent) =>
    a.name === active ? (
      <span className="shrink-0 rounded-full bg-inset px-2.5 py-0.5 text-[11.5px] font-medium whitespace-nowrap text-text-2">
        {tr("기본")}
      </span>
    ) : (
      <button
        type="button"
        className={`${btn.ghost} shrink-0 whitespace-nowrap`}
        disabled={saveDefault.isPending || !a.available}
        aria-label={tt`@${a.name}을(를) 기본 에이전트로`}
        onClick={() => saveDefault.mutate(a.name)}
      >
        {tr("기본으로", "Make default")}
      </button>
    );

  const heading = (a: Agent) => (
    <span className="text-[13.5px] text-ink">
      {a.display_name}{" "}
      <span className="font-mono text-[12px] text-meta">@{a.name}</span>
    </span>
  );

  return (
    <section
      aria-label={tr("에이전트")}
      className={`${card} flex w-full flex-col gap-4 p-6`}
    >
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="m-0 grow text-[20px] font-light tracking-[-0.02em] text-ink">
          {tr("에이전트")}
        </h2>
        <button
          type="button"
          className={btn.ghost}
          onClick={() => setImporting(true)}
        >
          {tr("YAML 가져오기")}
        </button>
        <button
          type="button"
          className={btn.outline}
          onClick={() => setEditing("new")}
        >
          {tr("봇 만들기")}
        </button>
      </div>
      <p className="m-0 text-[13px] leading-relaxed text-text-3">
        {tr(
          "기본 에이전트가 /ask를 받아요(채널마다 따로 정할 수도 있어요). 다른 에이전트는 메시지에 @이름으로 불러요. 내장 에이전트의 모델은 코딩 잡에도 같이 써요.",
          "The default agent answers /ask (channels can pick their own). Call any other agent with @name. A built-in agent's model is also used for its coding jobs.",
        )}
      </p>
      <ul className="m-0 flex list-none flex-col gap-2 p-0">
        {builtin.map((a) => {
          const used = usageText(a);
          return (
            <li
              key={a.id}
              className="flex flex-col gap-2.5 rounded-2xl border border-line-soft px-4 py-3"
            >
              <div className="flex items-center gap-3">
                <AgentAvatar id={a.name} size={28} />
                <span className="flex min-w-0 grow flex-col gap-0.5">
                  {heading(a)}
                  <span className="text-[12px] text-text-3">
                    {BACKEND_TEXT[a.backend]}
                  </span>
                  {used && (
                    <span className="text-[12px] text-meta">
                      {tr("사용량", "Usage")} · {used}
                    </span>
                  )}
                  {!a.available && (
                    <span className="text-[12px] text-danger">{a.problem}</span>
                  )}
                </span>
                {defaultControl(a)}
              </div>
              {a.backend !== "hermes" && (
                <div className="pl-10 max-md:pl-0">
                  <BuiltinModelPicker agent={a} />
                </div>
              )}
            </li>
          );
        })}
        {custom.map((a) => (
          <li
            key={a.id}
            className="flex flex-wrap items-center gap-3 rounded-2xl border border-line-soft px-4 py-3"
          >
            <AgentAvatar id={a.name} size={28} />
            <span className="flex min-w-0 grow basis-48 flex-col gap-0.5">
              {heading(a)}
              <span className="truncate text-[12px] text-text-3">
                {BACKENDS.find((b) => b.id === a.backend)?.label}
                {a.provider_id
                  ? ` · ${providers.data?.find((p) => p.id === a.provider_id)?.name ?? ""}`
                  : ""}
                {a.model ? ` · ${a.model}` : ""}{" "}
                {t(
                  `· 도구 ${a.tools?.length ?? 0}개`,
                  `· ${a.tools?.length ?? 0} tools`,
                )}{" "}
                {a.channel_ids.length
                  ? tt` · /ask 채널 ${a.channel_ids.length}개`
                  : ""}
              </span>
              {!a.available && (
                <span className="text-[12px] text-danger">{a.problem}</span>
              )}
            </span>
            <span className="flex shrink-0 flex-wrap items-center gap-1">
              {defaultControl(a)}
              <button
                type="button"
                className={`${btn.ghost} whitespace-nowrap`}
                onClick={() => setEditing(a)}
              >
                {tr("수정")}
              </button>
              <a
                className={`${btn.ghost} whitespace-nowrap`}
                href={agentExportUrl(a.name)}
                download
              >
                {tr("내보내기")}
              </a>
              <button
                type="button"
                className={`${btn.ghost} whitespace-nowrap`}
                disabled={remove.isPending}
                onClick={() => setDeleting(a)}
              >
                {tr("삭제")}
              </button>
            </span>
          </li>
        ))}
      </ul>
      {custom.length === 0 && (
        <p className="m-0 rounded-2xl border border-dashed border-line px-4 py-4 text-center text-[13px] text-text-3">
          {tr(
            "아직 만든 봇이 없어요. 역할·말투와 쓸 도구를 정해 나만의 에이전트를 만들어 보세요.",
            "No bots yet. Pick a role, a voice and the tools it may use to make your own agent.",
          )}
        </p>
      )}
      <ErrorText error={saveDefault.error ?? remove.error} />
      {editing && (
        <AgentDialog
          agent={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
        />
      )}
      <ImportDialog open={importing} onClose={() => setImporting(false)} />
      <ConfirmDialog
        open={deleting !== null}
        title={tr("에이전트 삭제", "Delete agent")}
        message={
          deleting
            ? tt`@${deleting.name} 에이전트를 지울까요? 1:1 대화도 함께 지워져요.`
            : ""
        }
        confirmLabel={tr("삭제")}
        danger
        onConfirm={() => {
          if (deleting) remove.mutate(deleting.name);
          setDeleting(null);
        }}
        onClose={() => setDeleting(null)}
      />
    </section>
  );
}

function AgentDialog({
  agent,
  onClose,
}: {
  agent: Agent | null;
  onClose: () => void;
}) {
  const save = useSaveAgent();
  const tools = useAgentTools();
  const channels = useChannels();
  const providers = useProviders();
  const providerList = providers.data ?? [];
  const defaultProvider = providerList.find((p) => p.is_default);
  const [name, setName] = useState(agent?.name ?? "");
  const [displayName, setDisplayName] = useState(agent?.display_name ?? "");
  const [avatar, setAvatar] = useState(agent?.avatar ?? "");
  const [backend, setBackend] = useState<string>(
    agent?.backend ?? "claude_code",
  );
  const [model, setModel] = useState(agent?.model ?? "");
  const [providerId, setProviderId] = useState<string>(
    agent?.provider_id ?? "",
  );
  const [sendImages, setSendImages] = useState(agent?.send_images ?? false);
  // "" until the list loads: the default provider.
  const chosenProvider =
    providerList.find((p) => p.id === providerId) ?? defaultProvider;
  // Both run on a model connection; the harness also has the workspace/plugin tools.
  const native = backend === "native";
  const local = backend === "ollama" || native;
  const openai = local && chosenProvider?.kind === "openai";
  const [prompt, setPrompt] = useState(agent?.system_prompt ?? "");
  const [allowed, setAllowed] = useState<string[]>(agent?.tools ?? []);
  const [channelIds, setChannelIds] = useState<string[]>(
    agent?.channel_ids ?? [],
  );
  const choosable = (channels.data?.channels ?? []).filter(
    (c) => c.kind !== "system" && c.kind !== "dm",
  );

  const toggle = (list: string[], value: string) =>
    list.includes(value) ? list.filter((v) => v !== value) : [...list, value];

  const submit = (e: FormEvent) => {
    e.preventDefault();
    save.mutate(
      {
        editing: agent?.name,
        // Renaming keeps the agent, its DM and its runs; only the @handle changes.
        name: agent && name.trim() === agent.name ? undefined : name.trim(),
        display_name: displayName.trim(),
        avatar: avatar.trim() || null,
        backend: backend as Agent["backend"],
        model: model.trim() || null,
        system_prompt: prompt.trim() || null,
        tools: allowed,
        channel_ids: channelIds,
        provider_id:
          local && chosenProvider && !chosenProvider.is_default
            ? chosenProvider.id
            : null,
        send_images: openai && sendImages,
      },
      { onSuccess: onClose },
    );
  };

  return (
    <Dialog
      open
      onClose={onClose}
      title={agent ? tt`@${agent.name} 수정` : tr("봇 만들기")}
    >
      <form
        onSubmit={submit}
        className="flex max-h-[70vh] flex-col gap-4 overflow-y-auto pr-1"
      >
        <div className="grid grid-cols-[1fr_1fr_80px] gap-3">
          <label className="flex flex-col gap-1">
            <span className={label}>{tr("@이름")}</span>
            <input
              className={`${field} font-mono`}
              value={name}
              onChange={(e) => setName(e.target.value.toLowerCase())}
              placeholder="tutor"
              required
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className={label}>{tr("표시 이름")}</span>
            <input
              className={field}
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder={tr("과목 튜터")}
              required
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className={label}>{tr("아바타")}</span>
            <input
              className={field}
              value={avatar}
              onChange={(e) => setAvatar(e.target.value)}
              placeholder="🎓"
              maxLength={4}
            />
          </label>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <label className="flex flex-col gap-1">
            <span className={label}>{tr("기반 에이전트")}</span>
            <SelectMenu
              className={field}
              value={backend}
              onChange={(value) => {
                setBackend(value);
                setModel("");
              }}
              options={[
                ...BACKENDS.map((b) => ({
                  value: b.id,
                  label: (
                    <>
                      {b.label} — {b.hint}
                    </>
                  ),
                })),
              ]}
            />
          </label>
          {local ? (
            <label className="flex flex-col gap-1">
              <span className={label}>{tr("모델 연결")}</span>
              <SelectMenu
                className={field}
                value={chosenProvider?.id ?? ""}
                onChange={(value) => {
                  setProviderId(value);
                  setModel("");
                }}
                options={[
                  ...providerList.map((p) => ({ value: p.id, label: p.name })),
                ]}
              />
            </label>
          ) : (
            <ModelField backend={backend} value={model} onChange={setModel} />
          )}
        </div>
        {local && (
          <div className="grid grid-cols-2 items-end gap-3">
            <ModelField
              backend={backend}
              providerId={chosenProvider?.id}
              value={model}
              onChange={setModel}
            />
            {openai && (
              <label className="flex h-10 items-center gap-2 text-[13px] text-text-2">
                <input
                  type="checkbox"
                  checked={sendImages}
                  onChange={(e) => setSendImages(e.target.checked)}
                />
                {tr("이미지 보내기")}
              </label>
            )}
          </div>
        )}
        <label className="flex flex-col gap-1">
          <span className={label}>
            {tr("시스템 프롬프트 (역할·말투·규칙)")}
          </span>
          <textarea
            rows={5}
            className={`${field} h-auto resize-y py-2 text-[13.5px]`}
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder={tr(
              "채널 과목의 볼트 노트를 근거로, 개념을 예시와 함께 짧게 설명하는 튜터다.",
            )}
          />
        </label>
        <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
          <legend className={`${label} mb-1`}>
            {tr("쓸 수 있는 Argos 도구")}
          </legend>
          {native && (
            <p className="m-0 text-[12px] text-text-3">
              {tr(
                "쓰기·명령 실행은 채널의 권한 모드(Auto-Edit / Ask)에 따라 대화 안에서 승인을 받아요.",
                "Writes and commands ask for approval in the chat, following the channel's mode (Auto-Edit / Ask).",
              )}
            </p>
          )}
          {(["read", "write", "approval"] as const).map((kind) => (
            <div key={kind} className="flex flex-col gap-1">
              <span className="text-[12px] text-text-3">{KIND_TEXT[kind]}</span>
              <div className="flex flex-wrap gap-1.5">
                {tools.data
                  ?.filter((t) => t.kind === kind && (native || !t.native_only))
                  .map((t) => (
                    <label
                      key={t.name}
                      title={t.description}
                      className={`flex cursor-pointer items-center gap-1.5 rounded-full border px-2.5 py-1 font-mono text-[11.5px] ${allowed.includes(t.name) ? "border-line bg-inset text-ink" : "border-line-soft text-text-3"}`}
                    >
                      <input
                        type="checkbox"
                        className="accent-[var(--ink)]"
                        checked={allowed.includes(t.name)}
                        onChange={() => setAllowed(toggle(allowed, t.name))}
                      />
                      {t.name}
                      {t.source.startsWith("plugin:") && (
                        <span className="rounded-full bg-inset px-1.5 font-sans text-[10px] text-meta">
                          {tt`플러그인 ${t.source.slice(7)}`}
                        </span>
                      )}
                      {t.native_only && !t.source.startsWith("plugin:") && (
                        <span className="rounded-full bg-inset px-1.5 font-sans text-[10px] text-meta">
                          {tr("하네스", "harness")}
                        </span>
                      )}
                    </label>
                  ))}
              </div>
            </div>
          ))}
        </fieldset>
        <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
          <legend className={`${label} mb-1`}>
            {tr("/ask를 맡을 채널 (선택)")}
          </legend>
          <div className="flex flex-wrap gap-1.5">
            {choosable.map((c) => (
              <label
                key={c.id}
                className={`flex cursor-pointer items-center gap-1.5 rounded-full border px-2.5 py-1 text-[12px] ${channelIds.includes(c.id) ? "border-line bg-inset text-ink" : "border-line-soft text-text-3"}`}
              >
                <input
                  type="checkbox"
                  className="accent-[var(--ink)]"
                  checked={channelIds.includes(c.id)}
                  onChange={() => setChannelIds(toggle(channelIds, c.id))}
                />
                # {c.name}
              </label>
            ))}
          </div>
        </fieldset>
        <ErrorText error={save.error} />
        <div className="flex justify-end gap-2">
          <button type="button" className={btn.ghost} onClick={onClose}>
            {tr("취소")}
          </button>
          <button type="submit" className={btn.cta} disabled={save.isPending}>
            {agent ? tr("저장") : tr("만들기")}
          </button>
        </div>
      </form>
    </Dialog>
  );
}

/** The models the chosen backend lists (Claude Code's /model list, Codex's model list,
 * installed Ollama models), or free text when it cannot list them. */
function ModelField({
  backend,
  providerId,
  value,
  onChange,
}: {
  backend: string;
  providerId?: string;
  value: string;
  onChange: (value: string) => void;
}) {
  const models = useAgentModels(backend, providerId);
  const list = models.data?.models ?? [];
  const byDefault = list.find((m) => m.default);
  const known = list.some((m) => m.id === value);
  const selected = list.find((m) => m.id === value);
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor="agent-model" className={label}>
        {tr("모델")}
      </label>
      {list.length > 0 ? (
        <SelectMenu
          id="agent-model"
          className={field}
          value={value}
          onChange={(value) => onChange(value)}
          options={[
            {
              value: "",
              label: (
                <>
                  {tr("기본값")}
                  {byDefault ? ` (${byDefault.label})` : ""}
                </>
              ),
            },
            ...list.map((m) => ({
              value: m.id,
              label: (
                <>
                  {m.label}
                  {m.label !== m.id ? ` · ${m.id}` : ""}
                </>
              ),
            })),
            ...(value && !known
              ? [
                  {
                    value: value,
                    label: (
                      <>
                        {value} {tr("(목록에 없음)") + " "}
                      </>
                    ),
                  },
                ]
              : []),
          ]}
        />
      ) : (
        <input
          id="agent-model"
          className={`${field} font-mono text-[13px]`}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={
            models.isLoading
              ? tr("모델 목록을 불러오는 중…")
              : tr("비우면 기본값")
          }
        />
      )}
      <span className="text-[11.5px] text-meta">
        {models.isLoading
          ? tr("모델 목록을 불러오는 중…")
          : models.data?.error
            ? models.data.error
            : (selected?.description ?? tt`${list.length}개 모델`)}
      </span>
    </div>
  );
}

function ImportDialog({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const importer = useImportAgent();
  const [text, setText] = useState("");
  return (
    <Dialog open={open} onClose={onClose} title={tr("YAML 가져오기")}>
      <form
        className="flex flex-col gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          importer.mutate(text, {
            onSuccess: () => {
              setText("");
              onClose();
            },
          });
        }}
      >
        <input
          type="file"
          accept=".yaml,.yml,text/yaml"
          aria-label={tr("YAML 파일")}
          className="text-[12.5px] text-text-3"
          onChange={(e) => void e.target.files?.[0]?.text().then(setText)}
        />
        <textarea
          rows={10}
          aria-label={tr("YAML 내용")}
          className={`${field} h-auto resize-y py-2 font-mono text-[12px]`}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={tr(
            "name: tutor\ndisplay_name: 과목 튜터\nbackend: llm\ntools: [search_notes]",
          )}
        />
        <ErrorText error={importer.error} />
        <div className="flex justify-end gap-2">
          <button type="button" className={btn.ghost} onClick={onClose}>
            {tr("취소")}
          </button>
          <button
            type="submit"
            className={btn.cta}
            disabled={!text.trim() || importer.isPending}
          >
            {tr("가져오기")}
          </button>
        </div>
      </form>
    </Dialog>
  );
}
