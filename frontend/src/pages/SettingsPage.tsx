import { useState } from "react";
import { AgentAvatar } from "../agents";
import {
  type Conflict,
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
  useResolveConflict,
  useRotateCalendarFeed,
  useSaveClassifier,
  useSaveDefaultAgent,
  useSaveJobRoots,
  useSyncCalendars,
} from "../api";
import { fmt } from "../dates";
import { btn, card, ErrorText, field, label } from "../ui";

const OFF = "";

export function SettingsPage() {
  return (
    <div className="flex min-h-0 grow flex-col gap-6 overflow-y-auto px-9 pt-8 pb-8">
      <h1 className="m-0 text-[28px] leading-tight font-light tracking-[-0.02em] text-ink">
        설정
      </h1>
      <AgentSection />
      <ClassifierSection />
      <ICloudSection />
      <CalendarFeedSection />
      <JobRootsSection />
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

const BACKEND_TEXT: Record<string, string> = {
  hermes: "Hermes 게이트웨이 · 자체 도구와 메모리를 가진 에이전트",
  claude_code: "Claude Code CLI · Argos 도구만 사용 · Claude 사용량을 씀",
  codex: "Codex CLI · Argos 도구만 사용 · Codex 사용량을 씀",
  ollama: "Ollama 로컬 모델 · 이 컴퓨터에서만 실행 (인박스 분류 모델 사용)",
};

/** App-wide default agent: answers /ask where the channel sets none (user decision). */
function AgentSection() {
  const agents = useAgents();
  const current = useAgentSettings();
  const save = useSaveDefaultAgent();
  const [choice, setChoice] = useState<string | null>(null);
  const active = current.data?.default_agent;
  const selected = choice ?? active;

  return (
    <section
      aria-label="기본 에이전트"
      className={`${card} flex max-w-[720px] flex-col gap-4 p-6`}
    >
      <h2 className="m-0 text-[20px] font-light tracking-[-0.02em] text-ink">
        기본 에이전트
      </h2>
      <p className="m-0 text-[13px] leading-relaxed text-text-3">
        /ask를 받을 에이전트예요. 채널 설정에서 채널마다 따로 정할 수도 있어요.
        다른 에이전트는 메시지에 @이름으로 불러요.
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
                {BACKEND_TEXT[a.backend]}
              </span>
              {!a.available && (
                <span className="text-[12px] text-danger">{a.problem}</span>
              )}
            </span>
            {a.name === active && (
              <span className="text-[11.5px] text-meta">사용 중</span>
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
          저장
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
      aria-label="코딩 잡 작업 디렉터리"
      className={`${card} flex max-w-[720px] flex-col gap-4 p-6`}
    >
      <h2 className="m-0 text-[20px] font-light tracking-[-0.02em] text-ink">
        코딩 잡 작업 디렉터리
      </h2>
      <p className="m-0 text-[13px] leading-relaxed text-text-3">
        Claude·Codex 잡은 이 폴더 안에서만 파일을 만들고 고쳐요. 폴더를 정하지
        않은 잡은 첫 번째 폴더 아래에 새 폴더를 만들어요.
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
                기본
              </span>
            )}
            <button
              type="button"
              className={`${btn.ghost} shrink-0 whitespace-nowrap`}
              aria-label={`${root} 삭제`}
              disabled={save.isPending || roots.length === 1}
              onClick={() => save.mutate(roots.filter((r) => r !== root))}
            >
              삭제
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
          <span className={label}>폴더 추가</span>
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
          추가
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
      aria-label="애플 캘린더 구독"
      className={`${card} flex max-w-[720px] flex-col gap-4 p-6`}
    >
      <h2 className="m-0 text-[20px] font-light tracking-[-0.02em] text-ink">
        애플 캘린더 구독
      </h2>
      <p className="m-0 text-[13px] leading-relaxed text-text-3">
        Argos의 일정과 마감(완료하지 않은 할 일)을 캘린더 앱에서 볼 수 있어요.
        캘린더 앱에서 "구독 캘린더 추가"에 아래 주소를 넣으면 10분마다 새로
        가져와요. 읽기 전용이라 캘린더 앱에서 고친 내용은 Argos에 반영되지
        않아요.
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
          {copied ? "복사했어요" : "복사"}
        </button>
      </div>
      <p className="m-0 text-[12px] leading-relaxed text-meta">
        주소를 아는 사람은 누구나 일정을 볼 수 있어요. 이 주소는 Tailscale에
        연결된 기기에서만 열려요. 주소가 새어 나갔다면 새 주소로 바꾸세요.
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
                "새 주소로 바꿀까요? 지금 주소로 구독한 캘린더는 더 이상 갱신되지 않아요.",
              )
            ) {
              rotate.mutate(undefined, { onSuccess: () => setCopied(false) });
            }
          }}
        >
          새 주소로 바꾸기
        </button>
      </div>
    </section>
  );
}

const RESULT_TEXT: [string, string][] = [
  ["created", "가져옴"],
  ["updated", "바뀜"],
  ["pushed", "보냄"],
  ["deleted", "지움"],
  ["conflicts", "충돌"],
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
  const personal = choices.find((c) => c.kind === "personal");
  const result = status?.last_result
    ? RESULT_TEXT.filter(([key]) => status.last_result?.[key])
        .map(([key, text]) => `${text} ${status.last_result?.[key]}`)
        .join(" · ") || "바뀐 것 없음"
    : null;

  return (
    <section
      aria-label="iCloud 캘린더"
      className={`${card} flex max-w-[720px] flex-col gap-4 p-6`}
    >
      <div className="flex items-baseline gap-2">
        <h2 className="m-0 grow text-[20px] font-light tracking-[-0.02em] text-ink">
          iCloud 캘린더
        </h2>
        {data?.connected && (
          <span className="text-[12px] text-meta">
            {data.username} · 연결됨
          </span>
        )}
      </div>
      <p className="m-0 text-[13px] leading-relaxed text-text-3">
        모든 캘린더의 일정을 Argos로 가져오고, Argos에서 만든 일정은 "
        {data?.write_calendar ?? "Argos"}" 캘린더에만 써요.
        {data && data.poll_minutes > 0
          ? ` ${data.poll_minutes}분마다 자동으로 맞춰요.`
          : ""}{" "}
        다른 캘린더의 일정은 여기서 보기만 하고 캘린더 앱에서 고쳐요.
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
            <span className={label}>앱 전용 암호</span>
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
            애플 ID 암호가 아니라{" "}
            <a
              href="https://account.apple.com/account/manage"
              target="_blank"
              rel="noreferrer"
              className="underline"
            >
              애플 계정 관리
            </a>
            의 "앱 전용 암호"에서 만든 암호를 넣어 주세요. 암호는 이 Mac의
            키체인에만 저장돼요.
          </p>
          <ErrorText error={connect.error} />
          <div>
            <button
              type="submit"
              className={btn.cta}
              disabled={connect.isPending}
            >
              {connect.isPending ? "확인하는 중…" : "연결"}
            </button>
          </div>
        </form>
      )}

      {data?.connected && (
        <>
          <div className="flex flex-wrap items-center gap-3 rounded-2xl bg-page px-4 py-3 text-[13px]">
            <span className="grow text-text-2">
              {running
                ? "동기화하는 중…"
                : status?.last_sync_at
                  ? `마지막 동기화 ${fmt(status.last_sync_at, "M/d HH:mm")}${result ? ` · ${result}` : ""}`
                  : "아직 동기화하지 않았어요"}
            </span>
            <button
              type="button"
              className={btn.outline}
              disabled={running}
              onClick={() => sync.mutate(undefined)}
            >
              지금 동기화
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
              <span className={label}>캘린더 → 채널</span>
              {data.calendars.map((c) => (
                <div
                  key={c.url}
                  className="flex items-center gap-3 rounded-2xl border border-line-soft bg-card px-4 py-2.5"
                >
                  <span className="min-w-0 grow truncate text-[13.5px] text-ink">
                    {c.name}
                  </span>
                  <span className="text-[11.5px] whitespace-nowrap text-meta">
                    {c.writable ? "Argos가 씀" : "읽기 전용"}
                  </span>
                  <select
                    aria-label={`${c.name} 일정을 넣을 채널`}
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
                    <option value="">
                      # {personal?.name ?? "일상"} (기본)
                    </option>
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
                    "iCloud 연결을 끊을까요? 키체인의 암호를 지우고, 가져온 일정은 Argos에 남아요.",
                  )
                ) {
                  disconnect.mutate(undefined);
                }
              }}
            >
              연결 끊기
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
  if (e.start_date) return `${fmt(e.start_date, "M/d (EEE)")} 종일`;
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
      <span className={label}>충돌 {conflicts.data.length}건</span>
      {conflicts.data.map((c) => (
        <div
          key={c.id}
          className="flex flex-col gap-2.5 rounded-2xl border border-danger-line px-4 py-3"
        >
          <span className="text-[12.5px] text-text-2">
            앱과 "{c.calendar_name}" 캘린더에서 둘 다 고쳤어요. 어느 쪽을
            남길까요?
          </span>
          <div className="flex gap-2">
            {side("Argos", c.event)}
            {side("캘린더", c.remote)}
          </div>
          <div className="flex gap-2">
            <button
              type="button"
              className={btn.outline}
              disabled={resolve.isPending}
              onClick={() => resolve.mutate({ id: c.id, keep: "app" })}
            >
              Argos 버전 남기기
            </button>
            <button
              type="button"
              className={btn.outline}
              disabled={resolve.isPending}
              onClick={() => resolve.mutate({ id: c.id, keep: "calendar" })}
            >
              캘린더 버전 남기기
            </button>
          </div>
        </div>
      ))}
      <ErrorText error={resolve.error} />
    </div>
  );
}
