import { useSearchParams } from "react-router";
import { useThread } from "../api";
import { ThreadReplies } from "../feed";
import { CloseIcon } from "../icons";
import { btn } from "../ui";

export function ThreadPanel({ messageId }: { messageId: string }) {
  const [params, setParams] = useSearchParams();
  const thread = useThread(messageId);

  const close = () => {
    const next = new URLSearchParams(params);
    next.delete("thread");
    setParams(next);
  };

  return (
    <aside
      aria-label="스레드"
      className="flex min-h-0 flex-col border-l border-line-soft bg-sidebar"
    >
      <div className="flex h-16 items-center gap-2.5 px-5">
        <span className="grow text-[12.5px] text-meta">스레드</span>
        <button
          type="button"
          aria-label="닫기"
          className={btn.icon}
          onClick={close}
        >
          <CloseIcon />
        </button>
      </div>
      {thread.isError && (
        <p className="px-5 text-[13px] text-text-3">
          이 메시지를 찾을 수 없어요.
        </p>
      )}
      {thread.data && (
        <ThreadReplies
          root={thread.data.root}
          replies={thread.data.replies}
          channelId={thread.data.root.channel_id}
        />
      )}
    </aside>
  );
}
