import { useMaterials, vaultFileUrl } from "../api";
import { fmt } from "../dates";
import { card, ErrorText } from "../ui";
import { useChannel } from "./ChannelPage";
import { NoFolder } from "./NotesTab";

function size(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/** Lecture files (PDF, slides, …) in the channel's vault folder, opened in a new tab. */
export function MaterialsTab() {
  const channel = useChannel();
  const materials = useMaterials(channel.id);
  if (!channel.vault_path) return <NoFolder />;
  const groups = new Map<string, NonNullable<typeof materials.data>>();
  for (const m of materials.data ?? []) {
    groups.set(m.folder, [...(groups.get(m.folder) ?? []), m]);
  }
  return (
    <div className="flex min-h-0 grow flex-col gap-4 overflow-y-auto px-8 pb-[22px]">
      <ErrorText error={materials.error} />
      {materials.data?.length === 0 && (
        <div
          className={`${card} px-6 py-8 text-center text-[13.5px] text-text-3`}
        >
          이 폴더에 자료 파일(PDF, 슬라이드 등)이 없어요.
        </div>
      )}
      {[...groups].map(([folder, files]) => (
        <section
          key={folder || "."}
          aria-label={folder || "폴더"}
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
              className="flex items-center gap-3 rounded-xl px-3 py-2 hover:bg-inset"
            >
              <span className="rounded-md bg-inset px-1.5 py-0.5 font-mono text-[10.5px] text-text-2 uppercase">
                {m.name.split(".").pop()}
              </span>
              <span className="min-w-0 grow truncate text-[13.5px] text-ink">
                {m.name}
              </span>
              <span className="font-mono text-[11.5px] whitespace-nowrap text-meta">
                {size(m.size)} · {fmt(m.modified_at, "M/d")}
              </span>
            </a>
          ))}
        </section>
      ))}
    </div>
  );
}
