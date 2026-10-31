import { type FormEvent, useState } from "react";
import {
  errorText,
  type Provider,
  type ProviderForm,
  useDeleteProvider,
  useProviderModels,
  useProviders,
  useSaveProvider,
  useTestProvider,
} from "../api";
import { tr, tt } from "../i18n";
import { btn, card, Dialog, ErrorText, field, label } from "../ui";

type Locality = Provider["locality"];
type Strict = NonNullable<Provider["strict_locality"]>;

const KINDS = [
  { id: "ollama", label: "Ollama" },
  { id: "openai", label: tr("OpenAI 호환") },
] as const;

const LOCALITY_TEXT: Record<Locality, string> = {
  device: tr("이 기기"),
  network: tr("내 네트워크"),
  cloud: tr("클라우드"),
};

const ORDER: Locality[] = ["device", "network", "cloud"];

/** The stricter of the address's place and the user's "show as" choice. */
const stricter = (a: Locality, b: Strict | null): Locality =>
  b && ORDER.indexOf(b) > ORDER.indexOf(a) ? b : a;

function LocalityBadge({ locality }: { locality: Locality }) {
  const tone =
    locality === "cloud" ? "bg-danger-bg text-danger" : "bg-inset text-text-2";
  return (
    <span
      className={`rounded-full px-[9px] py-0.5 text-[11.5px] font-medium whitespace-nowrap ${tone}`}
    >
      {LOCALITY_TEXT[locality]}
    </span>
  );
}

function CloudWarning() {
  return (
    <p role="alert" className="m-0 text-[12.5px] text-danger">
      {tr(
        "대화 내용이 이 컴퓨터와 내 네트워크 밖으로 나가요. 이 연결을 쓰는 에이전트와 분류에 보낸 글은 그 서비스가 받아요.",
      )}
    </p>
  );
}

/** Phase 20: named model providers (Ollama or OpenAI compatible) that custom agents
 * and the inbox classifier pick from. */
export function ProviderSection() {
  const providers = useProviders();
  const remove = useDeleteProvider();
  const [editing, setEditing] = useState<Provider | "new" | null>(null);

  return (
    <section
      aria-label={tr("모델 연결")}
      className={`${card} flex w-full flex-col gap-4 p-6`}
    >
      <div className="flex items-center gap-2">
        <h2 className="m-0 grow text-[20px] font-light tracking-[-0.02em] text-ink">
          {tr("모델 연결")}
        </h2>
        <button
          type="button"
          className={btn.outline}
          onClick={() => setEditing("new")}
        >
          {tr("연결 추가")}
        </button>
      </div>
      <p className="m-0 text-[13px] leading-relaxed text-text-3">
        {tr(
          "로컬 모델을 돌리는 서버(Ollama, OpenAI 호환 API)를 이름 붙여 등록해요. 커스텀 에이전트와 인박스 분류가 여기서 연결을 골라 써요.",
        )}
      </p>
      <ul className="m-0 flex list-none flex-col gap-2 p-0">
        {(providers.data ?? []).map((p) => (
          <li
            key={p.id}
            className="flex items-center gap-3 rounded-2xl border border-line-soft px-4 py-3"
          >
            <span className="flex min-w-0 grow flex-col gap-0.5">
              <span className="flex flex-wrap items-center gap-2 text-[13.5px] text-ink">
                <span>{p.name}</span>
                <LocalityBadge locality={p.locality} />
                {p.is_default && (
                  <span className="text-[11.5px] text-meta">{tr("기본")}</span>
                )}
              </span>
              <span className="text-[12px] text-text-3">
                {KINDS.find((k) => k.id === p.kind)?.label}
                {" · "}
                <span className="font-mono [overflow-wrap:anywhere]">
                  {p.base_url}
                </span>
              </span>
              <ProviderStatus id={p.id} />
            </span>
            <button
              type="button"
              className={`${btn.ghost} shrink-0 whitespace-nowrap`}
              onClick={() => setEditing(p)}
            >
              {tr("수정")}
            </button>
            {!p.is_default && (
              <button
                type="button"
                className={`${btn.ghost} shrink-0 whitespace-nowrap`}
                disabled={remove.isPending}
                onClick={() =>
                  confirm(tt`"${p.name}" 연결을 지울까요?`) &&
                  remove.mutate(p.id)
                }
              >
                {tr("삭제")}
              </button>
            )}
          </li>
        ))}
      </ul>
      <ErrorText error={providers.error ?? remove.error} />
      {editing && (
        <ProviderDialog
          provider={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
        />
      )}
    </section>
  );
}

function ProviderStatus({ id }: { id: string }) {
  const models = useProviderModels(id);
  const data = models.data;
  if (!data) return null;
  return data.reachable ? (
    <span className="text-[12px] text-meta">
      {tt`연결됨 · 모델 ${data.models?.length ?? 0}개`}
    </span>
  ) : (
    <span className="text-[12px] text-danger [overflow-wrap:anywhere]">
      {data.error ? errorText(data.error) : tr("연결할 수 없어요")}
    </span>
  );
}

function ProviderDialog({
  provider,
  onClose,
}: {
  provider: Provider | null;
  onClose: () => void;
}) {
  const save = useSaveProvider();
  const test = useTestProvider();
  const [name, setName] = useState(provider?.name ?? "");
  const [kind, setKind] = useState<Provider["kind"]>(
    provider?.kind ?? "ollama",
  );
  const [baseUrl, setBaseUrl] = useState(provider?.base_url ?? "");
  const [apiKey, setApiKey] = useState("");
  const [clearKey, setClearKey] = useState(false);
  const [strict, setStrict] = useState<Strict | null>(
    provider?.strict_locality ?? null,
  );

  // What the address itself means: the last test, else the saved provider's place.
  const known: Locality | undefined = test.data?.locality ?? provider?.locality;
  const shown = known ? stricter(known, strict) : undefined;

  const edited = <T,>(set: (value: T) => void) => {
    return (value: T) => {
      set(value);
      test.reset();
    };
  };

  const keyValue = (): string | null | undefined => {
    if (apiKey.trim()) return apiKey.trim();
    if (clearKey) return "";
    return provider ? undefined : null;
  };

  const runTest = () => {
    const key = apiKey.trim() || null;
    test.mutate({
      kind,
      base_url: baseUrl.trim(),
      api_key: key,
      // Without a new key, the saved one is used for the test.
      ...(provider && !key && !clearKey ? { provider_id: provider.id } : {}),
    });
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const body: ProviderForm & { editing?: string } = {
      name: name.trim(),
      kind,
      base_url: baseUrl.trim(),
      strict_locality: strict,
    };
    const key = keyValue();
    if (key !== undefined) body.api_key = key;
    if (provider) body.editing = provider.id;
    save.mutate(body, { onSuccess: onClose });
  };

  const result = test.data;

  return (
    <Dialog
      open
      onClose={onClose}
      title={provider ? tt`${provider.name} 연결 수정` : tr("연결 추가")}
    >
      <form onSubmit={submit} className="flex flex-col gap-4">
        <div className="grid grid-cols-[1fr_140px] gap-3">
          <label className="flex flex-col gap-1">
            <span className={label}>{tr("이름")}</span>
            <input
              className={field}
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={tr("예: 작업실 GPU")}
              required
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className={label}>{tr("종류")}</span>
            <select
              className={field}
              value={kind}
              onChange={(e) =>
                edited(setKind)(e.target.value as Provider["kind"])
              }
            >
              {KINDS.map((k) => (
                <option key={k.id} value={k.id}>
                  {k.label}
                </option>
              ))}
            </select>
          </label>
        </div>
        <label className="flex flex-col gap-1">
          <span className={label}>{tr("주소")}</span>
          <input
            className={`${field} font-mono text-[13px]`}
            value={baseUrl}
            onChange={(e) => edited(setBaseUrl)(e.target.value)}
            placeholder={
              kind === "ollama"
                ? "http://127.0.0.1:11434"
                : "https://api.example.com/v1"
            }
            required
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className={label}>{tr("API 키 (선택)")}</span>
          <input
            type="password"
            autoComplete="off"
            className={field}
            value={apiKey}
            onChange={(e) => {
              edited(setApiKey)(e.target.value);
              setClearKey(false);
            }}
            placeholder={
              provider?.has_api_key && !clearKey
                ? tr("새 키를 넣으면 바꿔요")
                : ""
            }
          />
        </label>
        {provider?.has_api_key && (
          <div className="flex items-center gap-2 text-[12.5px] text-text-3">
            <span className="grow">
              {clearKey ? tr("저장하면 키를 지워요") : tr("저장됨")}
            </span>
            <button
              type="button"
              className={btn.ghost}
              onClick={() => {
                setApiKey("");
                setClearKey(!clearKey);
                test.reset();
              }}
            >
              {clearKey ? tr("되돌리기") : tr("키 지우기")}
            </button>
          </div>
        )}
        <label className="flex flex-col gap-1">
          <span className={label}>{tr("더 엄격하게 표시")}</span>
          <select
            className={field}
            value={strict ?? ""}
            onChange={(e) => setStrict((e.target.value || null) as Strict)}
          >
            <option value="">{tr("자동")}</option>
            {(known === "device" || strict === "network") && (
              <option value="network">{tr("내 네트워크로")}</option>
            )}
            <option value="cloud">{tr("클라우드로")}</option>
          </select>
        </label>
        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            className={btn.outline}
            disabled={!baseUrl.trim() || test.isPending}
            onClick={runTest}
          >
            {test.isPending ? tr("시험하는 중…") : tr("연결 시험")}
          </button>
          {shown && <LocalityBadge locality={shown} />}
          {result &&
            (result.reachable ? (
              <span className="text-[12.5px] text-text-2">
                {tt`모델 ${result.models?.length ?? 0}개`}
              </span>
            ) : (
              <span className="text-[12.5px] text-danger [overflow-wrap:anywhere]">
                {result.error
                  ? errorText(result.error)
                  : tr("연결할 수 없어요")}
              </span>
            ))}
        </div>
        {shown === "cloud" && <CloudWarning />}
        <ErrorText error={test.error ?? save.error} />
        <div className="flex justify-end gap-2">
          <button type="button" className={btn.ghost} onClick={onClose}>
            {tr("취소")}
          </button>
          <button type="submit" className={btn.cta} disabled={save.isPending}>
            {tr("저장")}
          </button>
        </div>
      </form>
    </Dialog>
  );
}
