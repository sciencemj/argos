import { type FormEvent, useState } from "react";
import { AgentAvatar } from "../agents";
import {
  type Agent,
  agentExportUrl,
  useAgentModels,
  useAgents,
  useAgentTools,
  useChannels,
  useDeleteAgent,
  useImportAgent,
  useSaveAgent,
} from "../api";
import { t, tr, tt } from "../i18n";
import { btn, card, Dialog, ErrorText, field, label } from "../ui";

const BACKENDS = [
  {
    id: "claude_code",
    label: "Claude",
    hint: tr("Claude Code 로그인으로 동작"),
  },
  { id: "codex", label: "Codex", hint: tr("Codex 로그인으로 동작") },
  {
    id: "ollama",
    label: tr("로컬 모델"),
    hint: tr("Ollama 모델, 이 컴퓨터에서만"),
  },
] as const;

const KIND_TEXT = {
  read: tr("읽기"),
  write: tr("쓰기"),
  approval: tr("삭제 (허용해도 항상 사용자 승인)"),
} as const;

/** Custom agents (PLAN Phase 10): "봇 만들기", YAML import/export. */
export function CustomAgentsSection() {
  const agents = useAgents();
  const remove = useDeleteAgent();
  const [editing, setEditing] = useState<Agent | "new" | null>(null);
  const [importing, setImporting] = useState(false);
  const custom = (agents.data ?? []).filter((a) => !a.is_builtin);

  return (
    <section
      aria-label={tr("커스텀 에이전트")}
      className={`${card} flex w-full flex-col gap-4 p-6`}
    >
      <div className="flex items-center gap-2">
        <h2 className="m-0 grow text-[20px] font-light tracking-[-0.02em] text-ink">
          {tr("커스텀 에이전트")}
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
          "역할과 말투(시스템 프롬프트)와 쓸 수 있는 Argos 도구를 정해 나만의 에이전트를 만들어요. 허용하지 않은 도구는 Argos가 막아요. 메시지에 @이름으로 부르거나 채널의 /ask 담당으로 정할 수 있어요.",
        )}
      </p>
      {custom.length === 0 && (
        <p className="m-0 rounded-2xl border border-dashed border-line px-4 py-6 text-center text-[13px] text-text-3">
          {tr("아직 만든 에이전트가 없어요")}
        </p>
      )}
      {custom.map((a) => (
        <div
          key={a.id}
          className="flex items-center gap-3 rounded-2xl border border-line-soft px-4 py-3"
        >
          <AgentAvatar id={a.name} size={28} />
          <span className="flex min-w-0 grow flex-col gap-0.5">
            <span className="text-[13.5px] text-ink">
              {a.display_name}{" "}
              <span className="font-mono text-[12px] text-meta">@{a.name}</span>
            </span>
            <span className="truncate text-[12px] text-text-3">
              {BACKENDS.find((b) => b.id === a.backend)?.label}
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
          <button
            type="button"
            className={`${btn.ghost} shrink-0 whitespace-nowrap`}
            onClick={() => setEditing(a)}
          >
            {tr("수정")}
          </button>
          <a
            className={`${btn.ghost} shrink-0 whitespace-nowrap`}
            href={agentExportUrl(a.name)}
            download
          >
            {tr("내보내기")}
          </a>
          <button
            type="button"
            className={`${btn.ghost} shrink-0 whitespace-nowrap`}
            disabled={remove.isPending}
            onClick={() =>
              confirm(
                tt`@${a.name} 에이전트를 지울까요? 1:1 대화도 함께 지워져요.`,
              ) && remove.mutate(a.name)
            }
          >
            {tr("삭제")}
          </button>
        </div>
      ))}
      <ErrorText error={remove.error} />
      {editing && (
        <AgentDialog
          agent={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
        />
      )}
      <ImportDialog open={importing} onClose={() => setImporting(false)} />
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
  const [name, setName] = useState(agent?.name ?? "");
  const [displayName, setDisplayName] = useState(agent?.display_name ?? "");
  const [avatar, setAvatar] = useState(agent?.avatar ?? "");
  const [backend, setBackend] = useState<string>(
    agent?.backend ?? "claude_code",
  );
  const [model, setModel] = useState(agent?.model ?? "");
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
        name: agent ? undefined : name.trim(),
        display_name: displayName.trim(),
        avatar: avatar.trim() || null,
        backend: backend as Agent["backend"],
        model: model.trim() || null,
        system_prompt: prompt.trim() || null,
        tools: allowed,
        channel_ids: channelIds,
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
              disabled={Boolean(agent)}
              required={!agent}
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
            <select
              className={field}
              value={backend}
              onChange={(e) => {
                setBackend(e.target.value);
                setModel("");
              }}
            >
              {BACKENDS.map((b) => (
                <option key={b.id} value={b.id}>
                  {b.label} — {b.hint}
                </option>
              ))}
            </select>
          </label>
          <ModelField backend={backend} value={model} onChange={setModel} />
        </div>
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
          {(["read", "write", "approval"] as const).map((kind) => (
            <div key={kind} className="flex flex-col gap-1">
              <span className="text-[12px] text-text-3">{KIND_TEXT[kind]}</span>
              <div className="flex flex-wrap gap-1.5">
                {tools.data
                  ?.filter((t) => t.kind === kind)
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
  value,
  onChange,
}: {
  backend: string;
  value: string;
  onChange: (value: string) => void;
}) {
  const models = useAgentModels(backend);
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
        <select
          id="agent-model"
          className={field}
          value={value}
          onChange={(e) => onChange(e.target.value)}
        >
          <option value="">
            {tr("기본값")}
            {byDefault ? ` (${byDefault.label})` : ""}
          </option>
          {list.map((m) => (
            <option key={m.id} value={m.id}>
              {m.label}
              {m.label !== m.id ? ` · ${m.id}` : ""}
            </option>
          ))}
          {value && !known && (
            <option value={value}>
              {value} {tr("(목록에 없음)") + " "}
            </option>
          )}
        </select>
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
