import { type FormEvent, useState } from "react";
import { useSearchParams } from "react-router";
import { AgentAvatar, agentInfo } from "./agents";
import {
  type Approval,
  type CalEvent,
  type Channel,
  type Debate,
  type InboxAccept,
  type InboxItem,
  type Task,
  useAcceptInbox,
  useCancelDebate,
  useChannels,
  useConfig,
  useDebateToNote,
  useDebateToTask,
  useReclassify,
  useResolveApproval,
  useUpdateTask,
} from "./api";
import { CheckButton, useCompleteTask } from "./complete";
import { dday, fmt, isoToLocalInput, localInputToIso } from "./dates";
import { PawIcon, ShieldIcon } from "./icons";
import { Markdown } from "./markdown";
import { PawTrail } from "./paws";
import { btn, Chip, card, DdayBadge, ErrorText, field, label } from "./ui";

type Suggestion = {
  type?: "task" | "event" | "idea" | "study_note";
  title?: string;
  due_at?: string | null;
  starts_at?: string | null;
  ends_at?: string | null;
  all_day_date?: string | null;
  channel_hint?: string | null;
  summary?: string;
  error?: string;
};

const TYPE_LABEL: Record<string, string> = {
  task: "할 일",
  event: "일정",
  idea: "아이디어",
  study_note: "공부 노트",
};

const channelLabel = (channel: Channel) =>
  channel.kind === "personal" ? "내 공간" : `# ${channel.name}`;

// --- suggestion ("알아봤어요") -------------------------------------------------------

/** Card for an inbox item: pending, failed, or a suggestion the user accepts or fixes.
 * `channel` is where it was typed; a course channel already decides the destination. */
export function SuggestionCard({
  item,
  channel,
}: {
  item: InboxItem;
  channel?: Channel;
}) {
  const config = useConfig();
  const reclassify = useReclassify();
  const s = (item.suggestion_json ?? {}) as Suggestion;

  if (item.status === "dismissed") {
    return <Quiet>넘긴 입력이에요.</Quiet>;
  }
  if (item.status === "new") {
    if (s.error) {
      return (
        <Quiet>
          <span className="grow">
            분류하지 못했어요 · 원문은 인박스에 보관됨
          </span>
          <button
            type="button"
            className={`${btn.outline} h-8 text-[12.5px]`}
            disabled={reclassify.isPending}
            onClick={() => reclassify.mutate(item.id)}
          >
            다시 시도
          </button>
        </Quiet>
      );
    }
    return config.data?.classifier_enabled ? (
      <Quiet>
        <PawTrail label="알아보는 중" />
      </Quiet>
    ) : (
      <Quiet>인박스에 보관됨 · 분류 모델이 설정되지 않았어요</Quiet>
    );
  }
  if (item.status === "accepted") {
    return <Quiet>정리됨</Quiet>;
  }
  return <Proposal item={item} suggestion={s} channel={channel} />;
}

function Quiet({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex max-w-[580px] items-center gap-2 rounded-2xl border border-dashed border-line px-4 py-2.5 text-[12.5px] text-text-3">
      {children}
    </div>
  );
}

function Proposal({
  item,
  suggestion: s,
  channel,
}: {
  item: InboxItem;
  suggestion: Suggestion;
  channel?: Channel;
}) {
  const channels = useChannels();
  const accept = useAcceptInbox();
  const [editing, setEditing] = useState(false);
  const [hidden, setHidden] = useState(false);
  const type = s.type ?? "task";
  const confidence = item.confidence ?? 0;
  const pickable =
    channels.data?.channels.filter((c) => c.kind !== "system") ?? [];
  const byName = pickable.find((c) => c.name === s.channel_hint);
  const target =
    byName ??
    (channel && channel.kind !== "system" ? channel : undefined) ??
    (channel?.name === "inbox"
      ? pickable.find((c) => c.kind === "personal")
      : undefined);
  const fromContext = target !== undefined && target.id === channel?.id;

  if (hidden) return <Quiet>인박스에 두었어요</Quiet>;

  return (
    <div className={`${card} flex max-w-[580px] flex-col gap-4 px-[22px] py-5`}>
      <div className="flex items-center gap-2">
        <span className="flex text-ink">
          <PawIcon />
        </span>
        <span className="text-[13.5px] font-medium text-ink">알아봤어요</span>
        <span className="grow text-[13.5px] text-text-3">
          {TYPE_LABEL[type]}(으)로 정리할까요?
        </span>
        <span className="font-mono text-[11px] text-meta">
          확신도 {confidence.toFixed(2)}
        </span>
        <span className="flex h-[3px] w-14 overflow-hidden rounded-sm bg-line-soft">
          <span
            className="bg-step-5"
            style={{ width: `${Math.round(confidence * 100)}%` }}
          />
        </span>
      </div>

      {editing ? (
        <FixForm
          item={item}
          suggestion={s}
          channels={pickable}
          defaultChannel={target?.id}
          onDone={() => setEditing(false)}
        />
      ) : (
        <>
          <div className="grid grid-cols-[56px_minmax(0,1fr)] items-center gap-x-3.5 gap-y-2.5 text-[13.5px]">
            <span className="text-meta">유형</span>
            <span>
              <Chip>{TYPE_LABEL[type]}</Chip>
            </span>
            <span className="text-meta">제목</span>
            <span className="text-[18px] font-light tracking-[-0.02em] text-ink">
              {s.title ?? item.raw_text}
            </span>
            <When suggestion={s} />
            <span className="text-meta">채널</span>
            <span>
              {target ? (
                <>
                  {channelLabel(target)}
                  {fromContext && (
                    <span className="text-[12px] text-meta">
                      {" "}
                      · 채널 맥락으로 확정
                    </span>
                  )}
                </>
              ) : (
                <span className="text-text-3">
                  정해지지 않음 · 고치기에서 골라 주세요
                </span>
              )}
            </span>
          </div>
          <ErrorText error={accept.error} />
          <div className="flex items-center gap-2">
            <button
              type="button"
              className={btn.cta}
              disabled={accept.isPending || !target}
              onClick={() =>
                accept.mutate({ id: item.id, channel_id: target?.id })
              }
            >
              {type === "study_note" ? "노트로 저장" : "추가하기"}
            </button>
            {type !== "study_note" && (
              <button
                type="button"
                className={btn.outline}
                onClick={() => setEditing(true)}
              >
                고치기
              </button>
            )}
            <button
              type="button"
              className={btn.ghost}
              onClick={() => setHidden(true)}
            >
              인박스에 두기
            </button>
            <span className="grow" />
            <span className="text-[11.5px] text-meta">
              원문은 인박스에 보관됨
            </span>
          </div>
        </>
      )}
    </div>
  );
}

function When({ suggestion: s }: { suggestion: Suggestion }) {
  if (s.type === "event" && (s.starts_at || s.all_day_date)) {
    return (
      <>
        <span className="text-meta">일시</span>
        <span className="flex items-center gap-2">
          {s.starts_at
            ? `${fmt(s.starts_at, "M월 d일 (EEE) HH:mm")}${s.ends_at ? `–${fmt(s.ends_at, "HH:mm")}` : ""}`
            : `${fmt(`${s.all_day_date}T12:00:00`, "M월 d일 (EEE)")} 종일`}
        </span>
      </>
    );
  }
  if (s.due_at) {
    return (
      <>
        <span className="text-meta">마감</span>
        <span className="flex items-center gap-2">
          {fmt(s.due_at, "M월 d일 (EEE) HH:mm")}{" "}
          <DdayBadge days={dday(s.due_at)} />
        </span>
      </>
    );
  }
  return null;
}

/** "고치기": the user's version replaces the suggestion's fields on accept. */
function FixForm({
  item,
  suggestion: s,
  channels,
  defaultChannel,
  onDone,
}: {
  item: InboxItem;
  suggestion: Suggestion;
  channels: Channel[];
  defaultChannel?: string;
  onDone: () => void;
}) {
  const accept = useAcceptInbox();
  const [type, setType] = useState<"task" | "event" | "idea">(
    s.type === "event" || s.type === "idea" ? s.type : "task",
  );
  const [title, setTitle] = useState(s.title ?? item.raw_text);
  const [due, setDue] = useState(s.due_at ? isoToLocalInput(s.due_at) : "");
  const [start, setStart] = useState(
    s.starts_at ? isoToLocalInput(s.starts_at) : "",
  );
  const [end, setEnd] = useState(s.ends_at ? isoToLocalInput(s.ends_at) : "");
  const [day, setDay] = useState(s.all_day_date ?? "");
  const [channelId, setChannelId] = useState(defaultChannel ?? "");

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const body: InboxAccept & { id: string } = {
      id: item.id,
      type,
      title: title.trim(),
      channel_id: channelId || null,
    };
    if (type === "event") {
      if (start) {
        body.starts_at = localInputToIso(start);
        body.ends_at = end ? localInputToIso(end) : null;
      } else {
        body.start_date = day || null;
      }
    } else {
      body.due_at = due ? localInputToIso(due) : null;
    }
    accept.mutate(body, { onSuccess: onDone });
  };

  return (
    <form onSubmit={submit} className="flex flex-col gap-3.5">
      <div className="grid grid-cols-2 gap-3.5">
        <label className="flex flex-col gap-1">
          <span className={label}>유형</span>
          <select
            className={field}
            value={type}
            onChange={(e) => setType(e.target.value as typeof type)}
          >
            <option value="task">할 일</option>
            <option value="event">일정</option>
            <option value="idea">아이디어 (백로그)</option>
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className={label}>채널</span>
          <select
            className={field}
            value={channelId}
            onChange={(e) => setChannelId(e.target.value)}
            required
          >
            <option value="">골라 주세요</option>
            {channels.map((c) => (
              <option key={c.id} value={c.id}>
                {channelLabel(c)}
              </option>
            ))}
          </select>
        </label>
      </div>
      <label className="flex flex-col gap-1">
        <span className={label}>제목</span>
        <input
          className={field}
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          required
        />
      </label>
      {type === "event" ? (
        <div className="grid grid-cols-3 gap-3.5">
          <label className="flex flex-col gap-1">
            <span className={label}>시작</span>
            <input
              type="datetime-local"
              className={field}
              value={start}
              onChange={(e) => setStart(e.target.value)}
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className={label}>끝</span>
            <input
              type="datetime-local"
              className={field}
              value={end}
              onChange={(e) => setEnd(e.target.value)}
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className={label}>또는 종일</span>
            <input
              type="date"
              className={field}
              value={day}
              onChange={(e) => setDay(e.target.value)}
            />
          </label>
        </div>
      ) : (
        <label className="flex flex-col gap-1">
          <span className={label}>마감 (선택)</span>
          <input
            type="datetime-local"
            className={field}
            value={due}
            onChange={(e) => setDue(e.target.value)}
          />
        </label>
      )}
      <ErrorText error={accept.error} />
      <div className="flex gap-2">
        <button type="submit" className={btn.cta} disabled={accept.isPending}>
          추가하기
        </button>
        <button type="button" className={btn.ghost} onClick={onDone}>
          취소
        </button>
      </div>
    </form>
  );
}

// --- object cards ----------------------------------------------------------------

export function TaskRefCard({ task }: { task: Task }) {
  const [params, setParams] = useSearchParams();
  const open = () => {
    const next = new URLSearchParams(params);
    next.delete("thread");
    next.set("task", task.id);
    setParams(next);
  };
  const done = task.status === "done";
  const completion = useCompleteTask();
  const reopen = useUpdateTask();
  return (
    // biome-ignore lint/a11y/useSemanticElements: holds its own check button, so not a <button>
    <div
      role="button"
      tabIndex={0}
      onClick={open}
      onKeyDown={(e) => e.key === "Enter" && open()}
      className={`${card} flex max-w-[520px] cursor-pointer items-center gap-3 px-[18px] py-3.5 text-left`}
    >
      <CheckButton
        checked={done || completion.pending.has(task.id)}
        label={done ? `${task.title} 다시 열기` : `${task.title} 완료 처리`}
        size={24}
        onClick={() =>
          done
            ? reopen.mutate({ id: task.id, status: "todo" })
            : completion.complete(task)
        }
      />
      <span className="flex min-w-0 grow flex-col gap-0.5">
        <span
          className={`font-medium text-ink ${done || completion.pending.has(task.id) ? "text-meta line-through" : ""}`}
        >
          {task.title}
        </span>
        <span className="text-[12px] text-text-3">
          할 일{task.due_at ? ` · ${fmt(task.due_at, "M/d (EEE) HH:mm")}` : ""}
        </span>
      </span>
      {task.due_at && !done && <DdayBadge days={dday(task.due_at)} />}
    </div>
  );
}

export function EventRefCard({ event }: { event: CalEvent }) {
  const at = event.starts_at ?? `${event.start_date}T12:00:00`;
  return (
    <div
      className={`${card} flex max-w-[520px] items-center gap-4 py-3.5 pr-[18px] pl-3.5`}
    >
      <div className="flex size-[52px] flex-col items-center justify-center rounded-xl bg-inset">
        <span className="text-[10.5px] text-meta">{fmt(at, "M월 · EEE")}</span>
        <span className="text-[22px] leading-tight font-light text-ink">
          {fmt(at, "d")}
        </span>
      </div>
      <div className="flex grow flex-col gap-0.5">
        <div className="font-medium text-ink">{event.title}</div>
        <div className="font-mono text-[12px] text-text-3">
          {event.all_day
            ? "종일"
            : `${fmt(event.starts_at ?? "", "HH:mm")}${event.ends_at ? ` – ${fmt(event.ends_at, "HH:mm")}` : ""}`}
        </div>
      </div>
    </div>
  );
}

/** Compact suggestion line for the Today inbox (Today design): accept in place, picking
 * a channel when the suggestion has none. */
export function InboxRow({ item }: { item: InboxItem }) {
  const channels = useChannels();
  const accept = useAcceptInbox();
  const reclassify = useReclassify();
  const [picked, setPicked] = useState("");
  const s = (item.suggestion_json ?? {}) as Suggestion;
  const pickable =
    channels.data?.channels.filter((c) => c.kind !== "system") ?? [];
  const captured = pickable.find((c) => c.id === item.channel_id);
  const target =
    pickable.find((c) => c.name === s.channel_hint) ??
    captured ??
    pickable.find((c) => c.kind === "personal");

  if (item.status === "new") {
    return s.error ? (
      <div className="flex items-center gap-2 text-[12px] text-text-3">
        <span className="grow">분류하지 못했어요</span>
        <button
          type="button"
          className={`${btn.ghost} h-8 text-[12.5px]`}
          onClick={() => reclassify.mutate(item.id)}
        >
          다시 시도
        </button>
      </div>
    ) : null;
  }
  if (item.status !== "suggested") return null;

  const when = s.due_at
    ? fmt(s.due_at, "M/d (EEE)")
    : s.starts_at
      ? fmt(s.starts_at, "M/d (EEE) HH:mm")
      : s.all_day_date
        ? fmt(`${s.all_day_date}T12:00:00`, "M/d (EEE)")
        : null;
  const parts = [
    TYPE_LABEL[s.type ?? "task"],
    when,
    target ? channelLabel(target) : "채널 미정",
  ];
  const note = s.type === "study_note";

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2 rounded-xl border border-line-soft bg-card px-3 py-2 text-[12.5px]">
        <span className="flex text-ink">
          <PawIcon />
        </span>
        <span className="grow">{parts.filter(Boolean).join(" · ")}</span>
        <span className="font-mono text-[11px] text-meta">
          {(item.confidence ?? 0).toFixed(2)}
        </span>
      </div>
      <div className="flex items-center gap-1.5">
        {!target && (
          <select
            aria-label="채널 고르기"
            className={`${field} h-8 w-auto min-w-0 grow text-[12.5px]`}
            value={picked}
            onChange={(e) => setPicked(e.target.value)}
          >
            <option value="">채널 고르기</option>
            {pickable.map((c) => (
              <option key={c.id} value={c.id}>
                {channelLabel(c)}
              </option>
            ))}
          </select>
        )}
        <button
          type="button"
          className={`${btn.cta} h-8 shrink-0 px-3.5 text-[12.5px] whitespace-nowrap`}
          disabled={accept.isPending || (!target && !picked)}
          onClick={() =>
            accept.mutate({ id: item.id, channel_id: target?.id ?? picked })
          }
        >
          {note
            ? target
              ? "노트로 저장"
              : "채널 골라 저장"
            : target
              ? "추가하기"
              : "채널 골라 추가"}
        </button>
      </div>
      <ErrorText error={accept.error} />
    </div>
  );
}

// --- approval (PLAN P5) -------------------------------------------------------------

const ACTION_VERB: Record<string, string> = {
  delete_task: "승인하고 삭제",
  delete_event: "승인하고 삭제",
};

/** A destructive action an agent asked for. Nothing happens until the user decides. */
export function ApprovalCard({ approval }: { approval: Approval }) {
  const resolve = useResolveApproval();
  const payload = approval.payload_json as {
    title?: string;
    when?: string | null;
  };
  const pending = approval.status === "pending";
  const when = payload.when
    ? payload.when.length === 10
      ? fmt(`${payload.when}T12:00:00`, "M/d (EEE)")
      : fmt(payload.when, "M/d (EEE) HH:mm")
    : null;

  return (
    <div
      className={`${card} flex max-w-[580px] flex-col gap-3.5 px-5 py-[18px]`}
    >
      <div className="flex items-center gap-2 text-danger">
        <ShieldIcon />
        <span className="text-[13px] font-medium">
          {pending ? "승인 필요" : STATUS_TEXT[approval.status]}
        </span>
        <span className="font-mono text-[12px] text-meta">
          {approval.action}
        </span>
        <span className="grow" />
        <span className="text-[12px] text-meta">
          {agentInfo(approval.requested_by).name} 요청
        </span>
      </div>
      <div className="flex items-center gap-2.5 rounded-xl bg-inset px-3.5 py-3">
        <span
          className={`font-medium text-ink ${approval.status === "approved" ? "line-through decoration-danger" : pending ? "line-through decoration-danger" : ""}`}
        >
          {payload.title ?? approval.summary}
        </span>
        {when && (
          <span className="font-mono text-[12px] text-text-3">{when}</span>
        )}
      </div>
      {approval.error && (
        <p className="m-0 text-[12.5px] text-danger">{approval.error}</p>
      )}
      {pending && (
        <div className="flex gap-2">
          <button
            type="button"
            disabled={resolve.isPending}
            onClick={() => resolve.mutate({ id: approval.id, approve: true })}
            className="inline-flex h-9 cursor-pointer items-center rounded-full bg-danger px-[18px] text-[13.5px] font-medium text-on-dark disabled:opacity-50"
          >
            {ACTION_VERB[approval.action] ?? "승인"}
          </button>
          <button
            type="button"
            disabled={resolve.isPending}
            onClick={() => resolve.mutate({ id: approval.id, approve: false })}
            className={btn.outline}
          >
            거절
          </button>
        </div>
      )}
      <ErrorText error={resolve.error} />
    </div>
  );
}

const STATUS_TEXT: Record<string, string> = {
  approved: "승인됨",
  rejected: "거절됨",
  failed: "실행 실패",
  pending: "승인 필요",
};

// --- debate (PLAN Phase 10) ----------------------------------------------------------

const MODE_TEXT: Record<Debate["mode"], string> = {
  round_robin: "돌아가며",
  pro_con: "찬반",
  moderated: "사회자 진행",
};

const DEBATE_STATE: Record<Debate["status"], string> = {
  running: "진행 중",
  done: "끝남",
  cancelled: "중단됨",
  error: "오류로 멈춤",
};

/** The debate's card on its opening message: who, how far, and what came of it. */
export function DebateCard({
  debate,
  onThread,
}: {
  debate: Debate;
  onThread?: () => void;
}) {
  const cancel = useCancelDebate();
  const toTask = useDebateToTask();
  const toNote = useDebateToNote();
  const running = debate.status === "running";
  // The "## 결론" part of the moderator's summary (the whole summary if it has none).
  const section = (title: string) =>
    debate.summary
      ?.split(new RegExp(`^##\\s*${title}\\s*$`, "m"))[1]
      ?.split(/^##\s/m)[0]
      ?.trim();
  const conclusion = debate.summary
    ? (section("결론") ?? debate.summary.trim())
    : null;
  const improved = section("개선안");
  return (
    <div className={`${card} flex max-w-[640px] flex-col gap-3 px-[18px] py-4`}>
      <div className="flex items-center gap-2">
        <div className="flex -space-x-1.5">
          {debate.participants.map((p) => (
            <span key={p} className="rounded-full ring-2 ring-card">
              <AgentAvatar id={p} size={24} />
            </span>
          ))}
        </div>
        <span className="shrink-0 text-[13.5px] font-medium text-ink">
          토론
        </span>
        <span
          className={`min-w-0 grow text-right text-[11.5px] ${debate.status === "error" ? "text-danger" : "text-meta"}`}
        >
          {MODE_TEXT[debate.mode]} · {debate.rounds_done}/{debate.max_rounds}
          라운드 · {DEBATE_STATE[debate.status]}
        </span>
      </div>
      <div className="text-[14px] text-text">{debate.topic}</div>
      <div className="text-[12px] text-text-3">
        {debate.participants.map((p) => agentInfo(p).name).join(" · ")} · 사회{" "}
        {agentInfo(debate.moderator).name}
        {debate.use_tools ? " · 도구 사용" : ""}
      </div>
      {running && (
        <div className="flex items-center gap-2 text-[12.5px] text-text-3">
          <PawTrail />
          발언이 스레드에 이어지고 있어요. 스레드에 쓰면 다음 차례부터 반영돼요.
        </div>
      )}
      {conclusion && (
        <div className="flex flex-col gap-1 rounded-xl bg-page px-3.5 py-2.5 text-[13px] leading-relaxed text-text-2">
          <span className={label}>결론</span>
          <div className="line-clamp-6">
            <Markdown text={conclusion} />
          </div>
        </div>
      )}
      {improved && (
        <details className="rounded-xl bg-page px-3.5 py-2.5 text-[13px] text-text-2">
          <summary className={`${label} cursor-pointer`}>
            개선안 (최종본)
          </summary>
          <div className="mt-2 leading-relaxed">
            <Markdown text={improved} />
          </div>
        </details>
      )}
      {debate.error && (
        <p role="alert" className="m-0 text-[12.5px] text-danger">
          {debate.error}
        </p>
      )}
      <ErrorText error={cancel.error ?? toTask.error ?? toNote.error} />
      <div className="flex flex-wrap items-center gap-2">
        {onThread && (
          <button type="button" className={btn.outline} onClick={onThread}>
            스레드 보기
          </button>
        )}
        {running && (
          <button
            type="button"
            className={btn.ghost}
            disabled={cancel.isPending}
            onClick={() => cancel.mutate(debate.id)}
          >
            중단
          </button>
        )}
        {debate.summary && (
          <>
            <button
              type="button"
              className={btn.ghost}
              disabled={toTask.isPending || Boolean(debate.summary_task_id)}
              onClick={() => toTask.mutate(debate.id)}
            >
              {debate.summary_task_id ? "할 일로 만듦" : "할 일로"}
            </button>
            <button
              type="button"
              className={btn.ghost}
              disabled={toNote.isPending || Boolean(debate.summary_note_id)}
              onClick={() => toNote.mutate(debate.id)}
            >
              {debate.summary_note_id ? "노트로 저장함" : "노트로"}
            </button>
          </>
        )}
      </div>
    </div>
  );
}
