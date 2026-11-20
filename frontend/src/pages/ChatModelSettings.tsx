import {
  type ChatEffort,
  useChatModels,
  useChatSettings,
  useProviders,
  useSaveChatSettings,
} from "../api";
import { tr } from "../i18n";
import { card, ErrorText, field, label, SelectMenu } from "../ui";

const EFFORTS: { value: ChatEffort | ""; label: string }[] = [
  { value: "", label: tr("모델 기본값", "Model default") },
  { value: "low", label: tr("낮음", "Low") },
  { value: "medium", label: tr("중간", "Medium") },
  { value: "high", label: tr("높음", "High") },
];

/** Settings → 기본 채팅 모델 (v0.2): what a chat message with no @mention runs on.
 * Not an agent: agents are only called with @, each on its own model. */
export function ChatModelSection() {
  const chat = useChatSettings();
  const providers = useProviders();
  const models = useChatModels();
  const save = useSaveChatSettings();
  const data = chat.data;
  const connection = data?.provider_id ?? "";
  // Without a pick the connection is the inbox classifier's: offer its models there.
  const listed = (models.data?.models ?? []).filter((m) =>
    connection
      ? m.provider_id === connection
      : m.provider_name === data?.provider_name,
  );
  const current = data?.model ?? "";
  return (
    <section
      aria-label={tr("기본 채팅 모델", "Default chat model")}
      className={`${card} flex w-full flex-col gap-4 p-6`}
    >
      <h2 className="m-0 text-[20px] font-light tracking-[-0.02em] text-ink">
        {tr("기본 채팅 모델", "Default chat model")}
      </h2>
      <p className="m-0 text-[13px] leading-relaxed text-text-3">
        {tr(
          "에이전트를 부르지 않은 채팅은 이 모델이 Argos 도구와 메인 기억으로 답해요. 에이전트는 @이름으로 부르면 자기 모델과 기억을 써요. 입력창의 모델 선택도 여기를 바꿔요.",
          "Chat that calls no agent is answered by this model with Argos's tools and main memory. An agent called with @name uses its own model and memory. The composer's model picker changes this too.",
        )}
      </p>
      <label className="flex flex-col gap-1">
        <span className={label}>{tr("모델 연결", "Connection")}</span>
        <SelectMenu
          className={field}
          value={connection}
          disabled={save.isPending}
          // Another connection lists other models: drop the old pick with it.
          onChange={(value) =>
            save.mutate({ provider_id: value || null, model: null })
          }
          options={[
            {
              value: "",
              label: tr("인박스 분류와 같은 연결", "The inbox classifier's"),
            },
            ...(providers.data ?? []).map((p) => ({
              value: p.id,
              label: p.name,
            })),
          ]}
        />
      </label>
      <label className="flex flex-col gap-1">
        <span className={label}>{tr("모델", "Model")}</span>
        <SelectMenu
          className={`${field} font-mono text-[13px]`}
          value={current}
          disabled={save.isPending}
          onChange={(value) => save.mutate({ model: value || null })}
          options={[
            ...(connection
              ? []
              : [
                  {
                    value: "",
                    label: tr(
                      "인박스 분류 모델과 같게",
                      "Same as the inbox classifier",
                    ),
                  },
                ]),
            ...listed.map((m) => ({ value: m.model, label: m.model })),
            ...(current && !listed.some((m) => m.model === current)
              ? [{ value: current, label: current }]
              : []),
          ]}
        />
      </label>
      <label className="flex flex-col gap-1">
        <span className={label}>{tr("추론 수준", "Reasoning effort")}</span>
        <SelectMenu
          className={field}
          value={data?.effort ?? ""}
          disabled={save.isPending}
          onChange={(value) =>
            save.mutate({ effort: (value || null) as ChatEffort | null })
          }
          options={EFFORTS.map((e) => ({ value: e.value, label: e.label }))}
        />
      </label>
      {data?.problem ? (
        <p role="alert" className="m-0 text-[12.5px] text-danger">
          {data.problem}
        </p>
      ) : (
        data?.resolved_model && (
          <p className="m-0 text-[12.5px] text-meta">
            {data.provider_name} · {data.resolved_model}
          </p>
        )
      )}
      <ErrorText error={save.error} />
    </section>
  );
}
