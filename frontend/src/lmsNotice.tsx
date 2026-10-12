import { type ReactNode, useState } from "react";
import type { LmsNotice } from "./api";
import { tr } from "./i18n";
import { card } from "./ui";

const KINDS: Record<string, () => string> = {
  announcement: () => tr("공지", "Announcement"),
  activity: () => tr("활동", "Activity"),
  conversation: () => tr("메시지", "Message"),
  attendance: () => tr("출석", "Attendance"),
  material: () => tr("자료", "Material"),
};

/** Plain text with http(s) addresses as links: LMS text is never Markdown or HTML. */
export function linkify(text: string): ReactNode[] {
  return text.split(/(https?:\/\/[^\s<>"']+)/g).map((part, i) =>
    /^https?:\/\//.test(part) ? (
      <a
        // biome-ignore lint/suspicious/noArrayIndexKey: split parts have no identity
        key={i}
        href={part}
        target="_blank"
        rel="noreferrer"
        className="underline"
      >
        {part}
      </a>
    ) : (
      part
    ),
  );
}

/** An LMS notice in the feed (PLAN Phase 15a): the AI summary when there is one,
 *  the original text otherwise or on request. */
export function LmsNoticeCard({ notice }: { notice: LmsNotice }) {
  const [open, setOpen] = useState(false);
  const kind = (notice.kind && KINDS[notice.kind]?.()) || "LMS";
  const summary = notice.summary_state === "done" ? notice.summary : null;
  return (
    <div
      className={`${card} flex max-w-[640px] flex-col gap-2.5 px-[18px] py-3.5`}
    >
      <div className="flex items-baseline gap-2">
        <span className="shrink-0 rounded-full bg-inset px-2 py-px text-[11px] font-medium text-text-2">
          {kind}
        </span>
        <span className="min-w-0 font-medium text-ink">{notice.title}</span>
      </div>
      {summary ? (
        <ul className="m-0 flex list-disc flex-col gap-1 pl-5 text-[14px] text-text">
          {summary.split("\n").map((line) => (
            <li key={line} className="select-text">
              {line.replace(/^- /, "")}
            </li>
          ))}
        </ul>
      ) : notice.summary_state === "pending" ? (
        <div className="flex flex-col gap-1">
          <p className="m-0 line-clamp-3 select-text whitespace-pre-wrap text-[14px] text-text-2">
            {notice.text.split("\n").slice(0, 3).join("\n")}
          </p>
          <span className="text-[12px] text-meta">
            {tr("요약 중…", "Summarizing…")}
          </span>
        </div>
      ) : (
        <p className="m-0 select-text whitespace-pre-wrap text-[14px] text-text-2">
          {linkify(notice.text)}
        </p>
      )}
      {summary && open && (
        <p className="m-0 select-text whitespace-pre-wrap border-t border-line-soft pt-2.5 text-[13px] text-text-3">
          {linkify(notice.text)}
        </p>
      )}
      <div className="flex items-center gap-4 text-[12px]">
        {summary && (
          <button
            type="button"
            className="text-text-2 hover:text-ink"
            aria-expanded={open}
            onClick={() => setOpen(!open)}
          >
            {open ? "▾ " : "▸ "}
            {tr("원문 보기", "Original")}
          </button>
        )}
        <a
          href={notice.url}
          target="_blank"
          rel="noreferrer"
          className="ml-auto text-text-2 hover:text-ink"
        >
          {tr("LMS에서 열기", "Open in LMS")} ↗
        </a>
      </div>
    </div>
  );
}
