import { useEffect, useState } from "react";
import { useLocation, useSearchParams } from "react-router";
import {
  type Conflict,
  useCalendarChannel,
  useCalendarConflicts,
  useCalendarFeed,
  useCalendarOptions,
  useChannels,
  useClassifierSettings,
  useConnectICloud,
  useDisconnectICloud,
  useICloud,
  useJobSettings,
  useOllamaModels,
  useProviders,
  useRefreshUsage,
  useResolveConflict,
  useRotateCalendarFeed,
  useSaveClassifier,
  useSaveJobRoots,
  useSaveVault,
  useSyncCalendars,
  useSyncVault,
  useUsage,
  useVaultSettings,
} from "../api";
import { BASE } from "../base";
import { fmt } from "../dates";
import { t, tr, tt } from "../i18n";
import { LanguagePicker, MascotPicker, ThemePicker } from "../LanguagePicker";
import { btn, card, ErrorText, field, label, SelectMenu } from "../ui";
import { ChatModelSection } from "./ChatModelSettings";
import { AgentsSection } from "./CustomAgents";
import { LmsSection } from "./LmsSettings";
import {
  AppUpdateSection,
  NotifySection,
  OpsSection,
  UninstallSection,
} from "./OpsSettings";
import { ProviderSection } from "./ProviderSettings";
import { RemoteSection } from "./RemoteSettings";
import { ToolsSection } from "./ToolIntegrations";

const OFF = "";

const TABS = [
  {
    id: "agents",
    title: tr("에이전트 · 모델", "Agents · Models"),
    hint: tr(
      "누가 답하고 어떤 모델에 연결하는지",
      "Who answers and which models they use",
    ),
  },
  {
    id: "integrations",
    title: tr("일정 · 자료 · LMS", "Calendar · Notes · LMS"),
    hint: tr(
      "알림, 옵시디언, 애플 캘린더, LMS, 폰",
      "Alerts, Obsidian, Apple Calendar, LMS, phone",
    ),
  },
  {
    id: "system",
    title: tr("워크스페이스 · 시스템", "Workspace · System"),
    hint: tr(
      "화면·언어, 코딩 잡 폴더, 백업, 자동 실행, 업데이트",
      "Display, language, job folders, backups, start at login, updates",
    ),
  },
] as const;

export type SettingsTab = (typeof TABS)[number]["id"];

/** Old in-page anchors (the four section groups, deep links) → the tab holding them. */
const HASH_TAB: Record<string, SettingsTab> = {
  agents: "agents",
  capture: "agents",
  usage: "agents",
  tools: "agents",
  links: "integrations",
  "calendar-conflicts": "integrations",
  work: "system",
};

/** `?tab=` wins; else an old `#anchor` picks its tab; else agents. */
export function settingsTab(search: string, hash: string): SettingsTab {
  const asked = new URLSearchParams(search).get("tab");
  const tab = TABS.find((t) => t.id === asked);
  if (tab) return tab.id;
  return HASH_TAB[hash.replace(/^#/, "")] ?? "agents";
}

/** The tab ArrowLeft/ArrowRight (wrapping), Home or End moves to; null for other keys. */
export function tabAfterKey(
  current: SettingsTab,
  key: string,
): SettingsTab | null {
  const at = TABS.findIndex((t) => t.id === current);
  const n = TABS.length;
  const index =
    key === "ArrowRight"
      ? (at + 1) % n
      : key === "ArrowLeft"
        ? (at - 1 + n) % n
        : key === "Home"
          ? 0
          : key === "End"
            ? n - 1
            : null;
  return index === null ? null : TABS[index].id;
}

/** Settings in three tabs (v0.2): agents & models, integrations, workspace & system. */
export function SettingsPage() {
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const current = settingsTab(params.toString(), location.hash);
  const anchor = location.hash.replace(/^#/, "");

  // A deep link's anchor lives in a tab that only now rendered: scroll to it then.
  useEffect(() => {
    if (!anchor) return;
    const id = window.setTimeout(
      () => document.getElementById(anchor)?.scrollIntoView({ block: "start" }),
      50,
    );
    return () => window.clearTimeout(id);
  }, [anchor]);

  const choose = (tab: SettingsTab) =>
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (tab === "agents") next.delete("tab");
        else next.set("tab", tab);
        return next;
      },
      { replace: true },
    );

  return (
    <div className="min-h-0 grow overflow-y-auto">
      <div className="mx-auto flex w-full max-w-[760px] min-w-0 flex-col gap-6 px-9 max-md:px-4 pt-8 pb-16">
        <div
          data-tauri-drag-region="deep"
          className="flex flex-wrap items-end justify-between gap-x-5 gap-y-3"
        >
          <h1 className="m-0 text-[28px] leading-tight font-light tracking-[-0.02em] text-ink">
            {tr("설정")}
          </h1>
        </div>
        <div
          role="tablist"
          aria-label={tr("설정 탭", "Settings tabs")}
          className="grid grid-cols-3 gap-1 rounded-2xl bg-inset p-1"
          onKeyDown={(e) => {
            const next = tabAfterKey(current, e.key);
            if (!next) return;
            e.preventDefault();
            choose(next);
            document.getElementById(`settings-tab-${next}`)?.focus();
          }}
        >
          {TABS.map((tab) => (
            <button
              key={tab.id}
              type="button"
              role="tab"
              id={`settings-tab-${tab.id}`}
              tabIndex={current === tab.id ? 0 : -1}
              aria-selected={current === tab.id}
              aria-controls={`settings-panel-${tab.id}`}
              title={tab.hint}
              onClick={() => choose(tab.id)}
              className="min-w-0 rounded-xl px-3 py-2 text-center text-[13.5px] leading-tight break-keep text-text-3 max-md:px-1.5 max-md:text-[12.5px] md:whitespace-nowrap hover:text-ink aria-selected:bg-card aria-selected:font-medium aria-selected:text-ink aria-selected:shadow-sm"
            >
              {tab.title}
            </button>
          ))}
        </div>
        <p className="m-0 -mt-2 px-1 text-[12.5px] text-meta">
          {TABS.find((t) => t.id === current)?.hint}
        </p>
        <div
          role="tabpanel"
          id={`settings-panel-${current}`}
          aria-labelledby={`settings-tab-${current}`}
          className="flex flex-col gap-6"
        >
          {current === "agents" && (
            <>
              <AgentsSection />
              <ChatModelSection />
              <ToolsSection />
              <ProviderSection>
                <ClassifierQuickPick />
              </ProviderSection>
              <details
                className="group flex flex-col"
                open={anchor === "usage" || undefined}
              >
                <summary className="flex cursor-pointer list-none items-center gap-2 rounded-2xl px-1 py-2 text-[13px] font-semibold tracking-[0.02em] text-text-2 hover:text-ink [&::-webkit-details-marker]:hidden">
                  <span className="inline-block transition-transform group-open:rotate-90">
                    ›
                  </span>
                  {tr("고급 설정", "Advanced")}
                  <span className="font-normal text-[12px] text-meta">
                    {tr(
                      "인박스 분류 세부, 사용량",
                      "Inbox classifier details, usage",
                    )}
                  </span>
                </summary>
                <div className="mt-3 flex flex-col gap-6">
                  <ClassifierSection />
                  <UsageSection />
                </div>
              </details>
            </>
          )}
          {current === "integrations" && (
            <>
              <NotifySection />
              <VaultSection />
              <ICloudSection />
              <CalendarFeedSection />
              <LmsSection />
              <RemoteSection />
            </>
          )}
          {current === "system" && (
            <>
              <DisplaySection />
              <JobRootsSection />
              <OpsSection />
              <AppUpdateSection />
              <UninstallSection />
            </>
          )}
        </div>
      </div>
    </div>
  );
}

/** Theme and language (they used to sit in the page header). */
function DisplaySection() {
  return (
    <section
      aria-label={tr("화면 · 언어", "Display · Language")}
      className={`${card} flex w-full flex-col gap-4 p-6`}
    >
      <h2 className="m-0 text-[20px] font-light tracking-[-0.02em] text-ink">
        {tr("화면 · 언어", "Display · Language")}
      </h2>
      <div className="flex flex-wrap items-center gap-x-8 gap-y-3">
        <ThemePicker />
        <LanguagePicker />
        <MascotPicker />
      </div>
    </section>
  );
}

/** The inbox classifier's model in one line, under the model connections (the full
 * options stay in 고급 설정 → 인박스 분류). */
export function ClassifierQuickPick() {
  const current = useClassifierSettings();
  const providerId = current.data?.provider_id ?? "";
  const models = useOllamaModels(providerId || undefined);
  const providers = useProviders();
  const save = useSaveClassifier();
  const active = current.data?.enabled ? (current.data.model ?? OFF) : OFF;
  const list = models.data?.models ?? [];
  const where = providers.data?.find((p) => p.id === providerId);
  const hermes = current.data?.provider === "hermes";
  return (
    <div className="flex flex-col gap-1.5 rounded-2xl bg-inset px-4 py-3">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <span className="flex min-w-0 grow basis-40 flex-col gap-0.5">
          <span className="text-[13.5px] text-ink">
            {tr("자동 분류·요약 모델", "Auto classify & summarize model")}
          </span>
          <span className="text-[12px] text-text-3">
            {hermes
              ? tr(
                  "Hermes를 쓰는 동안에는 .env의 ARGOS_CLASSIFIER_MODEL로 모델을 정해요.",
                )
              : where
                ? tt`${where.name} · 인박스 정리와 LMS 알림 요약에 써요`
                : tr(
                    "인박스 정리와 LMS 알림 요약에 써요",
                    "Sorts the inbox and summarizes LMS notices",
                  )}
          </span>
        </span>
        {!hermes && (
          <SelectMenu
            ariaLabel={tr(
              "자동 분류·요약 모델",
              "Auto classify & summarize model",
            )}
            className={`${field} min-w-0 grow basis-56 font-mono text-[13px]`}
            value={active}
            disabled={save.isPending || current.isLoading}
            onChange={(value) =>
              save.mutate({
                model: value || null,
                provider_id: providerId || undefined,
              })
            }
            options={[
              ...list.map((m) => ({
                value: m.name,
                // A plain string label: the menu's type-ahead searches it.
                label: [
                  m.name,
                  m.decision ? tr("판단 모델") : null,
                  m.remote ? tr("클라우드") : null,
                ]
                  .filter(Boolean)
                  .join(" · "),
              })),
              ...(active && !list.some((m) => m.name === active)
                ? [{ value: active, label: active }]
                : []),
              { value: OFF, label: tr("분류 끄기") },
            ]}
          />
        )}
      </div>
      {models.data && !models.data.reachable && (
        <span className="text-[12px] text-meta">{models.data.error}</span>
      )}
      <ErrorText error={save.error} />
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
  const providers = useProviders();
  const [providerChoice, setProviderChoice] = useState<string | null>(null);
  const activeProvider = current.data?.provider_id ?? "";
  const selectedProvider = providerChoice ?? activeProvider;
  // Decision models and /api/tags are Ollama's: OpenAI-compatible servers cannot classify.
  const ollamaProviders = (providers.data ?? []).filter(
    (p) => p.kind === "ollama",
  );
  const where = ollamaProviders.find((p) => p.id === selectedProvider);
  const models = useOllamaModels(selectedProvider || undefined);
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
    selectedProvider !== activeProvider ||
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
          {ollamaProviders.length > 1 && (
            <label className="flex flex-col gap-1">
              <span className={label}>{tr("모델 연결")}</span>
              <SelectMenu
                className={field}
                value={selectedProvider}
                onChange={(value) => {
                  setProviderChoice(value);
                  setChoice(null);
                  setTitleChoice(null);
                }}
                options={[
                  ...ollamaProviders.map((p) => ({
                    value: p.id,
                    label: p.name,
                  })),
                ]}
              />
            </label>
          )}
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
                  : RUNS_AT[where?.locality ?? "device"],
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
          <SelectMenu
            id="classifier-title-model"
            className={`${field} font-mono text-[13px]`}
            value={selectedTitle}
            onChange={(value) => setTitleChoice(value)}
            options={[
              { value: "", label: tr("원문에서 만들기 (모델 없이)") },
              ...chatModels.map((m) => ({
                value: m.name,
                label: (
                  <>
                    {m.name}
                    {m.remote ? ` · ${tr("클라우드")}` : ""}
                  </>
                ),
              })),
              ...(selectedTitle && !titleModel && models.data?.reachable
                ? [
                    {
                      value: selectedTitle,
                      label: (
                        <>
                          {selectedTitle} · {tr("Ollama에서 찾을 수 없어요")}
                        </>
                      ),
                    },
                  ]
                : []),
            ]}
          />
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
                provider_id: selectedProvider || undefined,
              },
              {
                onSuccess: () => {
                  setProviderChoice(null);
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

const RUNS_AT = {
  device: tr("이 컴퓨터에서만 실행돼요"),
  network: tr("내 네트워크의 서버에서 실행돼요"),
  cloud: tr("입력한 내용이 외부 서버로 전송돼요"),
} as const;

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
    ? (feed.data.url ?? `${location.origin}${BASE}${feed.data.path}`)
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
  ["failed", t("실패", "failed")],
];

/** Two-way iCloud calendar sync (PLAN 7b): connect, calendars → channels, conflicts. */
export function ICloudSection() {
  const icloud = useICloud();
  const connect = useConnectICloud();
  const disconnect = useDisconnectICloud();
  const sync = useSyncCalendars();
  const setChannel = useCalendarChannel();
  const options = useCalendarOptions();
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
          `모든 캘린더의 일정을 Argos로 가져오고, Argos에서 만든 일정과 칸반 마감은 "${data?.write_calendar ?? "Argos"}" 캘린더(영역별로 나누면 "${data?.write_calendar ?? "Argos"} · 영역 이름")에만 써요.`,
          `Argos imports events from all calendars and writes its own events and kanban deadlines only to the “${data?.write_calendar ?? "Argos"}” calendar (or “${data?.write_calendar ?? "Argos"} · area name” per area).`,
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
          <div className="flex flex-col gap-2">
            {(
              [
                [
                  "push_tasks",
                  t(
                    "칸반 마감을 캘린더에 올리기",
                    "Put kanban deadlines in the calendar",
                  ),
                  t(
                    '마감 30분 전부터 "마감 · 제목"으로 보이고, 완료하면 사라져요. 캘린더에서 고쳐도 칸반 내용으로 돌아와요.',
                    "Shown as “Due · title” for the half hour before; gone once done. Edits in Calendar are replaced by the kanban.",
                  ),
                ],
                [
                  "split_areas",
                  t("영역별 캘린더로 나누기", "One calendar per area"),
                  t(
                    `새 일정과 마감을 "${data.write_calendar} · 영역 이름" 캘린더에 써요. 이미 있는 일정은 옮기지 않아요.`,
                    `New events and deadlines go to “${data.write_calendar} · area name”. Existing events stay where they are.`,
                  ),
                ],
              ] as const
            ).map(([key, title, hint]) => (
              <label key={key} className="flex items-start gap-2 text-sm">
                <input
                  type="checkbox"
                  className="mt-1 accent-[var(--ink)]"
                  checked={data[key]}
                  disabled={options.isPending}
                  onChange={(e) =>
                    options.mutate({
                      push_tasks: data.push_tasks,
                      split_areas: data.split_areas,
                      [key]: e.target.checked,
                    })
                  }
                />
                <span>
                  {title}
                  <span className="block text-xs text-meta">{hint}</span>
                </span>
              </label>
            ))}
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
                  <SelectMenu
                    ariaLabel={tt`${c.name} 일정을 넣을 채널`}
                    className="h-9 w-44 shrink-0 cursor-pointer border-0 border-b border-line bg-transparent text-[13.5px] text-text outline-none focus:border-ink"
                    value={c.channel_id ?? ""}
                    disabled={setChannel.isPending}
                    onChange={(value) =>
                      setChannel.mutate({
                        calendar_url: c.url,
                        channel_id: value || null,
                      })
                    }
                    options={[
                      { value: "", label: tr("내 공간 (기본)") },
                      ...choices
                        .filter((ch) => ch.kind !== "personal")
                        .map((ch) => ({
                          value: ch.id,
                          label: <># {ch.name}</>,
                        })),
                    ]}
                  />
                </div>
              ))}
            </div>
          )}
          <ErrorText
            error={
              sync.error ??
              setChannel.error ??
              options.error ??
              disconnect.error
            }
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
