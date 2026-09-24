import { useState } from "react";
import {
  useClassifierSettings,
  useOllamaModels,
  useSaveClassifier,
} from "../api";
import { btn, card, ErrorText } from "../ui";

const OFF = "";

export function SettingsPage() {
  return (
    <div className="flex min-h-0 grow flex-col gap-6 overflow-y-auto px-9 pt-8 pb-8">
      <h1 className="m-0 text-[28px] leading-tight font-light tracking-[-0.02em] text-ink">
        설정
      </h1>
      <ClassifierSection />
    </div>
  );
}

const SOURCE_TEXT = {
  app: "여기서 선택함",
  env: ".env 설정",
  none: "",
} as const;

/** Picks which installed Ollama model classifies inbox text (PLAN §9). */
function ClassifierSection() {
  const current = useClassifierSettings();
  const models = useOllamaModels();
  const save = useSaveClassifier();
  const [choice, setChoice] = useState<string | null>(null);

  const active = current.data?.enabled ? (current.data.model ?? OFF) : OFF;
  const selected = choice ?? active;
  const list = models.data?.models ?? [];
  const selectedModel = list.find((m) => m.name === selected);
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
          {remote ? "클라우드" : "로컬"}
        </span>
      )}
      {value === active && (
        <span className="text-[11.5px] whitespace-nowrap text-meta">
          사용 중
        </span>
      )}
    </label>
  );

  return (
    <section
      aria-label="인박스 분류"
      className={`${card} flex max-w-[720px] flex-col gap-4 p-6`}
    >
      <div className="flex items-baseline gap-2">
        <h2 className="m-0 grow text-[20px] font-light tracking-[-0.02em] text-ink">
          인박스 분류
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
        슬래시 없이 적은 메시지를 할 일·일정으로 정리할 모델을 골라요. 분류하는
        동안에도 원문은 인박스에 먼저 저장돼요.
      </p>

      {current.data?.provider === "hermes" ? (
        <p className="m-0 text-[13px] text-text-3">
          Hermes를 쓰는 동안에는 .env의 ARGOS_CLASSIFIER_MODEL로 모델을 정해요.
        </p>
      ) : (
        <div className="flex flex-col gap-2">
          {models.isLoading && (
            <p className="m-0 text-[13px] text-meta">
              Ollama 모델을 불러오는 중…
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
                다시 확인
              </button>
            </div>
          )}
          {list.map((m) =>
            option(
              m.name,
              m.name,
              [
                m.parameter_size,
                m.remote
                  ? "입력한 내용이 ollama.com으로 전송돼요"
                  : "이 컴퓨터에서만 실행돼요",
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
              "Ollama에서 찾을 수 없어요 · 다른 모델을 골라 주세요",
            )}
          {option(OFF, "분류 끄기", "메시지는 인박스에만 쌓이고 직접 정리해요")}
        </div>
      )}

      {selectedModel?.remote && selected !== active && (
        <p
          role="alert"
          className="m-0 rounded-xl bg-danger-bg px-3.5 py-2.5 text-[12.5px] text-danger"
        >
          클라우드 모델은 메시지 내용을 ollama.com 서버로 보내요. 개인 일정이
          밖으로 나가도 괜찮을 때만 고르세요.
        </p>
      )}
      <ErrorText error={save.error} />
      <div className="flex items-center gap-3">
        <button
          type="button"
          className={btn.cta}
          disabled={
            save.isPending ||
            selected === active ||
            current.data?.provider === "hermes"
          }
          onClick={() =>
            save.mutate(selected || null, { onSuccess: () => setChoice(null) })
          }
        >
          저장
        </button>
        {save.isSuccess && selected === active && (
          <span className="text-[12.5px] text-text-3">
            저장했어요. 다음 메시지부터 적용돼요.
          </span>
        )}
      </div>
    </section>
  );
}
