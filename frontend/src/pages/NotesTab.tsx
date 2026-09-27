import { useState } from "react";
import { useSearchParams } from "react-router";
import {
  type Note,
  type NoteSort,
  type SortOrder,
  useNote,
  useNotes,
  vaultFileUrl,
} from "../api";
import { fmt } from "../dates";
import { tr, tt } from "../i18n";
import { Markdown } from "../markdown";
import { card, ErrorText, field } from "../ui";
import { useChannel } from "./ChannelPage";

/** Obsidian's own syntax, turned into plain Markdown for reading: [[note|label]] →
 * label, ![[image.png]] → an image from the vault, %%comments%% dropped. */
export function obsidianToMarkdown(body: string, notePath: string): string {
  const folder = notePath.includes("/")
    ? notePath.slice(0, notePath.lastIndexOf("/"))
    : "";
  return body
    .replace(/%%[\s\S]*?%%/g, "")
    .replace(
      /!\[\[([^\]|]+?\.(?:png|jpe?g|gif|webp|svg))(?:\|[^\]]*)?\]\]/gi,
      (_, name: string) => {
        const path = name.includes("/")
          ? name
          : folder
            ? `${folder}/${name}`
            : name;
        return `![${name}](${vaultFileUrl(path)})`;
      },
    )
    .replace(/!\[\[([^\]]+)\]\]/g, (_, name: string) => tt`*(첨부: ${name})*`)
    .replace(/\[\[([^\]|]+)\|([^\]]+)\]\]/g, "$2")
    .replace(/\[\[([^\]]+)\]\]/g, "$1")
    .replace(/\s\^[A-Za-z0-9-]+$/gm, "");
}

function Snippet({ text }: { text: string }) {
  // The server marks the match with [ and ].
  const parts = text.split(/(\[[^\]]*\])/);
  return (
    <span className="line-clamp-2 text-[12px] leading-[1.5] text-text-3">
      {parts.map((part, i) =>
        part.startsWith("[") && part.endsWith("]") ? (
          // biome-ignore lint/suspicious/noArrayIndexKey: static split of one string
          <mark key={i} className="rounded-sm bg-inset px-0.5 text-ink">
            {part.slice(1, -1)}
          </mark>
        ) : (
          part
        ),
      )}
    </span>
  );
}

const SORTS: { id: NoteSort; label: string }[] = [
  { id: "relevance", label: tr("관련도") },
  { id: "modified", label: tr("수정") },
  { id: "title", label: tr("이름") },
  { id: "path", label: tr("경로") },
];
const SORT_KEY = "argos.notes.sort";

type SortChoice = { sort: Exclude<NoteSort, "relevance">; order: SortOrder };

/** The list order stays as the viewer left it (this browser only). */
function savedSort(): SortChoice {
  try {
    const saved = JSON.parse(
      localStorage.getItem(SORT_KEY) ?? "null",
    ) as SortChoice | null;
    if (saved && ["modified", "title", "path"].includes(saved.sort))
      return saved;
  } catch {
    // private window or blocked storage: use the default
  }
  return { sort: "modified", order: "desc" };
}

function SortBar({
  sort,
  order,
  searching,
  onSort,
  onOrder,
}: {
  sort: NoteSort;
  order: SortOrder;
  searching: boolean;
  onSort: (sort: NoteSort) => void;
  onOrder: () => void;
}) {
  const labelOf = (s: NoteSort) =>
    s === "modified"
      ? order === "desc"
        ? tr("최근 것부터")
        : tr("오래된 것부터")
      : s === "relevance"
        ? order === "asc"
          ? tr("잘 맞는 것부터")
          : tr("덜 맞는 것부터")
        : order === "asc"
          ? tr("가나다순")
          : tr("역순");
  return (
    <div className="mx-1 flex items-center gap-1">
      <fieldset className="m-0 flex min-w-0 gap-0.5 rounded-full border-0 bg-inset p-[2px] text-[12px]">
        <legend className="sr-only">{tr("정렬 기준")}</legend>
        {SORTS.filter((s) => searching || s.id !== "relevance").map((s) => (
          <button
            key={s.id}
            type="button"
            aria-pressed={sort === s.id}
            onClick={() => onSort(s.id)}
            className="h-6 cursor-pointer rounded-full px-2.5 text-text-3 aria-pressed:bg-card aria-pressed:font-medium aria-pressed:text-ink aria-pressed:shadow-sm"
          >
            {s.label}
          </button>
        ))}
      </fieldset>
      <span className="grow" />
      <button
        type="button"
        onClick={onOrder}
        aria-label={tt`순서 바꾸기 (지금: ${labelOf(sort)})`}
        title={labelOf(sort)}
        className="flex h-6 cursor-pointer items-center gap-1 rounded-full px-2 text-[12px] text-text-3 hover:bg-inset hover:text-ink"
      >
        <span aria-hidden="true">{order === "asc" ? "↑" : "↓"}</span>
        {labelOf(sort)}
      </button>
    </div>
  );
}

/** Notes in the channel's vault folder: search and read-only reading (PLAN Phase 8). */
export function NotesTab() {
  const channel = useChannel();
  const [params, setParams] = useSearchParams();
  const [query, setQuery] = useState("");
  const [choice, setChoice] = useState<SortChoice>(savedSort);
  // While searching, best match first unless the viewer picks another order.
  const [searchSort, setSearchSort] = useState<{
    sort: NoteSort;
    order: SortOrder;
  }>({
    sort: "relevance",
    order: "asc",
  });
  const searching = query.trim() !== "";
  const active = searching ? searchSort : choice;
  const notes = useNotes(channel.id, query.trim(), active.sort, active.order);

  const pick = (sort: NoteSort) => {
    // Dates start newest first; names and match quality start from the top.
    const order: SortOrder = sort === "modified" ? "desc" : "asc";
    if (searching) setSearchSort({ sort, order });
    else if (sort !== "relevance") remember({ sort, order });
  };
  const flip = () => {
    const order: SortOrder = active.order === "asc" ? "desc" : "asc";
    if (searching) setSearchSort({ ...searchSort, order });
    else remember({ ...choice, order });
  };
  const remember = (next: SortChoice) => {
    setChoice(next);
    try {
      localStorage.setItem(SORT_KEY, JSON.stringify(next));
    } catch {
      // not saved; the choice still applies until the page is left
    }
  };
  const selected = params.get("note");

  const open = (note: Note) => {
    const next = new URLSearchParams(params);
    next.set("note", note.id);
    setParams(next);
  };

  if (!channel.vault_path) {
    return <NoFolder />;
  }

  return (
    <div className="grid min-h-0 grow grid-cols-[minmax(260px,340px)_1fr] gap-4 px-8 pb-[22px]">
      <div className={`${card} flex min-h-0 flex-col gap-2 p-3`}>
        <input
          type="search"
          aria-label={tr("노트 검색")}
          placeholder={tr("노트 검색 (제목·본문)")}
          className={`${field} mx-1 w-auto`}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <SortBar
          sort={active.sort}
          order={active.order}
          searching={searching}
          onSort={pick}
          onOrder={flip}
        />
        <ErrorText error={notes.error} />
        <ul className="m-0 flex min-h-0 list-none flex-col gap-0.5 overflow-y-auto p-0">
          {notes.data?.map((n) => (
            <li key={n.id}>
              <button
                type="button"
                onClick={() => open(n)}
                aria-current={n.id === selected ? "true" : undefined}
                className="flex w-full cursor-pointer flex-col gap-0.5 rounded-xl px-3 py-2 text-left hover:bg-inset aria-[current=true]:bg-inset"
              >
                <span className="truncate text-[13.5px] text-ink">
                  {n.title}
                </span>
                {n.snippet ? (
                  <Snippet text={n.snippet} />
                ) : (
                  <span className="truncate font-mono text-[11px] text-meta">
                    {n.vault_path
                      .slice(channel.vault_path?.length ?? 0)
                      .replace(/^\//, "")}{" "}
                    · {fmt(n.modified_at, "M/d")}
                  </span>
                )}
              </button>
            </li>
          ))}
          {notes.data?.length === 0 && (
            <li className="px-3 py-6 text-center text-[13px] text-text-3">
              {query ? tr("찾는 노트가 없어요") : tr("이 폴더에 노트가 없어요")}
            </li>
          )}
        </ul>
      </div>
      <NoteReader noteId={selected} />
    </div>
  );
}

function NoteReader({ noteId }: { noteId: string | null }) {
  const note = useNote(noteId);
  if (!noteId) {
    return (
      <div
        className={`${card} flex items-center justify-center text-[13px] text-text-3`}
      >
        {tr("왼쪽에서 노트를 고르세요")}
      </div>
    );
  }
  return (
    <article
      aria-label={tr("노트")}
      className={`${card} flex min-h-0 flex-col gap-3 overflow-y-auto px-8 py-6`}
    >
      <ErrorText error={note.error} />
      {note.data && (
        <>
          <header className="flex flex-col gap-1">
            <h2 className="m-0 text-[22px] font-light tracking-[-0.02em] text-ink">
              {note.data.title}
            </h2>
            <span className="font-mono text-[11.5px] text-meta">
              {note.data.vault_path} · {fmt(note.data.modified_at, "M/d HH:mm")}{" "}
              {tr("· 읽기 전용")}
            </span>
          </header>
          <div className="text-[14px] leading-[1.7] text-text [&_img]:max-w-full [&_img]:rounded-lg">
            <Markdown
              text={obsidianToMarkdown(note.data.body, note.data.vault_path)}
            />
          </div>
        </>
      )}
    </article>
  );
}

export function NoFolder() {
  return (
    <div className="px-8 pb-[22px]">
      <div
        className={`${card} flex flex-col gap-1 px-6 py-8 text-center text-[13.5px] text-text-3`}
      >
        <span>{tr("이 채널에 연결된 옵시디언 폴더가 없어요.")}</span>
        <span className="text-[12.5px]">
          {tr(
            "채널 설정(오른쪽 위 톱니바퀴)에서 폴더를 고르세요. 볼트는 앱 설정에서 지정해요.",
          )}
        </span>
      </div>
    </div>
  );
}
