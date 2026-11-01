import {
  type CSSProperties,
  type KeyboardEvent,
  type MouseEvent,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { AgentAvatar, agentInfo } from "../agents";
import type { Agent } from "../api";
import { canSend, DraftChips, pastedFiles, useFileDrop } from "../attachments";
import { FEED_COLUMN } from "../feed";
import { tr } from "../i18n";
import { FileIcon, GhostIcon, PaperclipIcon, SendIcon } from "../icons";
import { Markdown } from "../markdown";
import { PawTrail } from "../paws";
import { type PrivateTurn, usePrivateChat } from "../privateChat";
import { btn, card } from "../ui";

type Origin = { x: number; y: number };
type Mode = Origin & { open: boolean; at: number };

const CLOSED: Mode = { open: false, x: 0, y: 0, at: 0 };
const RING_MS = 700;

// Which DMs are private, per agent, for as long as the app runs: module level, so
// leaving for another channel and coming back keeps the DM as it was.
const modes = new Map<string, Mode>();
const listeners = new Set<() => void>();
const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => listeners.delete(listener);
};

function toggle(agent: string, at: Origin) {
  const open = !(modes.get(agent) ?? CLOSED).open;
  modes.set(agent, { ...at, open, at: Date.now() });
  for (const l of listeners) l();
}

const usePrivateMode = (agent: string) =>
  useSyncExternalStore(subscribe, () => modes.get(agent) ?? CLOSED);

const reduceMotion = () =>
  globalThis.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;

/** Where the press happened, relative to the page's <main> (the layer's box). A key
 * press has no pointer position, so the button's center stands in. */
function originOf(e: MouseEvent<HTMLElement>): Origin {
  const button = e.currentTarget.getBoundingClientRect();
  const box = e.currentTarget.closest("main")?.getBoundingClientRect();
  const pointer = e.clientX !== 0 || e.clientY !== 0;
  const x = pointer ? e.clientX : button.left + button.width / 2;
  const y = pointer ? e.clientY : button.top + button.height / 2;
  return { x: x - (box?.left ?? 0), y: y - (box?.top ?? 0) };
}

function GhostButton({
  open,
  onPress,
}: {
  open: boolean;
  onPress: (e: MouseEvent<HTMLElement>) => void;
}) {
  const label = open ? tr("일반 대화로 돌아가기") : tr("비공개 대화로 전환");
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      aria-pressed={open}
      onClick={onPress}
      className="flex size-9 shrink-0 cursor-pointer items-center justify-center rounded-full border border-line bg-card text-text-2 hover:text-ink"
    >
      <GhostIcon size={18} />
    </button>
  );
}

/** The DM header's switch to private chat (only for agents whose model stays local). */
export function PrivateGhost({ agent }: { agent: Agent | undefined }) {
  const mode = usePrivateMode(agent?.name ?? "");
  if (!agent?.private_ok) return null;
  return (
    <GhostButton
      open={mode.open}
      onPress={(e) => toggle(agent.name, originOf(e))}
    />
  );
}

/** The private layer over the DM, and the ring that spreads when it switches. */
export function PrivateOverlay({ agent }: { agent: Agent | undefined }) {
  const mode = usePrivateMode(agent?.name ?? "");
  if (!agent?.private_ok) return null;
  const motion = !reduceMotion();
  // Only a switch just made rings; coming back to an open DM later does not.
  const ringing = motion && mode.at > 0 && Date.now() - mode.at < RING_MS;
  return (
    <>
      <PrivateLayer
        agent={agent}
        origin={mode}
        open={mode.open}
        animate={motion}
        onClose={(e) => toggle(agent.name, originOf(e))}
      />
      {ringing && (
        <span
          key={mode.at}
          aria-hidden="true"
          className="private-scope private-ring pointer-events-none absolute z-30 rounded-full border-2 border-meta"
          style={{ left: mode.x, top: mode.y }}
        />
      )}
    </>
  );
}

export function PrivateLayer({
  agent,
  origin,
  open,
  animate = true,
  onClose,
}: {
  agent: Agent;
  origin: Origin;
  open: boolean;
  animate?: boolean;
  onClose: (e: MouseEvent<HTMLElement>) => void;
}) {
  const chat = usePrivateChat(agent.name);
  const scroller = useRef<HTMLElement>(null);
  const last = chat.turns.at(-1);

  // Follow the answer as it streams.
  useEffect(() => {
    const box = scroller.current;
    if (last && box) box.scrollTop = box.scrollHeight;
  }, [last]);

  const style = {
    "--x": `${origin.x}px`,
    "--y": `${origin.y}px`,
  } as CSSProperties;
  const empty = chat.turns.length === 0 && chat.drafts.length === 0;
  return (
    <section
      aria-label={tr("비공개 대화")}
      aria-hidden={!open}
      inert={!open}
      data-open={open}
      style={style}
      className={`private-scope private-layer ${animate ? "private-animate" : ""} absolute inset-0 z-20 flex flex-col bg-page text-text`}
    >
      <header
        data-tauri-drag-region="deep"
        className={`${FEED_COLUMN} flex items-center gap-3.5 px-8 max-md:px-4 pt-[18px] pb-4`}
      >
        <AgentAvatar id={agent.name} size={36} />
        <div className="flex min-w-0 grow flex-col">
          <h2 className="m-0 truncate text-[28px] leading-tight font-light tracking-[-0.02em] text-ink max-md:text-[22px]">
            {agent.display_name}
          </h2>
          <span className="text-[12.5px] text-meta">
            {tr("비공개 · 저장 안 됨")}
          </span>
        </div>
        <GhostButton open={open} onPress={onClose} />
        <button
          type="button"
          className={`${btn.outline} shrink-0 whitespace-nowrap`}
          disabled={empty}
          onClick={chat.clear}
        >
          {tr("대화 지우기")}
        </button>
      </header>
      <div className="border-y border-line-soft bg-card">
        <p
          className={`${FEED_COLUMN} m-0 px-8 py-[7px] text-[12px] text-text-2 max-md:px-4`}
        >
          {tr(
            "비공개 대화 · 이 기기와 내 네트워크의 모델만 쓰고, 앱을 끄면 사라져요",
          )}
        </p>
      </div>
      <section
        ref={scroller}
        aria-label={tr("비공개 메시지")}
        className="flex min-h-0 grow flex-col overflow-y-auto"
      >
        <div
          className={`${FEED_COLUMN} flex grow flex-col gap-[26px] px-8 max-md:px-4 pt-5 pb-[18px]`}
        >
          {chat.turns.length === 0 ? (
            <div className="m-auto flex flex-col items-center gap-2.5 text-center text-[13px] text-meta">
              <GhostIcon size={28} />
              {tr("여기서 나눈 대화는 어디에도 저장되지 않아요")}
            </div>
          ) : (
            <>
              <div className="grow" />
              {chat.turns.map((turn, i) => (
                <Turn
                  // Turns only grow at the end or are cleared all at once.
                  // biome-ignore lint/suspicious/noArrayIndexKey: append-only list
                  key={i}
                  turn={turn}
                  agent={agent}
                  live={chat.running && i === chat.turns.length - 1}
                />
              ))}
            </>
          )}
        </div>
      </section>
      <PrivateComposer agent={agent} chat={chat} open={open} />
    </section>
  );
}

function Turn({
  turn,
  agent,
  live,
}: {
  turn: PrivateTurn;
  agent: Agent;
  live: boolean;
}) {
  const mine = turn.role === "user";
  return (
    <article className="flex gap-3.5">
      {mine ? (
        <div className="flex size-9 shrink-0 items-center justify-center rounded-full border border-line-soft bg-inset text-[12px] font-semibold text-text">
          {tr("나")}
        </div>
      ) : (
        <AgentAvatar id={agent.name} />
      )}
      <div className="flex min-w-0 grow flex-col gap-2">
        <span
          className="font-medium"
          style={{ color: mine ? "var(--ink)" : agentInfo(agent.name).text }}
        >
          {mine ? tr("나") : agent.display_name}
        </span>
        {mine ? (
          turn.text && (
            <p className="m-0 text-[14px] leading-[1.6] whitespace-pre-wrap text-text select-text">
              {turn.text}
            </p>
          )
        ) : (
          <div className="text-[13.5px] leading-[1.65] text-text-2 select-text">
            {turn.text ? <Markdown text={turn.text} /> : live && <PawTrail />}
            {live && turn.text && (
              <span className="ml-[3px] inline-block h-[15px] w-[2px] animate-pulse bg-ink align-[-2px]" />
            )}
          </div>
        )}
        {turn.parts.length > 0 && (
          <ul className="m-0 flex list-none flex-wrap gap-1.5 p-0">
            {[...new Set(turn.parts.map((p) => p.name))].map((name) => (
              <li
                key={name}
                className="flex max-w-[240px] items-center gap-1.5 rounded-xl border border-line-soft bg-inset px-2 py-1 text-[12px] text-text"
              >
                <span className="text-meta">
                  <FileIcon size={14} />
                </span>
                <span className="truncate">{name}</span>
              </li>
            ))}
          </ul>
        )}
        {turn.error && (
          <p role="alert" className="m-0 text-[12.5px] text-danger">
            {turn.error}
          </p>
        )}
      </div>
    </article>
  );
}

function PrivateComposer({
  agent,
  chat,
  open,
}: {
  agent: Agent;
  chat: ReturnType<typeof usePrivateChat>;
  open: boolean;
}) {
  const [text, setText] = useState("");
  const input = useRef<HTMLTextAreaElement>(null);
  const picker = useRef<HTMLInputElement>(null);
  const drop = useFileDrop(chat.addFiles);
  const ready = canSend(text, chat.drafts) && !chat.running;

  useEffect(() => {
    if (open) input.current?.focus();
  }, [open]);

  const send = () => {
    if (!ready) return;
    if (chat.send(text)) setText("");
  };
  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.nativeEvent.isComposing) return; // Korean IME still composing a syllable
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  return (
    <div className={`${FEED_COLUMN} px-8 max-md:px-4 pb-6`}>
      <div
        {...drop.handlers}
        className={`${card} relative flex flex-col gap-3 px-[18px] pt-4 pb-3 ${drop.over ? "border-ink" : ""}`}
      >
        {drop.over && (
          <span className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center rounded-3xl bg-card/90 text-[13px] font-medium text-ink">
            {tr("여기에 놓아 첨부")}
          </span>
        )}
        <DraftChips
          drafts={chat.drafts}
          onRemove={chat.removeDraft}
          onRetry={chat.retryDraft}
        />
        <label htmlFor="private-composer" className="sr-only">
          {tr("비공개 메시지 입력")}
        </label>
        <textarea
          id="private-composer"
          ref={input}
          rows={Math.min(6, Math.max(1, text.split("\n").length))}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
          onPaste={(e) => {
            const pasted = pastedFiles(e.clipboardData);
            if (pasted.length === 0) return;
            e.preventDefault();
            chat.addFiles(pasted);
          }}
          placeholder={tr("비공개 메시지…")}
          className="resize-none border-0 bg-transparent text-[15px] text-text outline-none placeholder:text-meta focus-visible:outline-none"
        />
        <div className="flex items-center gap-1.5">
          <button
            type="button"
            aria-label={tr("파일 붙이기")}
            title={tr("파일 붙이기")}
            onClick={() => picker.current?.click()}
            className="flex size-7 cursor-pointer items-center justify-center rounded-full bg-inset text-text-2 hover:text-ink"
          >
            <PaperclipIcon size={14} />
          </button>
          <input
            ref={picker}
            type="file"
            multiple
            hidden
            onChange={(e) => {
              chat.addFiles(Array.from(e.target.files ?? []));
              e.target.value = ""; // the same file can be picked again
            }}
          />
          <span className="grow" />
          {chat.error ? (
            <span role="alert" className="text-[12px] text-danger">
              {chat.error}
            </span>
          ) : (
            <span className="text-[12px] text-meta max-md:hidden">
              {`${tr("받는 이 ·")} ${agent.display_name}`}
            </span>
          )}
          {chat.running ? (
            <button
              type="button"
              onClick={chat.stop}
              className="h-9 cursor-pointer rounded-full border border-line bg-card px-3.5 text-[12.5px] text-text-2 hover:text-ink"
            >
              {tr("중단")}
            </button>
          ) : (
            <button
              type="button"
              aria-label={tr("보내기")}
              onClick={send}
              disabled={!ready}
              className="flex size-9 cursor-pointer items-center justify-center rounded-full bg-cta text-on-cta shadow-raised disabled:opacity-40"
            >
              <SendIcon />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
