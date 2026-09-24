import {
  type FormEvent,
  type KeyboardEvent,
  useEffect,
  useRef,
  useState,
} from "react";
import { useSearchParams } from "react-router";
import { AgentAvatar, agentInfo } from "./agents";
import {
  type Channel,
  type Message,
  useChannels,
  useConvertMessage,
  useMessages,
  usePinMessage,
  usePostMessage,
  useUpdateTask,
} from "./api";
import {
  ApprovalCard,
  EventRefCard,
  SuggestionCard,
  TaskRefCard,
} from "./cards";
import { fmt, localInputToIso } from "./dates";
import {
  CalendarIcon,
  CheckIcon,
  DogIcon,
  KanbanIcon,
  PinIcon,
  SendIcon,
} from "./icons";
import { btn, card, Dialog, ErrorText, field, label } from "./ui";

const SLASH = ["/task", "/event", "/note", "/ask"];

export function useOpenThread() {
  const [params, setParams] = useSearchParams();
  return (id: string) => {
    const next = new URLSearchParams(params);
    next.delete("task");
    next.set("thread", id);
    setParams(next);
  };
}

/** Channel feed: messages are the input layer, the cards show the objects (PLAN P4). */
export function Feed({ channel }: { channel: Channel }) {
  const messages = useMessages(channel.id);
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
        <div className="mx-8 mb-2 flex items-center gap-2 overflow-hidden rounded-full border border-line-soft bg-card px-4 py-2 text-[12.5px] text-text-3">
          <PinIcon size={13} />
          <span className="truncate">
            {pinned.map((m) => m.body).join(" · ")}
          </span>
        </div>
      )}
      <section
        ref={scroller}
        aria-label="메시지"
        className="flex min-h-0 grow flex-col gap-[26px] overflow-y-auto px-8 pt-2 pb-[18px]"
      >
        <div className="grow" />
        {messages.hasNextPage && (
          <button
            type="button"
            className={`${btn.ghost} self-center`}
            onClick={() => void messages.fetchNextPage()}
          >
            이전 메시지 더 보기
          </button>
        )}
        {messages.isSuccess && items.length === 0 && (
          <div className="text-[13px] text-text-3">
            #{channel.name}에 첫 메시지를 적어 보세요. 그냥 쓰면 Argos가 할
            일·일정으로 정리해요.
          </div>
        )}
        {items.map((m) => (
          <MessageItem
            key={m.id}
            message={m}
            channel={channel}
            onThread={openThread}
          />
        ))}
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
      나
    </div>
  );
}

export function MessageItem({
  message,
  channel,
  onThread,
}: {
  message: Message;
  channel: Channel;
  onThread?: (id: string) => void;
}) {
  const system = message.author_type === "system";
  const agent =
    message.author_type === "agent" ? agentInfo(message.author_id) : null;
  const { inbox_item, task, event, approval } = message.ref ?? {};
  return (
    <article className="group relative flex gap-3.5">
      <Author message={message} />
      <div className="flex min-w-0 grow flex-col gap-2.5">
        <div className="flex items-baseline gap-2">
          <span
            className="font-medium"
            style={{ color: agent?.text ?? "var(--ink)" }}
          >
            {agent ? agent.name : system ? "Argos" : "나"}
          </span>
          {(system || agent) && (
            <span className="rounded-full border border-line-soft px-[7px] text-[11px] text-meta">
              {agent ? "에이전트" : "시스템"}
            </span>
          )}
          <span className="font-mono text-[11px] text-meta">
            {fmt(message.created_at, "HH:mm")}
          </span>
          {message.pinned && (
            <span className="text-meta" title="고정됨">
              <PinIcon size={12} />
            </span>
          )}
        </div>
        <div
          className={`whitespace-pre-wrap ${system || agent ? "text-text-2" : "text-[15px] text-text"}`}
        >
          {message.body}
        </div>
        {inbox_item && <SuggestionCard item={inbox_item} channel={channel} />}
        {task && <TaskRefCard task={task} />}
        {event && <EventRefCard event={event} />}
        {approval && <ApprovalCard approval={approval} />}
        {onThread && message.reply_count > 0 && (
          <button
            type="button"
            onClick={() => onThread(message.id)}
            className="cursor-pointer self-start text-[12.5px] font-medium text-text-2 underline underline-offset-[3px]"
          >
            답글 {message.reply_count}
          </button>
        )}
      </div>
      {onThread && <QuickActions message={message} onThread={onThread} />}
    </article>
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
  const updateTask = useUpdateTask();
  const [eventOpen, setEventOpen] = useState(false);
  const ref = message.ref ?? {};
  const task = ref.task;
  const promotable = !ref.task && !ref.event && message.author_type === "user";
  const icon = btn.icon;

  return (
    <div
      role="toolbar"
      aria-label="빠른 액션"
      className="absolute -top-2 right-0 hidden gap-0.5 rounded-full border border-line-soft bg-card p-[3px] shadow-raised group-focus-within:flex group-hover:flex"
    >
      {task && task.status !== "done" && (
        <button
          type="button"
          aria-label="완료"
          className={icon}
          onClick={() => updateTask.mutate({ id: task.id, status: "done" })}
        >
          <CheckIcon />
        </button>
      )}
      {promotable && (
        <>
          <button
            type="button"
            aria-label="일정으로"
            className={icon}
            onClick={() => setEventOpen(true)}
          >
            <CalendarIcon />
          </button>
          <button
            type="button"
            aria-label="칸반으로"
            className={icon}
            onClick={() => convert.mutate({ id: message.id, kind: "task" })}
          >
            <KanbanIcon />
          </button>
        </>
      )}
      <button
        type="button"
        aria-label={message.pinned ? "고정 해제" : "고정"}
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
        스레드
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
    <Dialog open={open} onClose={onClose} title="일정으로 만들기">
      <form onSubmit={submit} className="flex flex-col gap-4">
        <label className="flex flex-col gap-1">
          <span className={label}>제목</span>
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
          종일
        </label>
        {allDay ? (
          <label className="flex flex-col gap-1">
            <span className={label}>날짜</span>
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
              <span className={label}>시작</span>
              <input
                type="datetime-local"
                className={field}
                value={start}
                onChange={(e) => setStart(e.target.value)}
                required
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className={label}>끝 (선택)</span>
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
            취소
          </button>
          <button
            type="submit"
            className={btn.cta}
            disabled={convert.isPending}
          >
            추가하기
          </button>
        </div>
      </form>
    </Dialog>
  );
}

/** Enter sends, Shift+Enter breaks the line; Enter while a Korean syllable is still
 * being composed only commits the syllable. */
function Composer({ channel }: { channel: Channel }) {
  const post = usePostMessage();
  const [text, setText] = useState("");
  const input = useRef<HTMLTextAreaElement>(null);

  const send = () => {
    const body = text.trim();
    if (!body || post.isPending) return;
    post.mutate(
      { channelId: channel.id, body },
      { onSuccess: () => setText("") },
    );
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      send();
    }
  };

  const insert = (command: string) => {
    setText((t) => `${command} ${t.replace(/^\/\w+\s*/, "")}`);
    input.current?.focus();
  };

  return (
    <div className="px-8 pb-6">
      <div className={`${card} flex flex-col gap-3 px-[18px] pt-4 pb-3`}>
        <label htmlFor="composer" className="sr-only">
          메시지 입력
        </label>
        <textarea
          id="composer"
          ref={input}
          rows={Math.min(6, Math.max(1, text.split("\n").length))}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={`#${channel.name}에 적기 — 그냥 쓰면 Argos가 알아서 정리해요`}
          className="resize-none border-0 bg-transparent text-[15px] text-text outline-none placeholder:text-meta focus-visible:outline-none"
        />
        <div className="flex items-center gap-1.5">
          {SLASH.map((c) => (
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
          {post.error && (
            <span role="alert" className="text-[12px] text-danger">
              {post.error.message}
            </span>
          )}
          <button
            type="button"
            aria-label="보내기"
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

/** Right panel for ?thread=<id>. */
export function ThreadReplies({
  root,
  replies,
  channelId,
}: {
  root: Message;
  replies: Message[];
  channelId: string;
}) {
  const channels = useChannels();
  const post = usePostMessage();
  const [text, setText] = useState("");
  const channel = channels.data?.channels.find((c) => c.id === channelId);
  if (!channel) return null;

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!text.trim()) return;
    post.mutate(
      { channelId, body: text.trim(), thread_root_id: root.id },
      { onSuccess: () => setText("") },
    );
  };

  return (
    <>
      <div className="flex min-h-0 grow flex-col gap-5 overflow-y-auto px-5 pb-4">
        <MessageItem message={root} channel={channel} />
        <div className="h-px bg-line-soft" />
        {replies.length === 0 && (
          <p className="m-0 text-[13px] text-meta">아직 답글이 없어요.</p>
        )}
        {replies.map((m) => (
          <MessageItem key={m.id} message={m} channel={channel} />
        ))}
      </div>
      <form onSubmit={submit} className="px-5 pt-3 pb-[18px]">
        <label htmlFor="thread-reply" className="sr-only">
          스레드에 답장
        </label>
        <input
          id="thread-reply"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="스레드에 답장…"
          className={`${field} focus-visible:outline-none`}
        />
        <ErrorText error={post.error} />
      </form>
    </>
  );
}
