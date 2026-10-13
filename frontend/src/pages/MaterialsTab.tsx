import {
  attachmentUrl,
  useChannelLmsMaterials,
  useMaterials,
  vaultFileUrl,
} from "../api";
import { fmt } from "../dates";
import { inDesktopApp, openAttachment } from "../desktop";
import { tr } from "../i18n";
import { card, ErrorText } from "../ui";
import { useChannel } from "./ChannelPage";
import { NoFolder } from "./NotesTab";

function size(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

const row = "flex items-center gap-3 rounded-xl px-3 py-2 hover:bg-inset";
const badge =
  "rounded-md bg-inset px-1.5 py-0.5 font-mono text-[10.5px] text-text-2 uppercase";
const meta = "font-mono text-[11.5px] whitespace-nowrap text-meta";

/** LMS weekly learning files (saved by the LMS sync) and lecture files in the
 * channel's vault folder (PDF, slides, …), opened in a new tab. */
export function MaterialsTab() {
  const channel = useChannel();
  const lms = useChannelLmsMaterials(channel.id);
  const hasLms = !!lms.data?.length;
  return (
    <div className="flex min-h-0 grow flex-col gap-4 overflow-y-auto pb-[22px]">
      {(lms.error || hasLms) && (
        <div className="flex flex-col gap-4 px-8 max-md:px-4">
          <ErrorText error={lms.error} />
          {hasLms && <LmsMaterials files={lms.data ?? []} />}
        </div>
      )}
      {channel.vault_path ? (
        <VaultMaterials channelId={channel.id} />
      ) : (
        lms.isSuccess && !hasLms && <NoFolder />
      )}
    </div>
  );
}

type LmsFile = NonNullable<
  ReturnType<typeof useChannelLmsMaterials>["data"]
>[number];

function LmsMaterials({ files }: { files: LmsFile[] }) {
  // Newest week first, like the LMS lists them; files keep the server's order.
  const groups = new Map<string, LmsFile[]>();
  for (const f of files)
    groups.set(f.module, [...(groups.get(f.module) ?? []), f]);
  return (
    <section
      aria-label={tr("LMS 주차학습", "LMS weekly learning")}
      className="flex flex-col gap-3"
    >
      <h3 className="m-0 text-[12px] font-medium text-meta">
        {tr("LMS 주차학습", "LMS weekly learning")}
      </h3>
      {[...groups].map(([module, rows]) => (
        <section
          key={module || "."}
          aria-label={module || tr("주차학습 자료", "Weekly materials")}
          className={`${card} flex flex-col p-3`}
        >
          {module && (
            <h4 className="m-0 px-3 pt-1 pb-2 text-[12.5px] font-medium text-text-2">
              {module}
            </h4>
          )}
          {rows.map((f) => (
            <div key={f.attachment_id} className={row}>
              <span className={badge}>{f.name.split(".").pop()}</span>
              <a
                href={attachmentUrl(f.attachment_id)}
                target="_blank"
                rel="noreferrer"
                onClick={(e) => {
                  if (!inDesktopApp()) return;
                  e.preventDefault(); // WKWebView does not open files: use their app
                  void openAttachment(f.attachment_id, f.name);
                }}
                className="min-w-0 grow truncate text-[13.5px] text-ink no-underline"
                title={f.name}
              >
                {f.title}
              </a>
              <span className={meta}>
                {size(f.size)} · {fmt(f.posted_at, "M/d")}
              </span>
              <a
                href={f.url}
                target="_blank"
                rel="noreferrer"
                className="text-[12px] whitespace-nowrap text-text-3 hover:text-ink"
              >
                {tr("LMS에서 열기", "Open in LMS")}
              </a>
            </div>
          ))}
        </section>
      ))}
    </section>
  );
}

function VaultMaterials({ channelId }: { channelId: string }) {
  const materials = useMaterials(channelId);
  const groups = new Map<string, NonNullable<typeof materials.data>>();
  for (const m of materials.data ?? []) {
    groups.set(m.folder, [...(groups.get(m.folder) ?? []), m]);
  }
  return (
    <div className="flex flex-col gap-4 px-8 max-md:px-4">
      <ErrorText error={materials.error} />
      {materials.data?.length === 0 && (
        <div
          className={`${card} px-6 py-8 text-center text-[13.5px] text-text-3`}
        >
          {tr("이 폴더에 자료 파일(PDF, 슬라이드 등)이 없어요.")}
        </div>
      )}
      {[...groups].map(([folder, files]) => (
        <section
          key={folder || "."}
          aria-label={folder || tr("폴더")}
          className={`${card} flex flex-col p-3`}
        >
          {folder && (
            <h3 className="m-0 px-3 pt-1 pb-2 font-mono text-[12px] font-normal text-meta">
              {folder}/
            </h3>
          )}
          {files.map((m) => (
            <a
              key={m.path}
              href={vaultFileUrl(m.path)}
              target="_blank"
              rel="noreferrer"
              className={row}
            >
              <span className={badge}>{m.name.split(".").pop()}</span>
              <span className="min-w-0 grow truncate text-[13.5px] text-ink">
                {m.name}
              </span>
              <span className={meta}>
                {size(m.size)} · {fmt(m.modified_at, "M/d")}
              </span>
            </a>
          ))}
        </section>
      ))}
    </div>
  );
}
