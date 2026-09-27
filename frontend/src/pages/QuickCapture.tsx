import { useEffect, useRef, useState } from "react";
import { useChannels, usePostMessage } from "../api";
import { hideQuickWindow, openMainWindow } from "../desktop";
import { tr } from "../i18n";
import { ErrorText } from "../ui";

/** The desktop app's quick-capture window (global shortcut, PLAN Phase 12): one line into
 * the inbox, where the classifier sorts it like any other message. */
export function QuickCapture() {
  const { data } = useChannels();
  const post = usePostMessage();
  const input = useRef<HTMLTextAreaElement>(null);
  const [text, setText] = useState("");
  const [sent, setSent] = useState(false);
  const inbox = data?.channels.find(
    (c) => c.kind === "system" && c.name === "inbox",
  );

  // The window is see-through; the rounded card below is all that shows.
  useEffect(() => {
    for (const el of [document.documentElement, document.body])
      el.style.background = "transparent";
  }, []);

  // The window is reused: every time it is shown, start clean with the cursor in place.
  useEffect(() => {
    const onFocus = () => {
      setSent(false);
      input.current?.focus();
    };
    onFocus();
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, []);

  const close = () => {
    setText("");
    post.reset();
    void hideQuickWindow();
  };

  const send = () => {
    const body = text.trim();
    if (!body || !inbox || post.isPending) return;
    post.mutate(
      { channelId: inbox.id, body },
      {
        onSuccess: () => {
          setText("");
          setSent(true);
          setTimeout(close, 700);
        },
      },
    );
  };

  return (
    <div
      data-tauri-drag-region
      className="flex h-full flex-col gap-2 overflow-hidden rounded-2xl border border-line bg-card p-4"
    >
      <textarea
        ref={input}
        aria-label={tr("빠른 입력")}
        rows={2}
        value={text}
        placeholder={tr(
          "생각난 걸 적고 Enter — 인박스로 가요 (/task, /event도 돼요)",
        )}
        className="w-full grow resize-none border-0 bg-transparent text-[15px] leading-relaxed text-ink outline-none placeholder:text-meta"
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Escape") close();
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            send();
          }
        }}
      />
      <div className="flex items-center gap-2 text-[12px] text-meta">
        <span className="grow">
          {sent
            ? tr("인박스에 넣었어요")
            : tr("Enter 보내기 · Shift+Enter 줄바꿈 · Esc 닫기")}
        </span>
        {inbox && (
          <button
            type="button"
            className="cursor-pointer text-text-3 hover:text-ink"
            onClick={() => {
              setText("");
              post.reset();
              void openMainWindow(`/c/${inbox.id}`); // the app hides this window
            }}
          >
            {tr("인박스 열기")}
          </button>
        )}
      </div>
      <ErrorText error={post.error} />
    </div>
  );
}
