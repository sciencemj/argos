import { useSearchParams } from "react-router";
import { useThread } from "../api";
import { ThreadReplies } from "../feed";
import { tr, tt } from "../i18n";
import { CloseIcon } from "../icons";
import { btn } from "../ui";

/** A thread beside the feed, or (`wide`) in place of it for long reviews. */
export function ThreadPanel({
  messageId,
  wide = false,
}: {
  messageId: string;
  wide?: boolean;
}) {
  const [params, setParams] = useSearchParams();
  const thread = useThread(messageId);

  const close = () => {
    const next = new URLSearchParams(params);
    next.delete("thread");
    next.delete("wide");
    setParams(next);
  };
  const toggleWide = () => {
    const next = new URLSearchParams(params);
    if (wide) next.delete("wide");
    else next.set("wide", "1");
    setParams(next);
  };

  return (
    <aside
      aria-label={tr("스레드")}
      className={`flex min-h-0 flex-col ${wide ? "grow bg-page" : "border-l border-line-soft bg-sidebar"}`}
    >
      <div
        className={`flex h-16 items-center gap-2.5 ${wide ? "px-8" : "px-5"}`}
      >
        <span className="grow text-[12.5px] text-meta">
          {tr("스레드") + " "}
          {thread.data ? tt` · 답글 ${thread.data.replies.length}` : ""}
        </span>
        <button
          type="button"
          className={`${btn.ghost} h-8 text-[12.5px]`}
          onClick={toggleWide}
          aria-pressed={wide}
        >
          {wide ? tr("옆으로 좁히기") : tr("넓게 보기")}
        </button>
        <button
          type="button"
          aria-label={tr("닫기")}
          className={btn.icon}
          onClick={close}
        >
          <CloseIcon />
        </button>
      </div>
      {thread.isError && (
        <p className="px-5 text-[13px] text-text-3">
          {tr("이 메시지를 찾을 수 없어요.")}
        </p>
      )}
      {thread.data && (
        <ThreadReplies
          root={thread.data.root}
          replies={thread.data.replies}
          channelId={thread.data.root.channel_id}
          wide={wide}
        />
      )}
    </aside>
  );
}
