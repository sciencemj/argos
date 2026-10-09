import { useState } from "react";
import { Link, useNavigate } from "react-router";
import {
  type SetupTool,
  useConnectTool,
  useDisconnectTool,
  useFinishSetup,
  useInstallService,
  useOps,
  useSetup,
} from "../api";
import { language, t, tr } from "../i18n";
import { DogIcon } from "../icons";
import { LanguagePicker } from "../LanguagePicker";
import { btn, card, ErrorText } from "../ui";
import { LmsSection } from "./LmsSettings";
import { AgentSection, ClassifierSection, VaultSection } from "./SettingsPage";

// Why each tool matters to Argos, in the words of the setup screen.
const USE: Record<string, string> = {
  claude: tr(
    "채팅·코딩 잡 에이전트. 연결하면 터미널의 Claude Code도 Argos 도구를 써요.",
  ),
  codex: tr(
    "채팅·코딩 잡 에이전트. 연결하면 터미널의 Codex도 Argos 도구를 써요.",
  ),
  hermes: tr(
    "기본 대화 에이전트(메신저 알림 포함). 연결해야 Argos에 일정·할 일을 기록해요.",
  ),
  ollama: tr("인박스 분류와 비슷한 메모 묶기를 이 Mac에서 돌리는 로컬 모델."),
};

function State({ on, label }: { on: boolean; label: string }) {
  return (
    <span
      className={`rounded-full border px-2 py-px text-[11.5px] ${on ? "border-line bg-inset text-ink" : "border-line-soft text-meta"}`}
    >
      {label} {on ? "✓" : tr("없음")}
    </span>
  );
}

function ToolRow({ tool, manage }: { tool: SetupTool; manage: boolean }) {
  const connect = useConnectTool();
  const disconnect = useDisconnectTool();
  const busy = connect.isPending || disconnect.isPending;
  const complete = tool.connected && tool.skill;
  const remove = (mcp: boolean, skill: boolean) => {
    const koreanItem = mcp && skill ? "MCP와 스킬을" : mcp ? "MCP를" : "스킬을";
    const englishItem =
      mcp && skill
        ? "MCP connection and skill"
        : mcp
          ? "MCP connection"
          : "skill";
    const question = t(
      `${tool.label}에서 Argos ${koreanItem} 지울까요?`,
      `Remove the Argos ${englishItem} from ${tool.label}?`,
    );
    if (confirm(question)) disconnect.mutate({ name: tool.name, mcp, skill });
  };

  return (
    <li className="flex flex-col gap-2 rounded-2xl border border-line-soft px-4 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[14px] font-medium text-ink">{tool.label}</span>
        <span className="min-w-0 grow truncate font-mono text-[11.5px] text-meta">
          {tool.installed ? (tool.version ?? tool.path) : ""}
        </span>
        {!tool.installed ? (
          <span className="text-[12px] text-text-3">{tr("설치 안 됨")}</span>
        ) : !tool.connectable ? (
          <span className="text-[12px] text-text-3">{tr("설치됨")}</span>
        ) : (
          <>
            <State on={tool.connected} label="MCP" />
            <State on={tool.skill} label={tr("스킬")} />
            {!complete && (
              <button
                type="button"
                className={`${btn.outline} shrink-0`}
                disabled={busy}
                onClick={() => connect.mutate({ name: tool.name })}
              >
                {connect.isPending ? tr("연결하는 중…") : tr("Argos에 연결")}
              </button>
            )}
          </>
        )}
      </div>
      <span className="text-[12.5px] leading-relaxed text-text-3">
        {USE[tool.name]}
      </span>
      {!tool.installed && (
        <span className="text-[12px] text-meta">{tool.hint}</span>
      )}
      {tool.skill_conflict && (
        <span className="text-[12px] text-danger">
          {tool.skill_path}
          {tr("에 Argos가 설치하지 않은 argos 스킬이 있어 건드리지 않아요.")}
        </span>
      )}
      {manage && (tool.connected || tool.skill) && (
        <div className="flex flex-wrap items-center gap-1">
          {tool.skill && tool.skill_path && (
            <span className="grow truncate font-mono text-[11px] text-meta">
              {tool.skill_path}
            </span>
          )}
          {tool.connected && (
            <button
              type="button"
              className={btn.ghost}
              disabled={busy}
              onClick={() => remove(true, false)}
            >
              {tr("MCP 제거")}
            </button>
          )}
          {tool.skill && (
            <button
              type="button"
              className={btn.ghost}
              disabled={busy}
              onClick={() => remove(false, true)}
            >
              {tr("스킬 제거")}
            </button>
          )}
          {tool.connected && tool.skill && (
            <button
              type="button"
              className={btn.ghost}
              disabled={busy}
              onClick={() => remove(true, true)}
            >
              {tr("모두 제거")}
            </button>
          )}
        </div>
      )}
      <ErrorText error={connect.error ?? disconnect.error} />
    </li>
  );
}

/** Agent tools on this Mac and their connection to Argos (setup and settings). */
export function ToolsSection({ embedded = false }: { embedded?: boolean }) {
  const setup = useSetup();
  const tools = setup.data?.tools ?? [];
  return (
    <section
      aria-label={tr("에이전트 도구")}
      className={
        embedded
          ? "flex flex-col gap-3"
          : `${card} flex w-full flex-col gap-4 p-6`
      }
    >
      {!embedded && (
        <h2 className="m-0 text-[20px] font-light tracking-[-0.02em] text-ink">
          {tr("에이전트 도구")}
        </h2>
      )}
      <p className="m-0 text-[13px] leading-relaxed text-text-3">
        {language() === "ko" ? (
          <>
            이 Mac에 설치된 도구를 찾았어요. "Argos에 연결"은 각 도구에 Argos의
            MCP 주소를 등록하고(<code>mcp add</code>, Hermes는{" "}
            <code>config set</code>), Argos 도구 쓰는 법을 담은{" "}
            <code>argos</code> 스킬을 설치해요. 누르기 전에는 도구 설정을 바꾸지
            않고, 설정에서 언제든 지울 수 있어요.
          </>
        ) : (
          <>
            We found the agent tools installed on this Mac. “Connect to Argos”
            registers the Argos MCP address with each tool (<code>mcp add</code>
            , or <code>config set</code> for Hermes) and installs the{" "}
            <code>argos</code> skill. Nothing changes until you connect, and you
            can remove it later in Settings.
          </>
        )}
      </p>
      {setup.isLoading && (
        <span className="text-[12.5px] text-meta">{tr("도구를 찾는 중…")}</span>
      )}
      <ul className="m-0 flex list-none flex-col gap-2 p-0">
        {tools.map((t) => (
          <ToolRow key={t.name} tool={t} manage={!embedded} />
        ))}
      </ul>
      <div className="flex items-center gap-3">
        <button
          type="button"
          className={btn.ghost}
          disabled={setup.isFetching}
          onClick={() => void setup.refetch()}
        >
          {setup.isFetching ? tr("다시 찾는 중…") : tr("다시 찾기")}
        </button>
        {!embedded && (
          <Link
            to="/welcome"
            className="text-[12.5px] text-text-3 hover:text-ink"
          >
            {tr("처음 설정 다시 보기")}
          </Link>
        )}
      </div>
      <ErrorText error={setup.error} />
    </section>
  );
}

function StartAtLogin() {
  const ops = useOps();
  const install = useInstallService();
  const service = ops.data?.service as
    | { installed: boolean; supported: boolean }
    | undefined;
  if (!service?.supported) return null;
  return (
    <div className="flex items-center gap-3 rounded-2xl border border-line-soft px-4 py-3">
      <span className="grow text-[13.5px] text-ink">
        {tr("로그인할 때 Argos 열기") + " "}
        <span className="block text-[12px] text-text-3">
          {tr("메뉴 막대에서 조용히 켜져 알림·동기화가 이어져요.")}
        </span>
      </span>
      {service.installed ? (
        <span className="text-[12px] text-text-3">{tr("켜짐")}</span>
      ) : (
        <button
          type="button"
          className={btn.outline}
          disabled={install.isPending}
          onClick={() => install.mutate(false)}
        >
          {tr("켜기")}
        </button>
      )}
    </div>
  );
}

const STEPS = [
  tr("환영"),
  tr("에이전트 도구"),
  tr("기본 설정"),
  tr("마무리"),
] as const;

/** First run (PLAN Phase 12): find the tools, connect them, pick the defaults. Every
 * step can be skipped; everything here is also in 설정. */
export function WelcomePage() {
  const [step, setStep] = useState(0);
  const setup = useSetup();
  const finish = useFinishSetup();
  const navigate = useNavigate();
  const done = () =>
    finish.mutate(undefined, { onSuccess: () => navigate("/") });

  return (
    <div className="h-full overflow-y-auto bg-page">
      <div data-tauri-drag-region className="window-drag" />
      <div className="mx-auto flex w-full max-w-[760px] flex-col gap-6 px-6 pt-12 pb-16">
        <div className="flex justify-end">
          <LanguagePicker />
        </div>
        <header className="flex items-center gap-4">
          <div className="flex size-12 shrink-0 items-center justify-center rounded-2xl bg-step-5 text-on-dark">
            <DogIcon />
          </div>
          <div className="flex grow flex-col">
            <h1 className="m-0 text-[28px] leading-tight font-light tracking-[-0.02em] text-ink">
              {STEPS[step] === tr("환영")
                ? tr("Argos에 오신 걸 환영해요")
                : STEPS[step]}
            </h1>
            <span className="font-mono text-[12px] text-meta">
              {step + 1} / {STEPS.length}
            </span>
          </div>
          <button type="button" className={btn.ghost} onClick={done}>
            {tr("건너뛰기")}
          </button>
        </header>
        <ol className="m-0 flex list-none gap-1.5 p-0" aria-label={tr("단계")}>
          {STEPS.map((s, i) => (
            <li
              key={s}
              aria-current={i === step ? "step" : undefined}
              className={`h-1 grow rounded-full transition-colors ${i <= step ? "bg-ink" : "bg-line"}`}
            />
          ))}
        </ol>

        {step === 0 && (
          <div
            className={`${card} flex flex-col gap-3 p-6 text-[14px] leading-relaxed text-text`}
          >
            <p className="m-0">
              {tr(
                "Argos는 과목·프로젝트별 채팅 피드에 적은 메모를 할 일·일정으로 정리하고, 에이전트(Claude, Codex, Hermes)와 함께 일하는 이 Mac 전용 대시보드예요.",
              )}
            </p>
            <p className="m-0 text-text-3">
              {tr(
                "몇 가지만 정하면 바로 쓸 수 있어요. 모든 항목은 나중에 설정에서 바꿀 수 있어요. 데이터는 이 Mac의",
              )}{" "}
              <code className="font-mono text-[12.5px]">
                {setup.data?.data_dir ?? "…"}
              </code>
              {tr("에만 저장돼요.")}
            </p>
          </div>
        )}
        {step === 1 && (
          <div className={`${card} p-6`}>
            <ToolsSection embedded />
          </div>
        )}
        {step === 2 && (
          <div className="flex flex-col gap-4">
            <AgentSection />
            <ClassifierSection />
            <VaultSection />
            <LmsSection />
          </div>
        )}
        {step === 3 && (
          <div className={`${card} flex flex-col gap-4 p-6`}>
            <p className="m-0 text-[14px] leading-relaxed text-text">
              {tr("준비됐어요. 채널에 그냥 적으면 인박스에서 정리되고,")}{" "}
              <code>/task</code>, <code>/event</code>,{" "}
              <code>{tr("@에이전트")}</code>
              {tr("로 바로 부를 수도 있어요.")}
            </p>
            {setup.data?.desktop && <StartAtLogin />}
            {setup.data?.desktop && (
              <p className="m-0 text-[12.5px] text-text-3">
                {tr("어디서든") + " "}
                <kbd className="font-mono">⌘⇧Space</kbd>
                {tr(
                  "로 인박스에 빠르게 적을 수 있어요. 창을 닫아도 메뉴 막대에서 계속 돌아요.",
                )}
              </p>
            )}
          </div>
        )}

        <ErrorText error={finish.error} />
        <div className="flex items-center gap-2">
          {step > 0 && (
            <button
              type="button"
              className={btn.ghost}
              onClick={() => setStep(step - 1)}
            >
              {tr("이전")}
            </button>
          )}
          <span className="grow" />
          {step < STEPS.length - 1 ? (
            <button
              type="button"
              className={btn.cta}
              onClick={() => setStep(step + 1)}
            >
              {tr("다음")}
            </button>
          ) : (
            <button
              type="button"
              className={btn.cta}
              disabled={finish.isPending}
              onClick={done}
            >
              {tr("Argos 시작하기")}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
