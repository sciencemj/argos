import {
  type FormEvent,
  type KeyboardEvent,
  useEffect,
  useRef,
  useState,
} from "react";
import { useSearchParams } from "react-router";
import { useLive } from "./agentStream";
import { AgentAvatar, agentInfo } from "./agents";
import {
  type Channel,
  cancelRun,
  type Message,
  useAgentSettings,
  useAgents,
  useChannels,
  useConvertMessage,
  useMessages,
  usePinMessage,
  usePostMessage,
} from "./api";
import {
  ApprovalCard,
  DebateCard,
  EventRefCard,
  SuggestionCard,
  TaskRefCard,
} from "./cards";
import { useCompleteTask } from "./complete";
import { fmt, localInputToIso } from "./dates";
import { t, tr, tt } from "./i18n";
import {
  CalendarIcon,
  CheckIcon,
  DogIcon,
  KanbanIcon,
  PinIcon,
  SendIcon,
} from "./icons";
import { Markdown } from "./markdown";
import { PawTrail } from "./paws";
import { btn, card, Dialog, ErrorText, field, label } from "./ui";

const COMMANDS = [
  { name: "/task", usage: tr("제목 [날짜] [시간] — 할 일") },
  { name: "/event", usage: tr("제목 날짜 [시작-끝] — 일정") },
  { name: "/note", usage: tr("내용 — 공부 노트") },
  { name: "/ask", usage: tr("질문 — 기본 에이전트에게") },
  { name: "/job", usage: tr("@claude|@codex 할 일 [--dir 경로] — 코딩 잡") },
  { name: "/debate", usage: tr("@a @b 주제 [--mode] [--rounds N] — 토론") },
];
const SLASH = COMMANDS.map((c) => c.name);

export function useOpenThread() {
  const [params, setParams] = useSearchParams();
  return (id: string) => {
    const next = new URLSearchParams(params);
    next.delete("task");
    next.set("thread", id);
    setParams(next);
  };
}

/** The feed's reading column: centered on wide screens, header and input line up with it. */
export const FEED_COLUMN = "mx-auto w-full max-w-[960px]";

/** Channel feed: messages are the input layer, the cards show the objects (PLAN P4). */
export function Feed({ channel }: { channel: Channel }) {
  const channels = useChannels();
  const messages = useMessages(
    channel.id,
    channel.kind === "system" && channel.name === "inbox",
  );
  const scroller = useRef<HTMLDivElement>(null);
  const openThread = useOpenThread();
  const pages = messages.data?.pages ?? [];
  const items = [...pages].reverse().flatMap((p) => p.items);
  const pinned = items.filter((m) => m.pinned);
  const lastId = items.at(-1)?.id;

  // Follow the conversation: jump to the newest message when one arrives.
  useEffect(() => {
    if (lastId)
      scroller.current?.scrollTo({ top: scroller.current.scrollHeight });
  }, [lastId]);

  return (
    <>
      {pinned.length > 0 && (
        <div className={`${FEED_COLUMN} mb-2 px-8`}>
          <div className="flex items-center gap-2 overflow-hidden rounded-full border border-line-soft bg-card px-4 py-2 text-[12.5px] text-text-3">
            <PinIcon size={13} />
            <span className="truncate">
              {pinned.map((m) => m.body).join(" · ")}
            </span>
          </div>
        </div>
      )}
      <section
        ref={scroller}
        aria-label={tr("메시지")}
        className="flex min-h-0 grow flex-col overflow-y-auto"
      >
        <div
          className={`${FEED_COLUMN} flex grow flex-col gap-[26px] px-8 pt-2 pb-[18px]`}
        >
          <div className="grow" />
          {messages.hasNextPage && (
            <button
              type="button"
              className={`${btn.ghost} self-center`}
              onClick={() => void messages.fetchNextPage()}
            >
              {tr("이전 메시지 더 보기")}
            </button>
          )}
          {messages.isSuccess && items.length === 0 && (
            <div className="text-[13px] text-text-3">
              {channel.name === "inbox" ? tr("내 공간") : `#${channel.name}`}
              {tr(
                "에 첫 메시지를 적어 보세요. 그냥 쓰면 Argos가 할 일·일정으로 정리해요.",
              )}
            </div>
          )}
          {items.map((m) => (
            <MessageItem
              key={m.id}
              message={m}
              channel={
                channels.data?.channels.find((c) => c.id === m.channel_id) ??
                channel
              }
              onThread={openThread}
            />
          ))}
        </div>
      </section>
      <Composer channel={channel} />
    </>
  );
}

function Author({ message }: { message: Message }) {
  if (message.author_type === "agent")
    return <AgentAvatar id={message.author_id} />;
  if (message.author_type === "system") {
    return (
      <div className="flex size-9 shrink-0 items-center justify-center rounded-full bg-step-5 text-on-dark">
        <DogIcon size={20} />
      </div>
    );
  }
  return (
    <div className="flex size-9 shrink-0 items-center justify-center rounded-full border border-line-soft bg-inset text-[12px] font-semibold text-text">
      {tr("나")}
    </div>
  );
}

export function MessageItem({
  message,
  channel,
  onThread,
  inlineReplies = true,
}: {
  message: Message;
  channel: Channel;
  onThread?: (id: string) => void;
  /** The thread panel lists replies itself, so it turns the inline answer cards off. */
  inlineReplies?: boolean;
}) {
  const system = message.author_type === "system";
  const agent =
    message.author_type === "agent" ? agentInfo(message.author_id) : null;
  const { inbox_item, task, event, approval, debate } = message.ref ?? {};
  return (
    <article className="group relative flex gap-3.5">
      <Author message={message} />
      <div className="flex min-w-0 grow flex-col gap-2.5">
        <div className="flex items-baseline gap-2">
          <span
            className="font-medium"
            style={{ color: agent?.text ?? "var(--ink)" }}
          >
            {agent ? agent.name : system ? "Argos" : tr("나")}
          </span>
          {(system || agent) && (
            <span className="rounded-full border border-line-soft px-[7px] text-[11px] text-meta">
              {agent ? tr("에이전트") : tr("시스템")}
            </span>
          )}
          <span className="font-mono text-[11px] text-meta">
            {fmt(message.created_at, "HH:mm")}
          </span>
          {message.pinned && (
            <span className="text-meta" title={tr("고정됨")}>
              <PinIcon size={12} />
            </span>
          )}
        </div>
        {message.run ? (
          <AgentBody message={message} />
        ) : system || message.body.includes("```") ? (
          // Argos's own notes (the weekly review) and code or documents pasted for
          // review are Markdown.
          <div
            className={`leading-[1.65] ${system ? "text-[14px] text-text-2" : "text-[15px] text-text"}`}
          >
            <Markdown text={message.body} />
          </div>
        ) : (
          <div
            className={`whitespace-pre-wrap ${system || agent ? "text-text-2" : "text-[15px] text-text"}`}
          >
            {message.body}
          </div>
        )}
        {inlineReplies &&
          message.agent_replies &&
          message.agent_replies.length > 0 && (
            <div
              className={`grid gap-3 ${message.agent_replies.length > 1 ? "grid-cols-2" : "max-w-[640px] grid-cols-1"}`}
            >
              {message.agent_replies.map((r) => (
                <AgentReplyCard key={r.id} message={r} />
              ))}
            </div>
          )}
        {inbox_item && <SuggestionCard item={inbox_item} channel={channel} />}
        {task && <TaskRefCard task={task} />}
        {event && <EventRefCard event={event} />}
        {approval && <ApprovalCard approval={approval} />}
        {debate && (
          <DebateCard
            debate={debate}
            onThread={onThread ? () => onThread(message.id) : undefined}
          />
        )}
        {onThread && message.reply_count > 0 && (
          <button
            type="button"
            onClick={() => onThread(message.id)}
            className="cursor-pointer self-start text-[12.5px] font-medium text-text-2 underline underline-offset-[3px]"
          >
            {t(
              `답글 ${message.reply_count}`,
              `${message.reply_count} ${message.reply_count === 1 ? "reply" : "replies"}`,
            )}
          </button>
        )}
      </div>
      {onThread && <QuickActions message={message} onThread={onThread} />}
    </article>
  );
}

/** The run's state; while an agent works, paw prints walk beside what it is doing. */
function RunLabel({
  message,
  liveStatus,
}: {
  message: Message;
  liveStatus?: string;
}) {
  const text = runLabel(message, liveStatus);
  const working =
    message.run?.status === "running" || message.run?.status === "queued";
  return working ? (
    <PawTrail label={text.replace(/…$/, "")} />
  ) : (
    <span>{text}</span>
  );
}

function runLabel(message: Message, liveStatus?: string): string {
  const run = message.run;
  if (!run) return "";
  if (run.status === "queued")
    return tr("잡 대기 중 · 앞 잡이 끝나면 시작해요");
  if (run.status === "running") {
    return liveStatus && liveStatus !== "thinking"
      ? liveStatus
      : tr("입력 중…");
  }
  if (run.status === "cancelled") return tr("중단됨");
  if (run.status === "error") return tr("오류");
  const seconds = run.finished_at
    ? Math.max(
        1,
        Math.round(
          (Date.parse(run.finished_at) - Date.parse(run.started_at)) / 1000,
        ),
      )
    : null;
  return seconds ? tt`완료 · ${seconds}초` : tr("완료");
}

/** An agent's answer: live tokens while its run streams, the stored text after. */
function AgentBody({
  message,
  compact,
}: {
  message: Message;
  compact?: boolean;
}) {
  const live = useLive(message.id);
  const run = message.run;
  const running = run?.status === "running" || run?.status === "queued";
  const text = running ? (live?.text ?? "") : message.body;
  return (
    <div className="flex flex-col gap-1.5">
      {!compact && (
        <div className="flex items-center gap-2 text-[11.5px] text-meta">
          <RunLabel message={message} liveStatus={live?.status} />
          {running && run && (
            <CancelButton
              runId={run.id}
              name={agentInfo(message.author_id).name}
            />
          )}
        </div>
      )}
      {(text || running) && (
        <div className="text-[13.5px] leading-[1.65] text-text-2">
          <Markdown text={text} />
          {running && (
            <span className="ml-[3px] inline-block h-[15px] w-[2px] animate-pulse bg-ink align-[-2px]" />
          )}
        </div>
      )}
      {run?.status === "error" && run.error && (
        <p role="alert" className="m-0 text-[12.5px] text-danger">
          {run.error}
        </p>
      )}
      {run?.log && !running && (
        <details className="text-[12px] text-text-3">
          <summary className="cursor-pointer">
            {t(
              `작업 로그 ${run.log.split("\n").length}줄`,
              `Job log · ${run.log.split("\n").length} lines`,
            )}{" "}
            {run.workspace && (
              <span className="ml-2 font-mono text-meta">{run.workspace}</span>
            )}
          </summary>
          <pre className="mt-2 max-h-72 overflow-auto rounded-lg bg-inset p-2.5 font-mono text-[11px] leading-[1.6] whitespace-pre-wrap text-text-2">
            {run.log}
          </pre>
        </details>
      )}
    </div>
  );
}

function CancelButton({ runId, name }: { runId: string; name: string }) {
  return (
    <button
      type="button"
      aria-label={tt`${name} 응답 중단`}
      onClick={() => void cancelRun(runId)}
      className="h-[26px] cursor-pointer rounded-full border border-line bg-card px-2.5 text-[11.5px] text-text-2 hover:text-ink"
    >
      {tr("중단")}
    </button>
  );
}

/** First-round answer under the message that called the agent (Main design cards). */
function AgentReplyCard({ message }: { message: Message }) {
  const agent = agentInfo(message.author_id);
  const live = useLive(message.id);
  const running =
    message.run?.status === "running" || message.run?.status === "queued";
  return (
    <div className={`${card} flex flex-col gap-2.5 px-[18px] py-4`}>
      <div className="flex items-center gap-2">
        <AgentAvatar id={message.author_id} size={24} />
        <span
          className="text-[13.5px] font-medium"
          style={{ color: agent.text }}
        >
          {agent.name}
        </span>
        <span className="grow" />
        <span className="text-[11.5px] text-meta">
          <RunLabel message={message} liveStatus={live?.status} />
        </span>
        {running && message.run && (
          <CancelButton runId={message.run.id} name={agent.name} />
        )}
      </div>
      <AgentBody message={message} compact />
    </div>
  );
}

/** Hover toolbar (Main design): ✅ done, 📅 to event, 🗂 to kanban, 📌 pin, thread. */
function QuickActions({
  message,
  onThread,
}: {
  message: Message;
  onThread: (id: string) => void;
}) {
  const pin = usePinMessage();
  const convert = useConvertMessage();
  const completion = useCompleteTask();
  const [eventOpen, setEventOpen] = useState(false);
  const ref = message.ref ?? {};
  const task = ref.task;
  const promotable = !ref.task && !ref.event && message.author_type === "user";
  const icon = btn.icon;

  return (
    <div
      role="toolbar"
      aria-label={tr("빠른 액션")}
      className="absolute -top-2 right-0 hidden gap-0.5 rounded-full border border-line-soft bg-card p-[3px] shadow-raised group-focus-within:flex group-hover:flex"
    >
      {task && task.status !== "done" && (
        <button
          type="button"
          aria-label={tr("완료")}
          className={icon}
          onClick={() => completion.complete(task)}
        >
          <CheckIcon />
        </button>
      )}
      {promotable && (
        <>
          <button
            type="button"
            aria-label={tr("일정으로")}
            className={icon}
            onClick={() => setEventOpen(true)}
          >
            <CalendarIcon />
          </button>
          <button
            type="button"
            aria-label={tr("칸반으로")}
            className={icon}
            onClick={() => convert.mutate({ id: message.id, kind: "task" })}
          >
            <KanbanIcon />
          </button>
        </>
      )}
      <button
        type="button"
        aria-label={message.pinned ? tr("고정 해제") : tr("고정")}
        aria-pressed={message.pinned}
        className={`${icon} ${message.pinned ? "text-ink" : ""}`}
        onClick={() => pin.mutate({ id: message.id, pinned: !message.pinned })}
      >
        <PinIcon />
      </button>
      <button
        type="button"
        className="h-8 cursor-pointer rounded-full px-2.5 text-[12px] text-text-3 hover:bg-inset hover:text-ink"
        onClick={() => onThread(message.id)}
      >
        {tr("스레드")}
      </button>
      <EventFromMessage
        message={message}
        open={eventOpen}
        onClose={() => setEventOpen(false)}
      />
    </div>
  );
}

function EventFromMessage({
  message,
  open,
  onClose,
}: {
  message: Message;
  open: boolean;
  onClose: () => void;
}) {
  const convert = useConvertMessage();
  const suggested = (message.ref?.inbox_item?.suggestion_json ?? {}) as {
    title?: string;
  };
  const [title, setTitle] = useState(
    suggested.title ?? message.body.slice(0, 80),
  );
  const [allDay, setAllDay] = useState(false);
  const [day, setDay] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const when = allDay
      ? { start_date: day }
      : {
          starts_at: localInputToIso(start),
          ends_at: end ? localInputToIso(end) : null,
        };
    convert.mutate(
      { id: message.id, kind: "event", title: title.trim(), ...when },
      {
        onSuccess: onClose,
      },
    );
  };

  return (
    <Dialog open={open} onClose={onClose} title={tr("일정으로 만들기")}>
      <form onSubmit={submit} className="flex flex-col gap-4">
        <label className="flex flex-col gap-1">
          <span className={label}>{tr("제목")}</span>
          <input
            className={field}
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            required
          />
        </label>
        <label className="flex items-center gap-2 text-[13px] text-text-2">
          <input
            type="checkbox"
            checked={allDay}
            onChange={(e) => setAllDay(e.target.checked)}
          />
          {tr("종일")}
        </label>
        {allDay ? (
          <label className="flex flex-col gap-1">
            <span className={label}>{tr("날짜")}</span>
            <input
              type="date"
              className={field}
              value={day}
              onChange={(e) => setDay(e.target.value)}
              required
            />
          </label>
        ) : (
          <div className="grid grid-cols-2 gap-3.5">
            <label className="flex flex-col gap-1">
              <span className={label}>{tr("시작")}</span>
              <input
                type="datetime-local"
                className={field}
                value={start}
                onChange={(e) => setStart(e.target.value)}
                required
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className={label}>{tr("끝 (선택)")}</span>
              <input
                type="datetime-local"
                className={field}
                value={end}
                onChange={(e) => setEnd(e.target.value)}
              />
            </label>
          </div>
        )}
        <ErrorText error={convert.error} />
        <div className="flex justify-end gap-2">
          <button type="button" className={btn.ghost} onClick={onClose}>
            {tr("취소")}
          </button>
          <button
            type="submit"
            className={btn.cta}
            disabled={convert.isPending}
          >
            {tr("추가하기")}
          </button>
        </div>
      </form>
    </Dialog>
  );
}

/** Enter sends, Shift+Enter breaks the line; Enter while a Korean syllable is still
 * being composed only commits the syllable. */
const MENTION_AT_END = /(^|\s)@([\w가-힣-]*)$/;

/** Message box of a channel feed, or of a thread (`threadRootId`): the same slash
 * commands and @mentions work in both. */
export function Composer({
  channel,
  threadRootId,
  narrow = false,
}: {
  channel: Channel;
  threadRootId?: string;
  narrow?: boolean;
}) {
  const post = usePostMessage();
  const agents = useAgents();
  const settings = useAgentSettings();
  const [text, setText] = useState("");
  const input = useRef<HTMLTextAreaElement>(null);
  const dm = channel.kind === "dm";
  const dmAgent = agents.data?.find((a) => a.id === channel.default_agent_id);
  const channelDefault = agents.data?.find(
    (a) => a.id === channel.default_agent_id,
  );
  const fallback = agents.data?.find(
    (a) => a.name === settings.data?.default_agent,
  );
  const askTarget = channelDefault ?? fallback;

  // Completion (Tab or Enter, ↑↓ to choose, Esc to close): "@cl" at the caret →
  // agents whose handle or display name starts with it; "/de" as the first word →
  // slash commands.
  const typed = MENTION_AT_END.exec(text)?.[2];
  const command = dm ? undefined : /^\/(\w*)$/.exec(text)?.[1];
  const [active, setActive] = useState(0);
  const [dismissed, setDismissed] = useState<string | null>(null);
  const options: {
    key: string;
    main: string;
    side: string;
    agent?: string;
    pick: () => void;
  }[] =
    dismissed === text
      ? []
      : command !== undefined
        ? COMMANDS.filter((c) =>
            c.name.slice(1).startsWith(command.toLowerCase()),
          ).map((c) => ({
            key: c.name,
            main: c.name,
            side: c.usage,
            pick: () => complete(`${c.name} `),
          }))
        : typed !== undefined
          ? (agents.data ?? [])
              .filter((a) =>
                [a.name, a.display_name].some((n) =>
                  n.toLowerCase().startsWith(typed.toLowerCase()),
                ),
              )
              .map((a) => ({
                key: a.id,
                main: a.display_name,
                side: `@${a.name}`,
                agent: a.name,
                pick: () => mention(a.name),
              }))
          : [];
  const current = Math.min(active, Math.max(0, options.length - 1));

  const send = () => {
    const body = text.trim();
    if (!body || post.isPending) return;
    post.mutate(
      { channelId: channel.id, body, thread_root_id: threadRootId },
      { onSuccess: () => setText("") },
    );
  };

  const mention = (name: string) => {
    setText((t) =>
      t.replace(MENTION_AT_END, (_m, lead: string) => `${lead}@${name} `),
    );
    setActive(0);
    input.current?.focus();
  };

  const complete = (value: string) => {
    setText(value);
    setActive(0);
    input.current?.focus();
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.nativeEvent.isComposing) return; // Korean IME still composing a syllable
    if (options.length > 0) {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        const step = e.key === "ArrowDown" ? 1 : -1;
        setActive((current + step + options.length) % options.length);
        return;
      }
      if (
        (e.key === "Tab" && !e.shiftKey) ||
        (e.key === "Enter" && !e.shiftKey)
      ) {
        e.preventDefault();
        options[current].pick();
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        setDismissed(text);
        return;
      }
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  const insert = (command: string) => {
    setText((t) => `${command} ${t.replace(/^\/\w+\s*/, "")}`);
    input.current?.focus();
  };

  const inputId = threadRootId ? "thread-reply" : "composer";
  return (
    <div
      className={`relative ${narrow ? "px-5 pb-[18px]" : `${FEED_COLUMN} px-8 pb-6`}`}
    >
      {options.length > 0 && (
        <div
          role="listbox"
          aria-label={
            command !== undefined ? tr("명령 고르기") : tr("에이전트 부르기")
          }
          className={`absolute bottom-full ${narrow ? "left-5" : "left-8"} mb-2 flex min-w-[260px] flex-col gap-0.5 rounded-2xl border border-line-soft bg-card p-1.5 shadow-lift`}
        >
          {options.map((o, i) => (
            <button
              key={o.key}
              type="button"
              role="option"
              aria-selected={i === current}
              onMouseEnter={() => setActive(i)}
              onClick={o.pick}
              className="flex h-9 cursor-pointer items-center gap-2 rounded-xl px-2.5 text-left text-[13.5px] aria-selected:bg-inset"
            >
              {o.agent && <AgentAvatar id={o.agent} size={20} />}
              <span className={`text-ink ${o.agent ? "grow" : "font-mono"}`}>
                {o.main}
              </span>
              <span
                className={`font-mono text-[11.5px] text-meta ${o.agent ? "" : "grow truncate"}`}
              >
                {o.side}
              </span>
            </button>
          ))}
          <span className="px-2.5 pt-1 pb-0.5 text-[11px] text-meta">
            {tr("Tab·Enter 선택 · ↑↓ 이동 · Esc 닫기")}
          </span>
        </div>
      )}
      <div className={`${card} flex flex-col gap-3 px-[18px] pt-4 pb-3`}>
        <label htmlFor={inputId} className="sr-only">
          {threadRootId ? tr("스레드에 답장") : tr("메시지 입력")}
        </label>
        <textarea
          id={inputId}
          ref={input}
          rows={Math.min(6, Math.max(1, text.split("\n").length))}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={
            threadRootId
              ? tr("스레드에 답장… (/task, /debate 같은 명령도 돼요)")
              : dm
                ? tt`${dmAgent?.display_name ?? tr("에이전트")}에게 메시지`
                : tt`${channel.name === "inbox" ? tr("내 공간") : `#${channel.name}`}에 적기 — 그냥 쓰면 Argos가 알아서 정리해요`
          }
          className="resize-none border-0 bg-transparent text-[15px] text-text outline-none placeholder:text-meta focus-visible:outline-none"
        />
        <div className="flex flex-wrap items-center gap-1.5">
          {!dm &&
            SLASH.map((c) => (
              <button
                key={c}
                type="button"
                onClick={() => insert(c)}
                className="h-7 cursor-pointer rounded-full bg-inset px-[11px] font-mono text-[11.5px] text-text-2 hover:text-ink"
              >
                {c}
              </button>
            ))}
          <span className="grow" />
          {post.error ? (
            <span role="alert" className="text-[12px] text-danger">
              {post.error.message}
            </span>
          ) : narrow ? null : (
            <span className="text-[12px] text-meta">
              {dm ? (
                <>
                  {tr("받는 이 ·") + " "}
                  <AgentName id={dmAgent?.name} />
                </>
              ) : (
                <>
                  {tr("@로 에이전트 호출 · /ask는") + " "}
                  <AgentName id={askTarget?.name} />
                  {channelDefault ? tr(" (채널 기본)") : tr(" (기본)")}
                </>
              )}
            </span>
          )}
          <button
            type="button"
            aria-label={tr("보내기")}
            onClick={send}
            disabled={!text.trim() || post.isPending}
            className="flex size-9 cursor-pointer items-center justify-center rounded-full bg-cta text-on-cta shadow-raised disabled:opacity-40"
          >
            <SendIcon />
          </button>
        </div>
      </div>
    </div>
  );
}

function AgentName({ id }: { id: string | undefined }) {
  if (!id) return <span>{tr("없음")}</span>;
  const agent = agentInfo(id);
  return (
    <span className="font-medium" style={{ color: agent.text }}>
      {agent.name}
    </span>
  );
}

/** Right panel for ?thread=<id>. */
export function ThreadReplies({
  root,
  replies,
  channelId,
  wide = false,
}: {
  root: Message;
  replies: Message[];
  channelId: string;
  wide?: boolean;
}) {
  const channels = useChannels();
  const channel = channels.data?.channels.find((c) => c.id === channelId);
  if (!channel) return null;
  const column = wide ? FEED_COLUMN : "";

  return (
    <>
      <div className="flex min-h-0 grow flex-col overflow-y-auto">
        <div
          className={`flex flex-col gap-5 pb-4 ${wide ? "px-8" : "px-5"} ${column}`}
        >
          <MessageItem message={root} channel={channel} inlineReplies={false} />
          <div className="h-px bg-line-soft" />
          {replies.length === 0 && (
            <p className="m-0 text-[13px] text-meta">
              {tr("아직 답글이 없어요.")}
            </p>
          )}
          {replies.map((m) => (
            <MessageItem key={m.id} message={m} channel={channel} />
          ))}
        </div>
      </div>
      <div className={column}>
        <Composer channel={channel} threadRootId={root.id} narrow={!wide} />
      </div>
    </>
  );
}
