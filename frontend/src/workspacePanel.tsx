import {
  type DragEvent,
  type FormEvent,
  useEffect,
  useRef,
  useState,
} from "react";
import {
  type Channel,
  useChannelFiles,
  useChannelKnowledge,
  useDeleteChannelFile,
  useLinkFolder,
  useUnlinkFolder,
  useUploadChannelFile,
} from "./api";
import { fmt } from "./dates";
import { tr, tt } from "./i18n";
import { CloseIcon, FileIcon, FolderIcon } from "./icons";
import { btn, ErrorText } from "./ui";

export function fileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

const badge =
  "rounded-full px-2 py-px text-[10.5px] font-medium whitespace-nowrap";

function IndexBadge({ indexed }: { indexed: boolean }) {
  return indexed ? (
    <span className={`${badge} bg-inset text-text-2`}>
      {tr("검색 가능", "Searchable")}
    </span>
  ) : (
    <span
      className={`${badge} border border-line-soft text-meta`}
      title={tr(
        "글자를 찾지 못해 검색에는 안 나와요 (이미지·스캔본 등)",
        "No text found, so search can't see it (image, scan…)",
      )}
    >
      {tr("텍스트 없음", "No text")}
    </span>
  );
}

/** Right panel (v0.2): files the channel's agent can read and search — uploads in the
 * channel's workspace and the LMS materials Argos downloaded — with their index state. */
export function WorkspacePanel({
  channel,
  onClose,
}: {
  channel: Channel;
  onClose: () => void;
}) {
  const files = useChannelFiles(channel.id);
  const docs = useChannelKnowledge(channel.id);
  const upload = useUploadChannelFile();
  const remove = useDeleteChannelFile();
  const picker = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState(0);
  const [confirm, setConfirm] = useState<string | null>(null);
  // Every file that failed in this panel, with why; the user clears them.
  const [failed, setFailed] = useState<{ name: string; reason: string }[]>([]);
  // Escape backs out of a pending delete.
  useEffect(() => {
    if (!confirm) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setConfirm(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [confirm]);
  const lms = (docs.data ?? []).filter((d) => d.source === "lms");
  const uploads = files.data ?? [];
  const searchable =
    uploads.filter((f) => f.indexed).length +
    lms.filter((d) => d.chars > 0).length;

  const send = async (list: FileList | File[]) => {
    const picked = [...list];
    setBusy((n) => n + picked.length);
    for (const file of picked) {
      try {
        await upload.mutateAsync({ channelId: channel.id, file });
      } catch (error) {
        const reason = error instanceof Error ? error.message : String(error);
        setFailed((list) => [...list, { name: file.name, reason }]);
      } finally {
        setBusy((n) => n - 1);
      }
    }
  };
  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setOver(false);
    if (e.dataTransfer.files.length) void send(e.dataTransfer.files);
  };

  return (
    <aside
      aria-label={tr("워크스페이스와 자료", "Workspace & knowledge")}
      className="flex w-[340px] shrink-0 flex-col border-l border-line-soft bg-sidebar max-lg:absolute max-lg:inset-y-0 max-lg:right-0 max-lg:z-20 max-lg:shadow-raised max-md:w-full"
    >
      <div
        data-tauri-drag-region="deep"
        className="flex items-center gap-2 px-4 pt-[18px] pb-3"
      >
        <FolderIcon />
        <span className="grow text-[13.5px] font-medium text-ink">
          {tr("워크스페이스 · 자료", "Workspace · Knowledge")}
        </span>
        <span className="text-[11.5px] text-meta">
          {tt`검색 ${searchable}/${uploads.length + lms.length}`}
        </span>
        <button
          type="button"
          aria-label={tr("닫기")}
          className={btn.icon}
          onClick={onClose}
        >
          <CloseIcon />
        </button>
      </div>
      <div className="flex min-h-0 grow flex-col gap-4 overflow-y-auto px-4 pb-4">
        <button
          type="button"
          onClick={() => picker.current?.click()}
          onDragOver={(e) => {
            e.preventDefault();
            setOver(true);
          }}
          onDragLeave={() => setOver(false)}
          onDrop={onDrop}
          className={`flex cursor-pointer flex-col items-center gap-1 rounded-2xl border border-dashed px-4 py-5 text-center ${over ? "border-ink bg-inset" : "border-line bg-card"}`}
        >
          <span className="text-[13px] font-medium text-ink">
            {busy > 0
              ? tt`올리는 중… (${busy})`
              : tr(
                  "파일을 끌어 놓거나 눌러서 올리기",
                  "Drop files or click to upload",
                )}
          </span>
          <span className="text-[11.5px] text-meta">
            {tr(
              "PDF·마크다운·텍스트는 본문까지 검색돼요",
              "PDF, Markdown and text files are searchable",
            )}
          </span>
        </button>
        <input
          ref={picker}
          type="file"
          multiple
          hidden
          data-testid="workspace-upload"
          onChange={(e) => {
            if (e.target.files?.length) void send(e.target.files);
            e.target.value = "";
          }}
        />
        {failed.length > 0 && (
          <div
            role="alert"
            className="flex flex-col gap-1 rounded-xl border border-danger-line bg-danger-bg px-3 py-2 text-[12px] text-danger"
          >
            <div className="flex items-center">
              <span className="grow font-medium">
                {tt`올리지 못한 파일 ${failed.length}개`}
              </span>
              <button
                type="button"
                className="cursor-pointer text-[11.5px] underline"
                onClick={() => setFailed([])}
              >
                {tr("지우기", "Clear")}
              </button>
            </div>
            {failed.map((f, i) => (
              // biome-ignore lint/suspicious/noArrayIndexKey: the same file may fail twice
              <span key={i} className="truncate" title={f.reason}>
                {f.name} — {f.reason}
              </span>
            ))}
          </div>
        )}
        <ErrorText error={remove.error} />

        <section className="flex flex-col gap-1">
          <h3 className="m-0 px-1 text-[11.5px] font-medium text-meta">
            {tt`올린 파일 ${uploads.length}`}
          </h3>
          {files.isSuccess && uploads.length === 0 && (
            <p className="m-0 px-1 text-[12.5px] text-text-3">
              {tr(
                "아직 없어요. 올린 파일은 에이전트가 읽고 검색할 수 있어요.",
                "None yet. The agent can read and search files you upload.",
              )}
            </p>
          )}
          {uploads.map((f) => (
            <div
              key={f.path}
              className="group flex items-center gap-2 rounded-xl px-2 py-1.5 hover:bg-inset"
            >
              <FileIcon size={14} />
              <div className="flex min-w-0 grow flex-col">
                <span
                  className="truncate text-[12.5px] text-ink"
                  title={f.path}
                >
                  {f.path}
                </span>
                <span className="text-[11px] text-meta">
                  {fileSize(f.size)} · {fmt(f.modified_at, "M/d HH:mm")}
                </span>
              </div>
              <IndexBadge indexed={f.indexed} />
              {confirm === f.path ? (
                <span className="flex shrink-0 items-center gap-1">
                  <button
                    type="button"
                    className="h-7 cursor-pointer rounded-full bg-danger px-2.5 text-[11.5px] font-medium text-on-dark"
                    onClick={() => {
                      setConfirm(null);
                      remove.mutate({ channelId: channel.id, path: f.path });
                    }}
                  >
                    {tr("삭제")}
                  </button>
                  <button
                    type="button"
                    className="h-7 cursor-pointer rounded-full px-2 text-[11.5px] text-text-3 hover:text-ink"
                    onClick={() => setConfirm(null)}
                  >
                    {tr("취소")}
                  </button>
                </span>
              ) : (
                <button
                  type="button"
                  aria-label={tt`${f.name} 삭제`}
                  className={`${btn.icon} size-7 opacity-0 group-hover:opacity-100 focus:opacity-100 max-md:opacity-100`}
                  onClick={() => setConfirm(f.path)}
                >
                  <CloseIcon size={13} />
                </button>
              )}
            </div>
          ))}
        </section>

        <section className="flex flex-col gap-1">
          <h3 className="m-0 px-1 text-[11.5px] font-medium text-meta">
            {tt`LMS 자료 ${lms.length}`}
          </h3>
          {docs.isSuccess && lms.length === 0 && (
            <p className="m-0 px-1 text-[12.5px] text-text-3">
              {tr(
                "LMS에서 받은 강의 자료가 여기 색인돼요.",
                "Course materials from the LMS are indexed here.",
              )}
            </p>
          )}
          {lms.map((d) => (
            <div
              key={d.id}
              className="flex items-center gap-2 rounded-xl px-2 py-1.5"
            >
              <FileIcon size={14} />
              <div className="flex min-w-0 grow flex-col">
                <span
                  className="truncate text-[12.5px] text-ink"
                  title={d.title}
                >
                  {d.title}
                </span>
                <span className="text-[11px] text-meta">
                  {fileSize(d.size)}
                </span>
              </div>
              <IndexBadge indexed={d.chars > 0} />
            </div>
          ))}
        </section>

        <LinkedFolders channel={channel} />
      </div>
    </aside>
  );
}

/** Local folders on this computer the channel's agents may work in (read, write, edit
 * with the file tools, as freely as in the channel's own workspace). */
function LinkedFolders({ channel }: { channel: Channel }) {
  const link = useLinkFolder();
  const unlink = useUnlinkFolder();
  const [path, setPath] = useState("");
  const folders = channel.linked_folders ?? [];
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!path.trim()) return;
    link.mutate(
      { channelId: channel.id, path: path.trim() },
      { onSuccess: () => setPath("") },
    );
  };
  return (
    <section className="flex flex-col gap-1.5">
      <h3 className="m-0 px-1 text-[11.5px] font-medium text-meta">
        {tt`연결한 로컬 폴더 ${folders.length}`}
      </h3>
      <p className="m-0 px-1 text-[12px] text-text-3">
        {tr(
          "이 컴퓨터의 폴더를 연결하면 에이전트가 그 안의 파일을 바로 읽고 고칠 수 있어요. 삭제·명령 실행은 여전히 승인을 받아요.",
          "Link a folder on this computer and agents can read and edit its files right away. Deleting and commands still ask first.",
        )}
      </p>
      {folders.map((folder) => (
        <div
          key={folder}
          className="group flex items-center gap-2 rounded-xl px-2 py-1.5 hover:bg-inset"
        >
          <FolderIcon size={14} />
          <span
            className="min-w-0 grow truncate font-mono text-[12px] text-ink"
            title={folder}
          >
            {folder}
          </span>
          <button
            type="button"
            aria-label={tt`${folder} 연결 해제`}
            title={tr("연결 해제", "Unlink")}
            disabled={unlink.isPending}
            onClick={() =>
              unlink.mutate({ channelId: channel.id, path: folder })
            }
            className={`${btn.icon} size-7 opacity-0 group-hover:opacity-100 focus:opacity-100 max-md:opacity-100`}
          >
            <CloseIcon size={13} />
          </button>
        </div>
      ))}
      <form onSubmit={submit} className="flex items-center gap-1.5 px-1">
        <label htmlFor={`link-${channel.id}`} className="sr-only">
          {tr("연결할 폴더 경로", "Folder path to link")}
        </label>
        <input
          id={`link-${channel.id}`}
          value={path}
          onChange={(e) => setPath(e.target.value)}
          placeholder="~/Documents/project"
          className="h-8 min-w-0 grow rounded-full border border-line-soft bg-card px-3 font-mono text-[12px] text-text outline-none placeholder:text-meta focus:border-ink"
        />
        <button
          type="submit"
          disabled={!path.trim() || link.isPending}
          className="h-8 shrink-0 cursor-pointer rounded-full bg-inset px-3 text-[12px] font-medium text-text-2 hover:text-ink disabled:opacity-50"
        >
          {tr("연결", "Link")}
        </button>
      </form>
      <ErrorText error={link.error ?? unlink.error} />
    </section>
  );
}
