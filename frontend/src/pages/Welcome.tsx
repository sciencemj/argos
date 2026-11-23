import { useState } from "react";
import { useNavigate } from "react-router";
import { useFinishSetup, useInstallService, useOps, useSetup } from "../api";
import { tr } from "../i18n";
import { DogIcon } from "../icons";
import { LanguagePicker } from "../LanguagePicker";
import { btn, card, ErrorText } from "../ui";
import { AgentsSection } from "./CustomAgents";
import { LmsSection } from "./LmsSettings";
import { ClassifierSection, VaultSection } from "./SettingsPage";
import { ToolsSection } from "./ToolIntegrations";

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
  tr("도구 연결", "Tool integrations"),
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
            <AgentsSection />
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
