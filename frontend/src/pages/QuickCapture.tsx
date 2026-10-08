import { useEffect, useRef, useState } from "react";
import { useChannels, usePostMessage } from "../api";
import {
  canSend,
  DraftChips,
  pastedFiles,
  useAttachmentDrafts,
  useFileDrop,
} from "../attachments";
import { hideQuickWindow, openMainWindow } from "../desktop";
import { tr } from "../i18n";
import { PaperclipIcon } from "../icons";
import { useExit, useShowCount } from "../quickMotion";
import { ErrorText } from "../ui";

/** The desktop app's quick-capture window (global shortcut, PLAN Phase 12): one line into
 * the inbox, where the classifier sorts it like any other message. */
export function QuickCapture() {
  const { data } = useChannels();
  const post = usePostMessage();
  const input = useRef<HTMLTextAreaElement>(null);
  const [text, setText] = useState("");
  const files = useAttachmentDrafts();
  const drop = useFileDrop(files.add);
  const picker = useRef<HTMLInputElement>(null);
  // Spotlight-like: the card grows in each time the app shows the window, and shrinks
  // away when the page closes it (the shortcut closes it at once, from the app).
  const shown = useShowCount();
  const exit = useExit(120);
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
      exit.reset(); // never left faded out, even if the show signal did not arrive
      input.current?.focus();
    };
    onFocus();
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [exit.reset]);

  // Shown again: the entrance replays on a fresh card (keyed by `shown`).
  useEffect(() => {
    if (shown === 0) return;
    exit.reset();
    input.current?.focus();
  }, [shown, exit.reset]);

  const close = () =>
    exit.leave(() => {
      setText("");
      post.reset();
      // Uploads that were not sent go right away (a sent message keeps its files).
      for (const d of files.drafts) files.remove(d.key);
      void hideQuickWindow();
    });

  const send = () => {
    const body = text.trim();
    if (!canSend(text, files.drafts) || !inbox || post.isPending) return;
    post.mutate(
      { channelId: inbox.id, body, attachment_ids: files.ids },
      {
        // Leave at once with the words still showing; the card is emptied once hidden.
        onSuccess: () => {
          files.clear();
          close();
        },
      },
    );
  };

  return (
    <div
      key={shown}
      data-tauri-drag-region
      {...drop.handlers}
      className={`flex h-full flex-col gap-2 overflow-hidden rounded-2xl border bg-card p-4 ${drop.over ? "border-ink" : "border-line"} ${exit.leaving ? "quick-out" : "quick-in"}`}
    >
      <textarea
        ref={input}
        aria-label={tr("빠른 입력")}
        rows={2}
        value={text}
        placeholder={tr(
          "생각난 걸 적거나 붙여넣고 Enter — 인박스로 가요 (/task, /event도 돼요)",
        )}
        className="w-full grow resize-none border-0 bg-transparent text-[15px] leading-relaxed text-ink outline-none placeholder:text-meta"
        onChange={(e) => setText(e.target.value)}
        onPaste={(e) => {
          const pasted = pastedFiles(e.clipboardData);
          if (pasted.length === 0) return;
          e.preventDefault();
          files.add(pasted);
        }}
        onKeyDown={(e) => {
          if (e.key === "Escape") close();
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            send();
          }
        }}
      />
      <div className="flex items-center gap-2 text-[12px] text-meta">
        <button
          type="button"
          aria-label={tr("파일 붙이기")}
          title={tr("파일 붙이기")}
          onClick={() => picker.current?.click()}
          className="cursor-pointer text-text-3 hover:text-ink"
        >
          <PaperclipIcon size={13} />
        </button>
        <input
          ref={picker}
          type="file"
          multiple
          hidden
          onChange={(e) => {
            files.add(Array.from(e.target.files ?? []));
            e.target.value = "";
          }}
        />
        <div className="flex min-w-0 grow">
          {files.drafts.length > 0 ? (
            <DraftChips
              compact
              drafts={files.drafts}
              onRemove={files.remove}
              onRetry={files.retry}
            />
          ) : (
            tr("Enter 보내기 · Shift+Enter 줄바꿈 · Esc 닫기")
          )}
        </div>
        {inbox && (
          <button
            type="button"
            className="cursor-pointer text-text-3 hover:text-ink"
            onClick={() =>
              exit.leave(() => {
                setText("");
                post.reset();
                void openMainWindow(`/c/${inbox.id}`); // the app hides this window
              })
            }
          >
            {tr("인박스 열기")}
          </button>
        )}
      </div>
      <ErrorText error={post.error} />
    </div>
  );
}
