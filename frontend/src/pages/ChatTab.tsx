import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router";
import { AgentAvatar } from "../agents";
import { type Channel, useAgentSettings, useAgents, useThread } from "../api";
import { Composer, FEED_COLUMN, ThreadReplies } from "../feed";
import { tr, tt } from "../i18n";
import { btn } from "../ui";
import { FeedTab, useChannel } from "./ChannelPage";

const QUICK_PROMPTS = [
  {
    label: tr("이번 주 마감 정리", "This week's deadlines"),
    text: tr(
      "이번 주 이 채널의 마감과 일정을 정리해 줘",
      "Summarize this channel's deadlines and events for this week",
    ),
  },
  {
    label: tr("자료 요약", "Summarize materials"),
    text: tr(
      "올린 파일과 강의 자료에서 핵심을 요약해 줘",
      "Summarize the key points of the uploaded files and course materials",
    ),
  },
  {
    label: tr("최근 공지", "Recent notices"),
    text: tr(
      "최근 LMS 공지와 과제 안내를 알려 줘",
      "Tell me the recent LMS notices and assignment guidance",
    ),
  },
  {
    label: tr("오늘 할 일 계획", "Plan today"),
    text: tr(
      "오늘 이 채널에서 할 일을 우선순위대로 계획해 줘",
      "Plan today's work in this channel by priority",
    ),
  },
];

/** The channel's first tab (v0.2 "가안 A"): a clean new conversation with the channel's
 * agent, or (`?session=<root id>`) one conversation from the sidebar's session tree.
 * System channels and 내 공간 keep their feed. */
/** Channels with a chat view: courses, projects and 내 공간 (its personal channel). */
export const hasChat = (kind: string) =>
  kind === "course" || kind === "project" || kind === "personal";

export function ChatTab() {
  const channel = useChannel();
  const [params] = useSearchParams();
  const session = params.get("session");
  if (!hasChat(channel.kind)) return <FeedTab />;
  return session ? (
    <SessionView key={session} channel={channel} rootId={session} />
  ) : (
    <NewChat key={channel.id} channel={channel} />
  );
}

function NewChat({ channel }: { channel: Channel }) {
  const [, setParams] = useSearchParams();
  const agents = useAgents();
  const settings = useAgentSettings();
  const agent =
    agents.data?.find((a) => a.id === channel.default_agent_id) ??
    agents.data?.find((a) => a.name === settings.data?.default_agent);
  const [draft, setDraft] = useState<{ text: string; key: number }>();
  const keys = useRef(0);
  return (
    <>
      <section
        aria-label={tr("새 대화", "New conversation")}
        className="flex min-h-0 grow flex-col overflow-y-auto"
      >
        <div
          className={`${FEED_COLUMN} flex grow flex-col items-center justify-center gap-6 px-8 py-10 max-md:px-4`}
        >
          <AgentAvatar id={agent?.name} size={44} />
          <div className="flex flex-col items-center gap-1.5 text-center">
            <h2 className="m-0 text-[22px] font-light tracking-[-0.01em] text-ink">
              {channel.kind === "personal"
                ? tr("내 공간에서 새 대화", "New conversation in My space")
                : tt`#${channel.name}에서 새 대화`}
            </h2>
            <p className="m-0 max-w-[460px] text-[13px] text-text-3">
              {agent
                ? tt`${agent.display_name}에게 물어보세요. 이 채널의 할 일·일정·자료를 함께 봐요.`
                : tr("에이전트를 고르면 대화를 시작할 수 있어요 (채널 설정)")}
            </p>
            {agent && !agent.available && (
              <p className="m-0 text-[12.5px] text-danger">{agent.problem}</p>
            )}
          </div>
          <div className="grid w-full max-w-[560px] grid-cols-2 gap-2 max-md:grid-cols-1">
            {QUICK_PROMPTS.map((p) => (
              <button
                key={p.label}
                type="button"
                className="flex cursor-pointer flex-col gap-0.5 rounded-2xl border border-line-soft bg-card px-4 py-3 text-left shadow-sm hover:border-line"
                onClick={() => {
                  keys.current += 1;
                  setDraft({ text: p.text, key: keys.current });
                }}
              >
                <span className="text-[13px] font-medium text-ink">
                  {p.label}
                </span>
                <span className="truncate text-[12px] text-meta">{p.text}</span>
              </button>
            ))}
          </div>
        </div>
      </section>
      <Composer
        channel={channel}
        agentSession
        draft={draft}
        onPosted={(message) => setParams({ session: message.id })}
        chatBar
      />
    </>
  );
}

function SessionView({
  channel,
  rootId,
}: {
  channel: Channel;
  rootId: string;
}) {
  const thread = useThread(rootId);
  const [, setParams] = useSearchParams();
  const scroller = useRef<HTMLDivElement>(null);
  const last = thread.data?.replies.at(-1)?.id;
  // Follow the conversation: the newest reply comes into view as it arrives.
  // biome-ignore lint/correctness/useExhaustiveDependencies: runs when the last reply changes
  useEffect(() => {
    const el = scroller.current;
    if (el) el.scrollTo({ top: el.scrollHeight });
  }, [last]);
  if (
    thread.isError ||
    (thread.data && thread.data.root.channel_id !== channel.id)
  )
    return (
      <div className={`${FEED_COLUMN} flex flex-col gap-3 px-8 py-10`}>
        <p className="m-0 text-[13px] text-text-3">
          {tr("이 대화를 찾을 수 없어요.", "This conversation was not found.")}
        </p>
        <button
          type="button"
          className={`${btn.outline} self-start`}
          onClick={() => setParams({})}
        >
          {tr("새 대화", "New conversation")}
        </button>
      </div>
    );
  if (!thread.data) return <SessionSkeleton />;
  return (
    <div className="flex min-h-0 grow flex-col">
      <ThreadReplies
        root={thread.data.root}
        replies={thread.data.replies}
        channelId={channel.id}
        wide
        scrollerRef={scroller}
        chatBar
      />
    </div>
  );
}

/** While a conversation loads: grey message shapes, so the page does not jump. */
function SessionSkeleton() {
  return (
    <div
      role="status"
      aria-busy="true"
      aria-label={tr("대화를 불러오는 중", "Loading conversation")}
      className={`${FEED_COLUMN} flex grow flex-col gap-6 px-8 py-6 max-md:px-4`}
    >
      {[72, 48, 64].map((width) => (
        <div key={width} className="flex animate-pulse gap-3">
          <div className="size-9 shrink-0 rounded-full bg-inset" />
          <div className="flex grow flex-col gap-2">
            <div className="h-3 w-24 rounded-full bg-inset" />
            <div
              className="h-3 rounded-full bg-inset"
              style={{ width: `${width}%` }}
            />
            <div className="h-3 w-1/3 rounded-full bg-inset" />
          </div>
        </div>
      ))}
    </div>
  );
}
