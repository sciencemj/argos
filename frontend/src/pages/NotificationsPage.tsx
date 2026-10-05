import { useNavigate } from "react-router";
import {
  type Notice,
  useChannels,
  useNotifications,
  useReadAllNotifications,
  useReadNotification,
} from "../api";
import { fmt } from "../dates";
import { tr, tt } from "../i18n";
import { BellIcon } from "../icons";
import { btn, card, ErrorText } from "../ui";

const KIND_TEXT: Record<string, string> = {
  due_soon: tr("마감 임박"),
  overdue: tr("마감 지남"),
  inbox_stale: tr("인박스"),
  undated: tr("날짜 없는 할 일"),
  weekly_review: tr("주간 리뷰"),
};

/** What Argos brought up on its own (PLAN Phase 11), newest first. */
export function NotificationsPage() {
  const notices = useNotifications();
  const read = useReadNotification();
  const readAll = useReadAllNotifications();
  const channels = useChannels();
  const navigate = useNavigate();
  const list = channels.data?.channels ?? [];

  const open = (n: Notice) => {
    if (!n.read_at) read.mutate(n.id);
    const inbox = list.find((c) => c.kind === "system" && c.name === "inbox");
    if (n.kind === "weekly_review") {
      navigate("/review");
    } else if (n.object_type === "task" && n.object_id && n.channel_id) {
      navigate(`/c/${n.channel_id}/kanban?task=${n.object_id}`);
    } else if (n.object_type === "inbox" && inbox) {
      navigate(`/c/${inbox.id}`);
    } else if (n.object_type === "message" && n.channel_id) {
      navigate(
        `/c/${n.channel_id}${n.object_id ? `?thread=${n.object_id}&wide=1` : ""}`,
      );
    } else {
      navigate("/");
    }
  };

  return (
    <div className="min-h-0 grow overflow-y-auto">
      <div className="mx-auto flex w-full max-w-[760px] flex-col gap-5 px-9 pt-8 pb-8">
        <div
          data-tauri-drag-region="deep"
          className="flex items-baseline gap-3"
        >
          <h1 className="m-0 grow text-[28px] leading-tight font-light tracking-[-0.02em] text-ink">
            {tr("알림")}
          </h1>
          {(notices.data?.unread ?? 0) > 0 && (
            <button
              type="button"
              className={btn.ghost}
              onClick={() => readAll.mutate(undefined)}
            >
              {tr("모두 읽음")}
            </button>
          )}
        </div>
        <ErrorText error={notices.error ?? read.error} />
        {notices.data?.items.length === 0 && (
          <div
            className={`${card} flex flex-col items-center gap-2 px-6 py-10 text-[13.5px] text-text-3`}
          >
            <BellIcon size={20} />
            {tr(
              "다가오는 마감이나 오래 둔 인박스가 있으면 여기에 알려 드려요.",
            )}
          </div>
        )}
        <ul className="m-0 flex list-none flex-col gap-2 p-0">
          {notices.data?.items.map((n) => (
            <li key={n.id}>
              <button
                type="button"
                onClick={() => open(n)}
                className={`${card} flex w-full cursor-pointer items-start gap-3 px-5 py-3.5 text-left hover:border-line`}
              >
                <span
                  className={`mt-[7px] size-2 shrink-0 rounded-full ${n.read_at ? "bg-transparent" : n.kind === "overdue" ? "bg-danger" : "bg-ink"}`}
                >
                  {!n.read_at && (
                    <span className="sr-only">{tr("안 읽음")}</span>
                  )}
                </span>
                <span className="flex min-w-0 grow flex-col gap-0.5">
                  <span
                    className={`text-[14px] ${n.read_at ? "text-text-2" : "font-medium text-ink"}`}
                  >
                    {n.title}
                  </span>
                  {n.body && (
                    <span className="truncate text-[12.5px] text-text-3">
                      {n.body}
                    </span>
                  )}
                  <span className="font-mono text-[11px] text-meta">
                    {KIND_TEXT[n.kind] ?? n.kind} ·{" "}
                    {fmt(n.created_at, "M/d HH:mm")}
                    {n.sent_at ? tr(" · 메신저로 보냄") : ""}
                    {n.send_error ? tt` · 메신저 실패: ${n.send_error}` : ""}
                  </span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
