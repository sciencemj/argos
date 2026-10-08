import { type DragEvent, useEffect, useReducer, useRef, useState } from "react";
import {
  type Attachment,
  attachmentUrl,
  deleteAttachment,
  uploadAttachment,
} from "./api";
import { inDesktopApp, openAttachment } from "./desktop";
import { tr } from "./i18n";
import { CloseIcon, FileIcon } from "./icons";
import { PawTrail } from "./paws";

/** Mirrors attachments.MAX_FILES on the server. */
export const MAX_FILES = 10;
const TOO_MANY = "첨부는 10개까지 붙일 수 있어요";

export type Draft = {
  key: string;
  file: File;
  status: "uploading" | "done" | "error";
  attachment?: Attachment;
  error?: string;
  preview?: string; // object URL of an image, revoked when the chip goes
};

type Action =
  | { type: "add"; drafts: Draft[] }
  | { type: "done"; key: string; attachment: Attachment }
  | { type: "error"; key: string; error: string }
  | { type: "retry"; key: string }
  | { type: "remove"; key: string }
  | { type: "clear" };

export function draftsReducer(state: Draft[], action: Action): Draft[] {
  const patch = (key: string, change: Partial<Draft>) =>
    state.map((d) => (d.key === key ? { ...d, ...change } : d));
  switch (action.type) {
    case "add":
      return [...state, ...action.drafts];
    case "done":
      return patch(action.key, {
        status: "done",
        attachment: action.attachment,
      });
    case "error":
      return patch(action.key, { status: "error", error: action.error });
    case "retry":
      return patch(action.key, { status: "uploading", error: undefined });
    case "remove":
      return state.filter((d) => d.key !== action.key);
    case "clear":
      return [];
  }
}

/** Text or at least one file, and no chip still uploading or failed. */
export const canSend = (text: string, drafts: Draft[]) =>
  drafts.every((d) => d.status === "done") &&
  (text.trim() !== "" || drafts.length > 0);

export const hasFiles = (data: DataTransfer | null) =>
  Boolean(data && Array.from(data.types ?? []).includes("Files"));

/** Files from a paste. Spreadsheets and rich-text apps add a picture of what was copied
 * next to the text; then the text wins. Finder puts the file names there instead. */
export function pastedFiles(data: DataTransfer | null): File[] {
  if (!data) return [];
  const files = Array.from(data.files ?? []);
  if (files.length === 0) return [];
  const text = data.getData("text/plain").trim();
  if (text && !files.every((f) => text.includes(f.name))) return [];
  return files;
}

export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

const forget = (d: Draft) => {
  if (d.preview) URL.revokeObjectURL(d.preview);
};

/** Composer state: every file starts uploading the moment it is added. */
export function useAttachmentDrafts() {
  const [drafts, dispatch] = useReducer(draftsReducer, []);
  const latest = useRef(drafts);
  latest.current = drafts;

  const upload = (draft: Draft) =>
    uploadAttachment(draft.file).then(
      (attachment) => dispatch({ type: "done", key: draft.key, attachment }),
      (error: unknown) =>
        dispatch({
          type: "error",
          key: draft.key,
          error: error instanceof Error ? error.message : String(error),
        }),
    );

  const add = (files: File[]) => {
    const room = MAX_FILES - latest.current.length;
    const fresh: Draft[] = files.map((file, index) => ({
      key: crypto.randomUUID(),
      file,
      status: index < room ? "uploading" : "error",
      error: index < room ? undefined : tr(TOO_MANY),
      preview: file.type.startsWith("image/")
        ? URL.createObjectURL(file)
        : undefined,
    }));
    latest.current = [...latest.current, ...fresh];
    dispatch({ type: "add", drafts: fresh });
    for (const d of fresh) if (d.status === "uploading") void upload(d);
  };

  const remove = (key: string) => {
    const d = latest.current.find((x) => x.key === key);
    if (!d) return;
    forget(d);
    if (d.attachment) void deleteAttachment(d.attachment.id).catch(() => {});
    dispatch({ type: "remove", key });
  };

  const retry = (key: string) => {
    const d = latest.current.find((x) => x.key === key);
    if (!d || d.error === tr(TOO_MANY)) return;
    dispatch({ type: "retry", key });
    void upload(d);
  };

  /** After a send: the files now belong to the message, only the previews go. */
  const clear = () => {
    for (const d of latest.current) forget(d);
    dispatch({ type: "clear" });
  };

  useEffect(
    () => () => {
      for (const d of latest.current) forget(d);
    },
    [],
  );

  return {
    drafts,
    add,
    remove,
    retry,
    clear,
    ids: drafts.flatMap((d) => (d.attachment ? [d.attachment.id] : [])),
  };
}

/** Drag-and-drop of files from Finder onto an area. */
export function useFileDrop(add: (files: File[]) => void) {
  const [over, setOver] = useState(false);
  const handlers = {
    onDragOver: (e: DragEvent) => {
      if (!hasFiles(e.dataTransfer)) return;
      e.preventDefault();
      setOver(true);
    },
    onDragLeave: (e: DragEvent) => {
      if (!e.currentTarget.contains(e.relatedTarget as Node | null))
        setOver(false);
    },
    onDrop: (e: DragEvent) => {
      if (!hasFiles(e.dataTransfer)) return;
      e.preventDefault();
      setOver(false);
      add(Array.from(e.dataTransfer.files));
    },
  };
  return { over, handlers };
}

export function DraftChips({
  drafts,
  onRemove,
  onRetry,
  compact = false,
}: {
  drafts: Draft[];
  onRemove: (key: string) => void;
  onRetry: (key: string) => void;
  /** The quick-capture window is too short for thumbnails. */
  compact?: boolean;
}) {
  if (drafts.length === 0) return null;
  return (
    <ul
      className="m-0 flex list-none flex-wrap gap-1.5 p-0"
      aria-label={tr("첨부")}
    >
      {drafts.map((d) => (
        <li
          key={d.key}
          title={d.status === "error" ? d.error : undefined}
          className={`flex max-w-[240px] items-center gap-2 rounded-xl border bg-inset py-1 pr-1 pl-1.5 text-[12px] ${d.status === "error" ? "border-danger" : "border-line-soft"}`}
        >
          {!compact && d.preview ? (
            <img
              src={d.preview}
              alt=""
              className="size-8 rounded-lg object-cover"
            />
          ) : (
            <span className="text-meta">
              <FileIcon size={compact ? 13 : 16} />
            </span>
          )}
          <span className="flex min-w-0 flex-col">
            <span className="truncate text-text">{d.file.name}</span>
            {!compact && (
              <span
                className={d.status === "error" ? "text-danger" : "text-meta"}
              >
                {d.status === "error" ? d.error : formatSize(d.file.size)}
              </span>
            )}
          </span>
          {d.status === "uploading" && <PawTrail />}
          {d.status === "error" && d.error !== tr(TOO_MANY) && (
            <button
              type="button"
              onClick={() => onRetry(d.key)}
              className="cursor-pointer text-[11.5px] font-medium text-text-2 underline hover:text-ink"
            >
              {tr("다시 시도")}
            </button>
          )}
          <button
            type="button"
            aria-label={`${tr("첨부 빼기")}: ${d.file.name}`}
            onClick={() => onRemove(d.key)}
            className="flex size-6 cursor-pointer items-center justify-center rounded-full text-meta hover:text-ink"
          >
            <CloseIcon size={12} />
          </button>
        </li>
      ))}
    </ul>
  );
}

export function AttachmentList({ attachments }: { attachments: Attachment[] }) {
  const [open, setOpen] = useState<number | null>(null);
  if (attachments.length === 0) return null;
  const shown = (a: Attachment) => a.kind === "image" && !a.missing;
  const images = attachments.filter(shown);
  const others = attachments.filter((a) => !shown(a));
  const single = images.length === 1;
  return (
    <div className="flex flex-col gap-2">
      {images.length > 0 && (
        <div
          className={single ? "flex" : "grid max-w-[480px] grid-cols-3 gap-1.5"}
        >
          {images.map((a, index) => (
            <button
              key={a.id}
              type="button"
              onClick={() => setOpen(index)}
              className="cursor-zoom-in overflow-hidden rounded-2xl border border-line-soft bg-inset p-0"
              style={
                single && a.width && a.height
                  ? {
                      aspectRatio: `${a.width} / ${a.height}`,
                      width: Math.min(420, a.width),
                      maxHeight: 320,
                    }
                  : { aspectRatio: "1" }
              }
            >
              <img
                src={attachmentUrl(a.id)}
                alt={a.name}
                loading="lazy"
                className="size-full object-cover"
              />
            </button>
          ))}
        </div>
      )}
      {others.map((a) =>
        a.missing ? (
          <div
            key={a.id}
            className="flex max-w-[360px] items-center gap-2.5 rounded-2xl border border-line-soft bg-inset px-3 py-2 opacity-60"
          >
            <FileIcon />
            <span className="truncate text-[13px] text-text-2">{a.name}</span>
            <span className="ml-auto shrink-0 text-[12px] text-meta">
              {tr("파일 없음")}
            </span>
          </div>
        ) : (
          <a
            key={a.id}
            href={attachmentUrl(a.id)}
            download={a.name}
            onClick={(e) => {
              if (!inDesktopApp()) return;
              e.preventDefault(); // WKWebView does not download: open it in its app
              void openAttachment(a.id, a.name);
            }}
            className="flex max-w-[360px] items-center gap-2.5 rounded-2xl border border-line-soft bg-card px-3 py-2 text-text no-underline hover:border-line"
          >
            <span className="text-meta">
              <FileIcon />
            </span>
            <span className="truncate text-[13px]">{a.name}</span>
            <span className="ml-auto shrink-0 font-mono text-[11.5px] text-meta">
              {formatSize(a.size)}
            </span>
          </a>
        ),
      )}
      {open !== null && images[open] && (
        <Lightbox
          images={images}
          index={open}
          onIndex={setOpen}
          onClose={() => setOpen(null)}
        />
      )}
    </div>
  );
}

function Lightbox({
  images,
  index,
  onIndex,
  onClose,
}: {
  images: Attachment[];
  index: number;
  onIndex: (index: number) => void;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    ref.current?.showModal();
  }, []);
  const image = images[index];
  const step = (by: number) =>
    onIndex((index + by + images.length) % images.length);
  return (
    <dialog
      ref={ref}
      aria-label={image.name}
      onClose={onClose}
      onClick={(e) => {
        if (e.target === e.currentTarget) ref.current?.close();
      }}
      onKeyDown={(e) => {
        if (e.key === "ArrowRight") step(1);
        if (e.key === "ArrowLeft") step(-1);
      }}
      className="m-auto max-h-[92vh] max-w-[92vw] border-0 bg-transparent p-0 backdrop:bg-black/70"
    >
      <img
        src={attachmentUrl(image.id)}
        alt={image.name}
        className="block max-h-[88vh] max-w-[92vw] rounded-2xl object-contain"
      />
      <p className="m-0 mt-2 text-center text-[12.5px] text-on-dark">
        {image.name}
        {images.length > 1 && ` · ${index + 1}/${images.length}`}
      </p>
    </dialog>
  );
}
