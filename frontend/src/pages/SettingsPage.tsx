import { useEffect, useRef, useState } from "react";
import { AgentAvatar } from "../agents";
import {
  type Agent,
  type Conflict,
  useAgentModels,
  useAgentSettings,
  useAgents,
  useCalendarChannel,
  useCalendarConflicts,
  useCalendarFeed,
  useChannels,
  useClassifierSettings,
  useConnectICloud,
  useDisconnectICloud,
  useICloud,
  useJobSettings,
  useOllamaModels,
  useRefreshUsage,
  useResolveConflict,
  useRotateCalendarFeed,
  useSaveClassifier,
  useSaveDefaultAgent,
  useSaveJobRoots,
  useSaveVault,
  useSetAgentModel,
  useSyncCalendars,
  useSyncVault,
  useUsage,
  useVaultSettings,
} from "../api";
import { fmt } from "../dates";
import { t, tr, tt } from "../i18n";
import { LanguagePicker } from "../LanguagePicker";
import { btn, card, ErrorText, field, label } from "../ui";
import { CustomAgentsSection } from "./CustomAgents";
import { LmsSection } from "./LmsSettings";
import {
  AppUpdateSection,
  NotifySection,
  OpsSection,
  UninstallSection,
} from "./OpsSettings";
import { ToolsSection } from "./Welcome";

const OFF = "";

const GROUPS = [
  {
    id: "agents",
    title: tr("에이전트"),
    hint: tr("누가 답하고, MCP·스킬, 사용량"),
  },
  {
    id: "capture",
    title: tr("입력과 알림"),
    hint: tr("정리 모델, 알림, 주간 리뷰"),
  },
  { id: "links", title: tr("연결"), hint: tr("옵시디언, 애플 캘린더, LMS") },
  {
    id: "work",
    title: tr("작업과 운영"),
    hint: tr("코딩 잡 폴더, 백업, 자동 실행"),
  },
] as const;

/** Settings, grouped (agents, capture, links, work) with a table of contents that
 * follows the scroll on wide screens; one centred column on narrow ones. */
export function SettingsPage() {
  const [current, setCurrent] = useState<string>(GROUPS[0].id);
  const scroller = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const root = scroller.current;
    if (!root) return;
    const seen = new IntersectionObserver(
      (entries) => {
        const top = entries
          .filter((e) => e.isIntersecting)
          .sort(
            (a, b) => a.boundingClientRect.top - b.boundingClientRect.top,
          )[0];
        if (top) setCurrent(top.target.id);
      },
      { root, rootMargin: "0px 0px -65% 0px" },
    );
    for (const g of GROUPS) {
      const el = document.getElementById(g.id);
      if (el) seen.observe(el);
    }
    return () => seen.disconnect();
  }, []);

  const section = (
    id: (typeof GROUPS)[number]["id"],
    children: React.ReactNode,
  ) => {
    const group = GROUPS.find((g) => g.id === id);
    return (
      <section
        id={id}
        aria-labelledby={`${id}-title`}
        className="flex scroll-mt-8 flex-col gap-4"
      >
        <div className="flex items-baseline gap-3 px-1">
          <h2
            id={`${id}-title`}
            className="m-0 text-[13px] font-semibold tracking-[0.02em] text-text-2"
          >
            {group?.title}
          </h2>
          <span className="text-[12px] text-meta">{group?.hint}</span>
        </div>
        {children}
      </section>
    );
  };

  return (
    <div ref={scroller} className="min-h-0 grow overflow-y-auto">
      <div className="mx-auto flex w-full max-w-[1120px] gap-12 px-9 pt-8 pb-16">
        <nav
          aria-label={tr("설정 목차")}
          className="sticky top-8 hidden h-fit w-48 shrink-0 flex-col gap-1 lg:flex"
        >
          <h1 className="m-0 mb-4 text-[28px] leading-tight font-light tracking-[-0.02em] text-ink">
            {tr("설정")}
          </h1>
          {GROUPS.map((g) => (
            <a
              key={g.id}
              href={`#${g.id}`}
              onClick={(e) => {
                e.preventDefault();
                document
                  .getElementById(g.id)
                  ?.scrollIntoView({ behavior: "smooth" });
                setCurrent(g.id);
              }}
              aria-current={current === g.id ? "true" : undefined}
              className="flex flex-col rounded-xl px-3 py-2 text-[13.5px] text-text-3 hover:text-ink aria-[current=true]:bg-card aria-[current=true]:text-ink aria-[current=true]:shadow-sm"
            >
              {g.title}
              <span className="text-[11.5px] text-meta">{g.hint}</span>
            </a>
          ))}
        </nav>
        <div className="mx-auto flex w-full max-w-[760px] min-w-0 flex-col gap-10">
          <div data-tauri-drag-region="deep" className="flex justify-end">
            <LanguagePicker />
          </div>
          <h1 className="m-0 text-[28px] leading-tight font-light tracking-[-0.02em] text-ink lg:hidden">
            {tr("설정")}
          </h1>
          {section(
            "agents",
            <>
              <AgentSection />
              <ModelSection />
              <ToolsSection />
              <CustomAgentsSection />
              <UsageSection />
            </>,
          )}
          {section(
            "capture",
            <>
              <ClassifierSection />
              <NotifySection />
            </>,
          )}
          {section(
            "links",
            <>
              <VaultSection />
              <ICloudSection />
              <CalendarFeedSection />
              <LmsSection />
            </>,
          )}
          {section(
            "work",
            <>
              <JobRootsSection />
              <OpsSection />
              <AppUpdateSection />
              <UninstallSection />
            </>,
          )}
        </div>
      </div>
    </div>
  );
}

const SOURCE_TEXT = {
  app: tr("여기서 선택함"),
  env: tr(".env 설정"),
  none: "",
} as const;

type TitleMode = "parallel" | "sequential";

const TITLE_MODES: { value: TitleMode; title: string; meta: string }[] = [
  {
    value: "parallel",
    title: tr("동시 처리"),
    meta: tr(
      "두 모델이 메모리에 함께 올라갈 때 (예: tev1:0.8b + gemma4:e2b). 분류와 제목을 한꺼번에 받아 가장 빨라요.",
    ),
  },
  {
    value: "sequential",
    title: tr("순차 처리"),
    meta: tr(
      "메모리가 부족할 때 (예: tev1:4b + gemma4:e2b). 분류 결과를 먼저 보여 주고, 제목은 하나씩 나중에 다듬어요. 자동 적용은 제목이 나온 뒤에 해요.",
    ),
  },
];

/** Picks which installed Ollama model classifies inbox text (PLAN §9). */
export function ClassifierSection() {
  const current = useClassifierSettings();
  const models = useOllamaModels();
  const save = useSaveClassifier();
  const [choice, setChoice] = useState<string | null>(null);
  const [titleChoice, setTitleChoice] = useState<string | null>(null);

  const active = current.data?.enabled ? (current.data.model ?? OFF) : OFF;
  const selected = choice ?? active;
  const list = models.data?.models ?? [];
  const selectedModel = list.find((m) => m.name === selected);
  // Decision models only pick answers: a chat model names tasks and events for them.
  const activeTitle = current.data?.title_model ?? "";
  const selectedTitle = titleChoice ?? activeTitle;
  const chatModels = list.filter((m) => !m.decision);
  const titleModel = chatModels.find((m) => m.name === selectedTitle);
  const [modeChoice, setModeChoice] = useState<TitleMode | null>(null);
  const activeMode: TitleMode = current.data?.title_mode ?? "parallel";
  const selectedMode = modeChoice ?? activeMode;
  const changed =
    selected !== active ||
    selectedTitle !== activeTitle ||
    selectedMode !== activeMode;
  // The active model may be missing from the list (uninstalled since); still show it.
  const missing =
    active !== OFF &&
    models.data?.reachable &&
    !list.some((m) => m.name === active);

  const option = (
    value: string,
    title: string,
    meta?: string,
    remote?: boolean,
  ) => (
    <label
      key={value || "off"}
      className={`flex cursor-pointer items-center gap-3 rounded-2xl border px-4 py-3 ${selected === value ? "border-line bg-inset" : "border-line-soft bg-card"}`}
    >
      <input
        type="radio"
        name="classifier-model"
        value={value}
        checked={selected === value}
        onChange={() => setChoice(value)}
        className="accent-[var(--ink)]"
      />
      <span className="flex grow flex-col gap-0.5">
        <span
          className={
            value
              ? "font-mono text-[13.5px] text-ink"
              : "text-[13.5px] text-ink"
          }
        >
          {title}
        </span>
        {meta && <span className="text-[12px] text-text-3">{meta}</span>}
      </span>
      {remote !== undefined && (
        <span
          className={`rounded-full px-[9px] py-0.5 text-[11.5px] font-medium whitespace-nowrap ${remote ? "bg-danger-bg text-danger" : "bg-inset text-text-2"}`}
        >
          {remote ? tr("클라우드") : tr("로컬")}
        </span>
      )}
      {value === active && (
        <span className="text-[11.5px] whitespace-nowrap text-meta">
          {tr("사용 중")}
        </span>
      )}
    </label>
  );

  return (
    <section
      aria-label={tr("인박스 분류")}
      className={`${card} flex w-full flex-col gap-4 p-6`}
    >
      <div className="flex items-baseline gap-2">
        <h2 className="m-0 grow text-[20px] font-light tracking-[-0.02em] text-ink">
          {tr("인박스 분류")}
        </h2>
        {current.data && (
          <span className="text-[12px] text-meta">
            {current.data.provider}
            {current.data.source !== "none" &&
              ` · ${SOURCE_TEXT[current.data.source]}`}
          </span>
        )}
      </div>
      <p className="m-0 text-[13px] leading-relaxed text-text-3">
        {tr(
          "슬래시 없이 적은 메시지를 할 일·일정으로 정리할 모델을 골라요. 분류하는 동안에도 원문은 인박스에 먼저 저장돼요.",
        )}
      </p>

      {current.data?.provider === "hermes" ? (
        <p className="m-0 text-[13px] text-text-3">
          {tr(
            "Hermes를 쓰는 동안에는 .env의 ARGOS_CLASSIFIER_MODEL로 모델을 정해요.",
          )}
        </p>
      ) : (
        <div className="flex flex-col gap-2">
          {models.isLoading && (
            <p className="m-0 text-[13px] text-meta">
              {tr("Ollama 모델을 불러오는 중…")}
            </p>
          )}
          {models.data && !models.data.reachable && (
            <div className="flex items-center gap-3 rounded-2xl border border-dashed border-line px-4 py-3 text-[13px] text-text-3">
              <span className="grow">{models.data.error}</span>
              <button
                type="button"
                className={btn.outline}
                onClick={() => void models.refetch()}
              >
                {tr("다시 확인")}
              </button>
            </div>
          )}
          {list.map((m) =>
            option(
              m.name,
              m.name,
              [
                m.decision ? tr("판단 모델 · 빠르게 고르기만 해요") : null,
                m.parameter_size,
                m.remote
                  ? tr("입력한 내용이 ollama.com으로 전송돼요")
                  : tr("이 컴퓨터에서만 실행돼요"),
              ]
                .filter(Boolean)
                .join(" · "),
              m.remote,
            ),
          )}
          {missing &&
            option(
              active,
              active,
              tr("Ollama에서 찾을 수 없어요 · 다른 모델을 골라 주세요"),
            )}
          {option(
            OFF,
            tr("분류 끄기"),
            tr("메시지는 인박스에만 쌓이고 직접 정리해요"),
          )}
        </div>
      )}

      {selectedModel?.decision && (
        <div className="flex flex-col gap-2 rounded-2xl border border-line-soft px-4 py-3.5">
          <label
            htmlFor="classifier-title-model"
            className="text-[13.5px] text-ink"
          >
            {tr("제목 모델")}
          </label>
          <p className="m-0 text-[12.5px] leading-relaxed text-text-3">
            {tr(
              "판단 모델은 유형과 채널만 골라요. 할 일·일정의 제목은 여기서 고른 모델이 다듬어요. 고르지 않으면 원문에서 날짜만 뺀 글이 제목이 돼요.",
            )}
          </p>
          <select
            id="classifier-title-model"
            className={`${field} font-mono text-[13px]`}
            value={selectedTitle}
            onChange={(e) => setTitleChoice(e.target.value)}
          >
            <option value="">{tr("원문에서 만들기 (모델 없이)")}</option>
            {chatModels.map((m) => (
              <option key={m.name} value={m.name}>
                {m.name}
                {m.remote ? ` · ${tr("클라우드")}` : ""}
              </option>
            ))}
            {selectedTitle && !titleModel && models.data?.reachable && (
              <option value={selectedTitle}>
                {selectedTitle} · {tr("Ollama에서 찾을 수 없어요")}
              </option>
            )}
          </select>
          {selectedTitle && (
            <fieldset className="m-0 flex flex-col gap-1.5 border-0 p-0 pt-1">
              <legend className="mb-1.5 p-0 text-[12.5px] text-text-2">
                {tr("두 모델을 함께 쓰는 방식")}
              </legend>
              {TITLE_MODES.map((m) => (
                <label
                  key={m.value}
                  className="flex cursor-pointer items-start gap-2.5 text-[13px]"
                >
                  <input
                    type="radio"
                    name="classifier-title-mode"
                    value={m.value}
                    checked={selectedMode === m.value}
                    onChange={() => setModeChoice(m.value)}
                    className="mt-[3px] accent-[var(--ink)]"
                  />
                  <span className="flex flex-col gap-0.5">
                    <span className="text-ink">{m.title}</span>
                    <span className="text-[12px] text-text-3">{m.meta}</span>
                  </span>
                </label>
              ))}
            </fieldset>
          )}
        </div>
      )}

      {(titleModel?.remote &&
        selectedTitle !== activeTitle &&
        selectedModel?.decision) ||
      (selectedModel?.remote && selected !== active) ? (
        <p
          role="alert"
          className="m-0 rounded-xl bg-danger-bg px-3.5 py-2.5 text-[12.5px] text-danger"
        >
          {tr(
            "클라우드 모델은 메시지 내용을 ollama.com 서버로 보내요. 개인 일정이 밖으로 나가도 괜찮을 때만 고르세요.",
          )}
        </p>
      ) : null}
      <ErrorText error={save.error} />
      <div className="flex items-center gap-3">
        <button
          type="button"
          className={btn.cta}
          disabled={
            save.isPending || !changed || current.data?.provider === "hermes"
          }
          onClick={() =>
            save.mutate(
              {
                model: selected || null,
                title_model: selectedModel?.decision
                  ? selectedTitle
                  : undefined,
                title_mode: selectedModel?.decision ? selectedMode : undefined,
              },
              {
                onSuccess: () => {
                  setChoice(null);
                  setTitleChoice(null);
                  setModeChoice(null);
                },
              },
            )
          }
        >
          {tr("저장")}
        </button>
        {save.isSuccess && !changed && (
          <span className="text-[12.5px] text-text-3">
            {tr("저장했어요. 다음 메시지부터 적용돼요.")}
          </span>
        )}
      </div>
    </section>
  );
}

const BACKEND_TEXT: Record<string, string> = {
  hermes: tr("Hermes 게이트웨이 · 자체 도구와 메모리를 가진 에이전트"),
  claude_code: tr("Claude Code CLI · Argos 도구만 사용 · Claude 사용량을 씀"),
  codex: tr("Codex CLI · Argos 도구만 사용 · Codex 사용량을 씀"),
  ollama: tr("Ollama 로컬 모델 · 이 컴퓨터에서만 실행 (인박스 분류 모델 사용)"),
};

/** App-wide default agent: answers /ask where the channel sets none (user decision). */
function ModelRow({ agent }: { agent: Agent }) {
  const models = useAgentModels(agent.backend);
  const save = useSetAgentModel();
  const current = agent.model ?? "";
  const known = models.data?.models ?? [];
  const fallback =
    agent.backend === "ollama"
      ? tr("인박스 분류 모델과 같게")
      : tr("도구 기본값");
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-3">
        <AgentAvatar id={agent.name} size={24} />
        <span className="w-28 shrink-0 text-[13.5px] text-ink">
          {agent.display_name}
        </span>
        <select
          aria-label={tt`${agent.display_name} 모델`}
          className={`${field} grow`}
          value={current}
          disabled={save.isPending}
          onChange={(e) =>
            save.mutate({ name: agent.name, model: e.target.value || null })
          }
        >
          <option value="">{fallback}</option>
          {known.map((m) => (
            <option key={m.id} value={m.id}>
              {m.label}
              {m.description ? ` · ${m.description}` : ""}
            </option>
          ))}
          {current && !known.some((m) => m.id === current) && (
            <option value={current}>{current}</option>
          )}
        </select>
      </div>
      {models.data?.error && (
        <span className="pl-9 text-[12px] text-meta">{models.data.error}</span>
      )}
      <ErrorText error={save.error} />
    </div>
  );
}

/** Models for the built-in agents (Hermes picks its own in its gateway). */
export function ModelSection() {
  const agents = useAgents();
  const builtin = (agents.data ?? []).filter(
    (a) => a.is_builtin && a.backend !== "hermes",
  );
  return (
    <section
      aria-label={tr("에이전트 모델")}
      className={`${card} flex w-full flex-col gap-4 p-6`}
    >
      <h2 className="m-0 text-[20px] font-light tracking-[-0.02em] text-ink">
        {tr("에이전트 모델")}
      </h2>
      <p className="m-0 text-[13px] leading-relaxed text-text-3">
        {tr(
          "기본 에이전트가 답할 때 쓰는 모델이에요. 목록은 각 도구가 알려 주는 것이고, 코딩 잡에도 같은 모델을 써요. Hermes 모델은 Hermes 게이트웨이 설정에서 정해요.",
        )}
      </p>
      <div className="flex flex-col gap-3">
        {builtin.map((a) => (
          <ModelRow key={a.id} agent={a} />
        ))}
      </div>
    </section>
  );
}

export function AgentSection() {
  const agents = useAgents();
  const current = useAgentSettings();
  const save = useSaveDefaultAgent();
  const [choice, setChoice] = useState<string | null>(null);
  const active = current.data?.default_agent;
  const selected = choice ?? active;

  return (
    <section
      aria-label={tr("기본 에이전트")}
      className={`${card} flex w-full flex-col gap-4 p-6`}
    >
      <h2 className="m-0 text-[20px] font-light tracking-[-0.02em] text-ink">
        {tr("기본 에이전트")}
      </h2>
      <p className="m-0 text-[13px] leading-relaxed text-text-3">
        {tr(
          "/ask를 받을 에이전트예요. 채널 설정에서 채널마다 따로 정할 수도 있어요. 다른 에이전트는 메시지에 @이름으로 불러요.",
        )}
      </p>
      <div className="flex flex-col gap-2">
        {agents.data?.map((a) => (
          <label
            key={a.id}
            className={`flex cursor-pointer items-center gap-3 rounded-2xl border px-4 py-3 ${selected === a.name ? "border-line bg-inset" : "border-line-soft bg-card"}`}
          >
            <input
              type="radio"
              name="default-agent"
              value={a.name}
              checked={selected === a.name}
              onChange={() => setChoice(a.name)}
              className="accent-[var(--ink)]"
            />
            <AgentAvatar id={a.name} size={28} />
            <span className="flex grow flex-col gap-0.5">
              <span className="text-[13.5px] text-ink">
                {a.display_name}{" "}
                <span className="font-mono text-[12px] text-meta">
                  @{a.name}
                </span>
              </span>
              <span className="text-[12px] text-text-3">
                {a.is_builtin ? BACKEND_TEXT[a.backend] : tr("커스텀 에이전트")}
              </span>
              {!a.available && (
                <span className="text-[12px] text-danger">{a.problem}</span>
              )}
            </span>
            {a.name === active && (
              <span className="shrink-0 text-[11.5px] whitespace-nowrap text-meta">
                {tr("사용 중")}
              </span>
            )}
          </label>
        ))}
      </div>
      <ErrorText error={save.error} />
      <div>
        <button
          type="button"
          className={btn.cta}
          disabled={!selected || selected === active || save.isPending}
          onClick={() =>
            selected &&
            save.mutate(selected, { onSuccess: () => setChoice(null) })
          }
        >
          {tr("저장")}
        </button>
      </div>
    </section>
  );
}

/** Folders coding jobs may write in (PLAN §8.1 allowlist), managed by the user. */
function JobRootsSection() {
  const current = useJobSettings();
  const save = useSaveJobRoots();
  const [draft, setDraft] = useState("");
  const roots = current.data?.roots ?? [];

  const add = () => {
    const path = draft.trim();
    if (!path) return;
    save.mutate([...roots, path], { onSuccess: () => setDraft("") });
  };

  return (
    <section
      aria-label={tr("코딩 잡 작업 디렉터리")}
      className={`${card} flex w-full flex-col gap-4 p-6`}
    >
      <h2 className="m-0 text-[20px] font-light tracking-[-0.02em] text-ink">
        {tr("코딩 잡 작업 디렉터리")}
      </h2>
      <p className="m-0 text-[13px] leading-relaxed text-text-3">
        {tr(
          "Claude·Codex 잡은 이 폴더 안에서만 파일을 만들고 고쳐요. 폴더를 정하지 않은 잡은 첫 번째 폴더 아래에 새 폴더를 만들어요.",
        )}
      </p>
      <ul className="m-0 flex list-none flex-col gap-2 p-0">
        {roots.map((root, i) => (
          <li
            key={root}
            className="flex items-center gap-3 rounded-2xl border border-line-soft bg-card px-4 py-3"
          >
            <span className="grow font-mono text-[12.5px] break-all text-ink">
              {root}
            </span>
            {i === 0 && (
              <span className="text-[11.5px] whitespace-nowrap text-meta">
                {tr("기본")}
              </span>
            )}
            <button
              type="button"
              className={`${btn.ghost} shrink-0 whitespace-nowrap`}
              aria-label={tt`${root} 삭제`}
              disabled={save.isPending || roots.length === 1}
              onClick={() => save.mutate(roots.filter((r) => r !== root))}
            >
              {tr("삭제")}
            </button>
          </li>
        ))}
      </ul>
      <form
        className="flex items-end gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          add();
        }}
      >
        <label className="flex grow flex-col gap-1">
          <span className={label}>{tr("폴더 추가")}</span>
          <input
            className={`${field} font-mono text-[13px]`}
            placeholder="~/Desktop/SCHOOL"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
          />
        </label>
        <button
          type="submit"
          className={btn.outline}
          disabled={save.isPending || !draft.trim()}
        >
          {tr("추가")}
        </button>
      </form>
      <ErrorText error={save.error} />
    </section>
  );
}

/** ICS feed URL for Apple Calendar to subscribe to (PLAN 7a, one-way). */
function CalendarFeedSection() {
  const feed = useCalendarFeed();
  const rotate = useRotateCalendarFeed();
  const [copied, setCopied] = useState(false);
  const url = feed.data
    ? (feed.data.url ?? `${location.origin}${feed.data.path}`)
    : "";

  return (
    <section
      aria-label={tr("애플 캘린더 구독")}
      className={`${card} flex w-full flex-col gap-4 p-6`}
    >
      <h2 className="m-0 text-[20px] font-light tracking-[-0.02em] text-ink">
        {tr("애플 캘린더 구독")}
      </h2>
      <p className="m-0 text-[13px] leading-relaxed text-text-3">
        {tr(
          'Argos의 일정과 마감(완료하지 않은 할 일)을 캘린더 앱에서 볼 수 있어요. 캘린더 앱에서 "구독 캘린더 추가"에 아래 주소를 넣으면 10분마다 새로 가져와요. 읽기 전용이라 캘린더 앱에서 고친 내용은 Argos에 반영되지 않아요.',
        )}
      </p>
      <div className="flex items-center gap-3 rounded-2xl border border-line-soft bg-card px-4 py-3">
        <span className="grow font-mono text-[12px] break-all text-ink">
          {url || "…"}
        </span>
        <button
          type="button"
          className={`${btn.outline} shrink-0`}
          disabled={!url}
          onClick={() =>
            void navigator.clipboard.writeText(url).then(() => setCopied(true))
          }
        >
          {copied ? tr("복사했어요") : tr("복사")}
        </button>
      </div>
      <p className="m-0 text-[12px] leading-relaxed text-meta">
        {tr(
          "주소를 아는 사람은 누구나 일정을 볼 수 있어요. Argos는 이 Mac에서만 열리므로 이 Mac의 캘린더 앱에서 구독하세요. 주소가 새어 나갔다면 새 주소로 바꾸세요.",
        )}
      </p>
      <ErrorText error={rotate.error} />
      <div>
        <button
          type="button"
          className={btn.ghost}
          disabled={rotate.isPending}
          onClick={() => {
            if (
              confirm(
                tr(
                  "새 주소로 바꿀까요? 지금 주소로 구독한 캘린더는 더 이상 갱신되지 않아요.",
                ),
              )
            ) {
              rotate.mutate(undefined, { onSuccess: () => setCopied(false) });
            }
          }}
        >
          {tr("새 주소로 바꾸기")}
        </button>
      </div>
    </section>
  );
}

const RESULT_TEXT: [string, string][] = [
  ["created", tr("가져옴")],
  ["updated", tr("바뀜")],
  ["pushed", tr("보냄")],
  ["deleted", tr("지움")],
  ["conflicts", tr("충돌")],
];

/** Two-way iCloud calendar sync (PLAN 7b): connect, calendars → channels, conflicts. */
function ICloudSection() {
  const icloud = useICloud();
  const connect = useConnectICloud();
  const disconnect = useDisconnectICloud();
  const sync = useSyncCalendars();
  const setChannel = useCalendarChannel();
  const channels = useChannels();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const data = icloud.data;
  const status = data?.status;
  const running = Boolean(status?.running) || sync.isPending;
  const choices = (channels.data?.channels ?? []).filter(
    (c) => c.kind !== "system" && c.kind !== "dm",
  );
  const result = status?.last_result
    ? RESULT_TEXT.filter(([key]) => status.last_result?.[key])
        .map(([key, text]) => `${text} ${status.last_result?.[key]}`)
        .join(" · ") || tr("바뀐 것 없음")
    : null;

  return (
    <section
      aria-label={tr("iCloud 캘린더")}
      className={`${card} flex w-full flex-col gap-4 p-6`}
    >
      <div className="flex items-baseline gap-2">
        <h2 className="m-0 grow text-[20px] font-light tracking-[-0.02em] text-ink">
          {tr("iCloud 캘린더")}
        </h2>
        {data?.connected && (
          <span className="text-[12px] text-meta">
            {data.username} {tr("· 연결됨") + " "}
          </span>
        )}
      </div>
      <p className="m-0 text-[13px] leading-relaxed text-text-3">
        {t(
          `모든 캘린더의 일정을 Argos로 가져오고, Argos에서 만든 일정은 "${data?.write_calendar ?? "Argos"}" 캘린더에만 써요.`,
          `Argos imports events from all calendars and writes new events only to the “${data?.write_calendar ?? "Argos"}” calendar.`,
        )}
        {data && data.poll_minutes > 0
          ? tt` ${data.poll_minutes}분마다 자동으로 맞춰요.`
          : ""}{" "}
        {tr("다른 캘린더의 일정은 여기서 보기만 하고 캘린더 앱에서 고쳐요.")}
      </p>

      {data && !data.connected && (
        <form
          className="flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            connect.mutate(
              { username, password },
              { onSuccess: () => setPassword("") },
            );
          }}
        >
          <label className="flex flex-col gap-1">
            <span className={label}>Apple ID</span>
            <input
              className={field}
              type="email"
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className={label}>{tr("앱 전용 암호")}</span>
            <input
              className={`${field} font-mono`}
              type="password"
              autoComplete="off"
              placeholder="xxxx-xxxx-xxxx-xxxx"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </label>
          <p className="m-0 text-[12px] leading-relaxed text-meta">
            {t("애플 ID 암호가 아니라", "Use an app-specific password from")}{" "}
            <a
              href="https://account.apple.com/account/manage"
              target="_blank"
              rel="noreferrer"
              className="underline"
            >
              {tr("애플 계정 관리")}
            </a>
            {t(
              '의 "앱 전용 암호"에서 만든 암호를 넣어 주세요. 암호는 이 Mac의 키체인에만 저장돼요.',
              ", not your Apple ID password. Argos stores it only in this Mac's Keychain.",
            )}
          </p>
          <ErrorText error={connect.error} />
          <div>
            <button
              type="submit"
              className={btn.cta}
              disabled={connect.isPending}
            >
              {connect.isPending ? tr("확인하는 중…") : tr("연결")}
            </button>
          </div>
        </form>
      )}

      {data?.connected && (
        <>
          <div className="flex flex-wrap items-center gap-3 rounded-2xl bg-page px-4 py-3 text-[13px]">
            <span className="grow text-text-2">
              {running
                ? tr("동기화하는 중…")
                : status?.last_sync_at
                  ? tt`마지막 동기화 ${fmt(status.last_sync_at, "M/d HH:mm")}${result ? ` · ${result}` : ""}`
                  : tr("아직 동기화하지 않았어요")}
            </span>
            <button
              type="button"
              className={btn.outline}
              disabled={running}
              onClick={() => sync.mutate(undefined)}
            >
              {tr("지금 동기화")}
            </button>
          </div>
          {status?.last_error && !running && (
            <p
              role="alert"
              className="m-0 rounded-xl bg-danger-bg px-3.5 py-2.5 text-[12.5px] text-danger"
            >
              {status.last_error}
            </p>
          )}
          {data.calendars.length > 0 && (
            <div className="flex flex-col gap-2">
              <span className={label}>{tr("캘린더 → 채널")}</span>
              {data.calendars.map((c) => (
                <div
                  key={c.url}
                  className="flex items-center gap-3 rounded-2xl border border-line-soft bg-card px-4 py-2.5"
                >
                  <span className="min-w-0 grow truncate text-[13.5px] text-ink">
                    {c.name}
                  </span>
                  <span className="text-[11.5px] whitespace-nowrap text-meta">
                    {c.writable ? tr("Argos가 씀") : tr("읽기 전용")}
                  </span>
                  <select
                    aria-label={tt`${c.name} 일정을 넣을 채널`}
                    className="h-9 w-44 shrink-0 cursor-pointer border-0 border-b border-line bg-transparent text-[13.5px] text-text outline-none focus:border-ink"
                    value={c.channel_id ?? ""}
                    disabled={setChannel.isPending}
                    onChange={(e) =>
                      setChannel.mutate({
                        calendar_url: c.url,
                        channel_id: e.target.value || null,
                      })
                    }
                  >
                    <option value="">{tr("내 공간 (기본)")}</option>
                    {choices
                      .filter((ch) => ch.kind !== "personal")
                      .map((ch) => (
                        <option key={ch.id} value={ch.id}>
                          # {ch.name}
                        </option>
                      ))}
                  </select>
                </div>
              ))}
            </div>
          )}
          <ErrorText
            error={sync.error ?? setChannel.error ?? disconnect.error}
          />
          <ConflictList />
          <div>
            <button
              type="button"
              className={btn.ghost}
              onClick={() => {
                if (
                  confirm(
                    tr(
                      "iCloud 연결을 끊을까요? 키체인의 암호를 지우고, 가져온 일정은 Argos에 남아요.",
                    ),
                  )
                ) {
                  disconnect.mutate(undefined);
                }
              }}
            >
              {tr("연결 끊기")}
            </button>
          </div>
        </>
      )}
    </section>
  );
}

function describeTimes(e: {
  starts_at?: string | null;
  ends_at?: string | null;
  start_date?: string | null;
  end_date?: string | null;
}) {
  if (e.start_date) return tt`${fmt(e.start_date, "M/d (EEE)")} 종일`;
  if (!e.starts_at) return "";
  return `${fmt(e.starts_at, "M/d (EEE) HH:mm")}${e.ends_at ? ` – ${fmt(e.ends_at, "HH:mm")}` : ""}`;
}

/** Both sides changed the same event: nothing is merged until the user picks (7b). */
function ConflictList() {
  const conflicts = useCalendarConflicts();
  const resolve = useResolveConflict();
  if (!conflicts.data?.length) return null;
  const side = (title: string, e: Conflict["event"] | Conflict["remote"]) => (
    <div className="flex min-w-0 flex-1 flex-col gap-0.5 rounded-xl bg-page px-3 py-2">
      <span className={label}>{title}</span>
      <span className="text-[13px] text-ink">{String(e.title ?? "")}</span>
      <span className="font-mono text-[11.5px] text-text-3">
        {describeTimes(e as Parameters<typeof describeTimes>[0])}
      </span>
    </div>
  );
  return (
    <div id="calendar-conflicts" className="flex flex-col gap-2">
      <span className={label}>
        {t(
          `충돌 ${conflicts.data.length}건`,
          `${conflicts.data.length} calendar conflicts`,
        )}
      </span>
      {conflicts.data.map((c) => (
        <div
          key={c.id}
          className="flex flex-col gap-2.5 rounded-2xl border border-danger-line px-4 py-3"
        >
          <span className="text-[12.5px] text-text-2">
            {t(
              `앱과 "${c.calendar_name}" 캘린더에서 둘 다 고쳤어요. 어느 쪽을 남길까요?`,
              `This event was edited in both Argos and the “${c.calendar_name}” calendar. Which version would you like to keep?`,
            )}
          </span>
          <div className="flex gap-2">
            {side("Argos", c.event)}
            {side(tr("캘린더"), c.remote)}
          </div>
          <div className="flex gap-2">
            <button
              type="button"
              className={btn.outline}
              disabled={resolve.isPending}
              onClick={() => resolve.mutate({ id: c.id, keep: "app" })}
            >
              {tr("Argos 버전 남기기")}
            </button>
            <button
              type="button"
              className={btn.outline}
              disabled={resolve.isPending}
              onClick={() => resolve.mutate({ id: c.id, keep: "calendar" })}
            >
              {tr("캘린더 버전 남기기")}
            </button>
          </div>
        </div>
      ))}
      <ErrorText error={resolve.error} />
    </div>
  );
}

const TASK_TEXT: [string, string][] = [
  ["imported", tr("가져온 할 일")],
  ["checked_in_argos", tr("노트에 체크")],
  ["checked_in_note", tr("앱에 반영")],
  ["updated", tr("바뀐 할 일")],
  ["unlinked", tr("연결 해제")],
];

/** Obsidian vault (PLAN Phase 8): which vault, where daily notes live, what happened. */
export function VaultSection() {
  const vault = useVaultSettings();
  const save = useSaveVault();
  const sync = useSyncVault();
  const data = vault.data;
  const [path, setPath] = useState<string | null>(null);
  const [daily, setDaily] = useState<string | null>(null);
  const status = data?.status;
  const running = Boolean(status?.running) || sync.isPending;
  const pathValue = path ?? data?.path ?? "";
  const dailyValue =
    daily ??
    (data?.daily_folder !== data?.daily_folder_detected
      ? data?.daily_folder
      : "") ??
    "";
  const tasks = status?.last_tasks
    ? TASK_TEXT.filter(([key]) => status.last_tasks?.[key])
        .map(([key, text]) => `${text} ${status.last_tasks?.[key]}`)
        .join(" · ")
    : "";

  return (
    <section
      aria-label={tr("옵시디언 볼트")}
      className={`${card} flex w-full flex-col gap-4 p-6`}
    >
      <h2 className="m-0 text-[20px] font-light tracking-[-0.02em] text-ink">
        {tr("옵시디언 볼트")}
      </h2>
      <p className="m-0 text-[13px] leading-relaxed text-text-3">
        {tr(
          "볼트의 노트를 검색하고 읽을 수 있어요. 채널 설정에서 폴더를 연결하면 그 폴더 노트의 체크박스 할 일이 칸반으로 오고, 체크 표시가 양쪽에 반영돼요. 최근 데일리 노트의 할 일은 내 공간으로 와요.",
        )}
      </p>
      <form
        className="flex flex-col gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate(
            { path: pathValue || null, daily_folder: dailyValue || null },
            {
              onSuccess: () => {
                setPath(null);
                setDaily(null);
              },
            },
          );
        }}
      >
        <label className="flex flex-col gap-1">
          <span className={label}>{tr("볼트 폴더")}</span>
          <input
            className={`${field} font-mono text-[13px]`}
            value={pathValue}
            onChange={(e) => setPath(e.target.value)}
            list="known-vaults"
            placeholder="/Users/…/My Vault"
          />
          <datalist id="known-vaults">
            {data?.detected.map((p) => (
              <option key={p} value={p} />
            ))}
          </datalist>
        </label>
        {data && data.detected.length > 0 && !data.path && (
          <div className="flex flex-wrap items-center gap-2 text-[12.5px] text-text-3">
            {tr("옵시디언에서 찾은 볼트:") + " "}
            {data.detected.map((p) => (
              <button
                key={p}
                type="button"
                className={`${btn.outline} h-8 text-[12.5px]`}
                onClick={() => setPath(p)}
              >
                {p.split("/").pop()}
              </button>
            ))}
          </div>
        )}
        <label className="flex flex-col gap-1">
          <span className={label}>{tr("데일리 노트 폴더")}</span>
          <input
            className={`${field} font-mono text-[13px]`}
            value={dailyValue}
            onChange={(e) => setDaily(e.target.value)}
            placeholder={
              data?.daily_folder_detected
                ? tt`${data.daily_folder_detected} (볼트 설정에서 찾음)`
                : tr("없음")
            }
          />
          {data && (
            <span className="text-[11.5px] text-meta">
              {t(
                `최근 ${data.daily_days}일 데일리 노트의 열린 할 일만 가져와요.`,
                `Import open tasks from the last ${data.daily_days} days of daily notes.`,
              )}
            </span>
          )}
        </label>
        <p className="m-0 rounded-xl bg-inset px-3.5 py-2.5 text-[12.5px] leading-relaxed text-text-2">
          {tr("할 일을 가져오면 노트의 그 줄 끝에") + " "}
          <code>^argos-…</code>{" "}
          {tr(
            "표시를 붙여 다시 찾아요. 그 표시와 체크 표시 말고는 노트를 바꾸지 않고, 바꾸기 전에 앱 데이터 폴더에 백업을 남겨요. 새 노트는 공부 노트를 저장할 때만 만들어요.",
          ) + " "}
        </p>
        <ErrorText error={save.error} />
        <div className="flex gap-2">
          <button
            type="submit"
            className={btn.cta}
            disabled={save.isPending || (path === null && daily === null)}
          >
            {tr("저장")}
          </button>
          {data?.path && (
            <button
              type="button"
              className={btn.ghost}
              onClick={() => {
                if (
                  confirm(
                    tr("볼트 연결을 끊을까요? 노트 파일은 그대로 남아요."),
                  )
                ) {
                  save.mutate({ path: null, daily_folder: null });
                }
              }}
            >
              {tr("연결 끊기")}
            </button>
          )}
        </div>
      </form>
      {data?.path && status && (
        <>
          <div className="flex flex-wrap items-center gap-3 rounded-2xl bg-page px-4 py-3 text-[13px]">
            <span className="grow text-text-2">
              {running
                ? tr("노트를 읽는 중…")
                : status.last_run_at
                  ? tt`노트 ${status.notes}개 · ${fmt(status.last_run_at, "M/d HH:mm")}${tasks ? ` · ${tasks}` : ""}`
                  : tr("아직 읽지 않았어요")}
            </span>
            <button
              type="button"
              className={btn.outline}
              disabled={running}
              onClick={() => sync.mutate(undefined)}
            >
              {tr("다시 읽기")}
            </button>
          </div>
          {status.last_error && !running && (
            <p
              role="alert"
              className="m-0 rounded-xl bg-danger-bg px-3.5 py-2.5 text-[12.5px] text-danger"
            >
              {status.last_error}
            </p>
          )}
          {status.warnings.length > 0 && (
            <ul className="m-0 flex list-none flex-col gap-1 p-0 text-[12px] text-text-3">
              {status.warnings.slice(0, 5).map((w) => (
                <li key={w}>· {w}</li>
              ))}
            </ul>
          )}
          <ErrorText error={sync.error} />
        </>
      )}
    </section>
  );
}

/** Plan usage in the status bar (PLAN Phase 9): connect Claude Code, see Codex. */
function UsageSection() {
  const usage = useUsage();
  const refresh = useRefreshUsage();
  const data = usage.data;
  const row = (name: string, title: string, how: string) => {
    const p = data?.providers.find((x) => x.provider === name);
    const values = p?.windows.length
      ? p.windows
          .map((w) => `${w.name} ${Math.round(w.used_percent)}%`)
          .join(" · ")
      : (p?.message ?? "");
    return (
      <div className="flex flex-col gap-1 rounded-2xl border border-line-soft px-4 py-3">
        <div className="flex items-baseline gap-2">
          <span className="grow text-[13.5px] text-ink">{title}</span>
          <span
            className={`text-right text-[12px] ${p?.state === "error" ? "text-danger" : "text-text-3"}`}
          >
            {values}
          </span>
        </div>
        <span className="text-[12px] leading-relaxed text-meta">{how}</span>
      </div>
    );
  };
  return (
    <section
      id="usage"
      aria-label={tr("에이전트 사용량")}
      className={`${card} flex w-full flex-col gap-4 p-6`}
    >
      <h2 className="m-0 text-[20px] font-light tracking-[-0.02em] text-ink">
        {tr("에이전트 사용량")}
      </h2>
      <p className="m-0 text-[13px] leading-relaxed text-text-3">
        {tr(
          "아래 상태줄에 Claude·Codex 요금제의 5시간·주간 사용량을 보여줘요. 몇 분마다 새로 읽고, 90%를 넘은 에이전트를 부르면 스레드에 알려줘요.",
        )}
      </p>
      {row(
        "claude",
        "Claude",
        tr(
          "Claude Code에 로그인한 계정으로 Claude의 사용량 정보를 읽어요(Claude Code의 /usage와 같은 정보). 처음 한 번 macOS가 키체인 접근을 물어볼 수 있어요. 로그인 정보는 저장하지 않아요.",
        ),
      )}
      {row("codex", "Codex", tr("로그인한 Codex CLI에게 사용량을 물어봐요."))}
      <div>
        <button
          type="button"
          className={btn.ghost}
          disabled={refresh.isPending}
          onClick={() => refresh.mutate()}
        >
          {refresh.isPending ? tr("읽는 중…") : tr("지금 새로 읽기")}
        </button>
      </div>
    </section>
  );
}
