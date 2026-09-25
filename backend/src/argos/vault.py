"""Obsidian vault: note index and search, checkbox tasks, and careful file edits
(PLAN Phase 8).

Notes stay in the vault. Argos keeps `note_ref` rows and a full-text index (`note_fts`)
and reads bodies from disk when a note is opened. Open checkbox tasks in notes under a
channel's folder (and in recent daily notes) become Argos tasks; the line gets a block
ID at its end (`^argos-xxxxxx`) so it can be found again after edits and moves.
Checking a box in either place checks it in the other.

Editing a note is limited to what PLAN §8.3 allows: one line at a time (the block ID
or the `[ ]`/`[x]` mark), only when the file is unchanged since it was read, with a
backup copy first and a check that no other line changed.

Nothing here assumes one person's setup: the vault is chosen in settings (with the
vaults Obsidian knows offered), the daily notes folder comes from the vault's own
settings, and both the Tasks plugin (📅 2026-10-01) and Dataview ([due:: 2026-10-01])
styles are read, as are plain checkboxes."""

import asyncio
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import sys
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, cast

import frontmatter
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from argos import services
from argos.config import Settings
from argos.hub import hub
from argos.models import Channel, NoteRef, SourceLink, Task, TaskStatus

log = logging.getLogger(__name__)

SOURCE = "vault"
ACTOR = "sync:vault"
MATERIAL_TYPES = {
    ".pdf", ".ppt", ".pptx", ".doc", ".docx", ".hwp", ".hwpx", ".xls", ".xlsx", ".csv",
    ".key", ".pages", ".numbers", ".zip", ".ipynb", ".png", ".jpg", ".jpeg", ".gif", ".webp",
}  # fmt: skip

# --- finding the vault ------------------------------------------------------------------


def obsidian_config_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/obsidian"
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home())) / "obsidian"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "obsidian"


def detect_vaults() -> list[Path]:
    """Vaults the Obsidian app knows on this machine (its obsidian.json, read only)."""
    try:
        data = json.loads((obsidian_config_dir() / "obsidian.json").read_text())
    except (OSError, ValueError):
        return []
    paths = [Path(v["path"]) for v in data.get("vaults", {}).values() if v.get("path")]
    return [p for p in paths if p.is_dir()]


def _get(data: object, key: str) -> object:
    return cast(dict[str, object], data).get(key) if isinstance(data, dict) else None


def detect_daily_folder(vault: Path) -> str | None:
    """The folder the vault's Daily Notes (or Periodic Notes) plugin writes to."""
    for relative, section in (
        (".obsidian/daily-notes.json", None),
        (".obsidian/plugins/periodic-notes/data.json", "daily"),
    ):
        try:
            data: Any = json.loads((vault / relative).read_text())
        except (OSError, ValueError):
            continue
        if section is not None:
            data = _get(data, section)
        folder = _get(data, "folder")
        if isinstance(folder, str) and folder.strip("/"):
            return folder.strip("/")
    return None


def resolve_inside(vault: Path, relative: str) -> Path:
    """Absolute path of `relative` inside the vault; refuses anything that escapes it
    (.., absolute paths, symlinks out)."""
    root = vault.resolve()
    if Path(relative).is_absolute():
        raise services.InvalidError("볼트 안의 상대 경로를 적어 주세요")
    target = (root / relative).resolve()
    if target != root and not target.is_relative_to(root):
        raise services.InvalidError("볼트 밖의 경로예요")
    return target


def _hidden(part: str) -> bool:
    return part.startswith(".")


def walk(vault: Path) -> Iterable[tuple[str, Path]]:
    """(relative path, absolute path) of every file outside hidden folders."""
    root = vault.resolve()
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if not _hidden(d))
        for name in sorted(files):
            if not _hidden(name):
                path = Path(folder) / name
                yield path.relative_to(root).as_posix(), path


def folders(vault: Path, depth: int = 3) -> list[str]:
    root = vault.resolve()
    found: list[str] = []
    for folder, dirs, _ in os.walk(root):
        rel = Path(folder).relative_to(root)
        dirs[:] = sorted(d for d in dirs if not _hidden(d))
        if len(rel.parts) >= depth:
            dirs[:] = []
        if rel.parts:
            found.append(rel.as_posix())
    return found


# --- checkbox tasks -------------------------------------------------------------------------

TASK_LINE = re.compile(
    r"^(?P<lead>\s*(?:[-*+]|\d+[.)])\s+\[)(?P<mark>[^\]])(?P<close>\]\s+)(?P<text>.*?)"
    r"(?:\s+\^(?P<block>[A-Za-z0-9-]+))?\s*$"
)
DATE = r"\d{4}-\d{2}-\d{2}"
PRIORITIES = {"🔺": 3, "⏫": 2, "🔼": 1, "🔽": 0, "⏬": 0}
DATAVIEW_FIELD = re.compile(r"\s*[\[(](?P<key>[\w-]+)::\s*(?P<value>[^\])]*)[\])]")
EMOJI_FIELD = re.compile(
    r"\s*(?P<emoji>[📅⏳🛫✅❌➕])️?\s*(?P<date>" + DATE + r")"
    r"|\s*🔁️?\s*[^📅⏳🛫✅❌➕🔺⏫🔼🔽⏬🆔⛔^]*"
    r"|\s*[🆔⛔]️?\s*[\w,-]*"
    r"|\s*(?P<priority>[🔺⏫🔼🔽⏬])️?"
)
DATAVIEW_PRIORITY = {"highest": 3, "high": 2, "medium": 1, "low": 0, "lowest": 0}
DONE_MARKS = {"x", "X"}
SKIP_MARKS = {"-"}  # cancelled (Tasks plugin): never imported


@dataclass
class NoteTask:
    line: int  # 0-based
    mark: str
    title: str
    due: date | None
    priority: int | None
    block: str | None
    at: time | None = None  # a time written in the line ("14:00", "11:00~12:30" → 11:00)
    links: list[str] = field(default_factory=list[str])  # URLs, kept out of the title

    @property
    def done(self) -> bool:
        return self.mark in DONE_MARKS

    def digest(self) -> str:
        due = self.due.isoformat() if self.due else None
        data = [
            self.title,
            due,
            self.priority,
            self.at.isoformat() if self.at else None,
            self.links,
        ]
        return hashlib.sha256(json.dumps(data).encode()).hexdigest()


def _fence(line: str) -> bool:
    return line.lstrip().startswith(("```", "~~~"))


def parse_tasks(body: str) -> list[NoteTask]:
    """Checkbox lines outside code blocks and the frontmatter."""
    tasks: list[NoteTask] = []
    in_code = False
    lines = body.split("\n")
    start = 0
    if lines and lines[0].rstrip("\r") == "---":  # frontmatter
        for i in range(1, len(lines)):
            if lines[i].rstrip("\r") in ("---", "..."):
                start = i + 1
                break
    for number in range(start, len(lines)):
        line = lines[number].rstrip("\r")
        if _fence(line):
            in_code = not in_code
            continue
        if in_code or (match := TASK_LINE.match(line)) is None:
            continue
        raw = match["text"]
        due: date | None = None
        priority: int | None = None
        for field_match in EMOJI_FIELD.finditer(raw):
            if field_match["emoji"] == "📅":
                due = _date(field_match["date"])
            elif field_match["priority"]:
                priority = PRIORITIES[field_match["priority"]]
        for field_match in DATAVIEW_FIELD.finditer(raw):
            key, value = field_match["key"].lower(), field_match["value"].strip()
            if key == "due":
                due = _date(value[:10])
            elif key == "priority":
                priority = DATAVIEW_PRIORITY.get(value.lower(), priority)
        title, at, links = _clean_title(DATAVIEW_FIELD.sub("", EMOJI_FIELD.sub("", raw)))
        if title:
            tasks.append(
                NoteTask(number, match["mark"], title, due, priority, match["block"], at, links)
            )
    return tasks


# Obsidian tags: "#word" (never all digits, so "#1" stays), not "C#" or URL fragments.
TAG = re.compile(r"(?<![\w&/])#(?!\d+(?![^\s#\[\](),.!?]))[^\s#\[\](),.!?]+")
URL = re.compile(r"<?https?://[^\s>]+>?")
CLOCK = r"(?:[01]?\d|2[0-3]):[0-5]\d"
TIME_RANGE = re.compile(rf"(?<![\d:])(?P<start>{CLOCK})(?:\s*[~\-–—]\s*{CLOCK})?(?![\d:])")


def _clean_title(text: str) -> tuple[str, time | None, list[str]]:
    """The line's words as a card title: #tags and times left out (the first time is
    returned for the due time), links returned separately for the description."""
    links = [u.strip("<>") for u in URL.findall(text)]
    text = URL.sub(" ", text)
    at: time | None = None
    if (found := TIME_RANGE.search(text)) is not None:
        hour, minute = found["start"].split(":")
        at = time(int(hour), int(minute))
    text = TIME_RANGE.sub(" ", TAG.sub(" ", text))
    text = re.sub(r"\(\s*\)|\[\s*\]", " ", text)  # brackets emptied by the removals
    return " ".join(text.split()).strip(" -–—·:,"), at, links


def _date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def with_mark(line: str, done: bool) -> str:
    match = TASK_LINE.match(line.rstrip("\r"))
    assert match is not None
    at = match.start("mark")
    return line[:at] + ("x" if done else " ") + line[at + 1 :]


def with_block(line: str, block: str) -> str:
    ending = "\r" if line.endswith("\r") else ""
    return f"{line.rstrip()} ^{block}{ending}"


# --- careful file edits ---------------------------------------------------------------------


class VaultChanged(Exception):
    """The file changed between reading and writing; the edit is skipped this round."""


def edit_lines(
    vault: Path,
    relative: str,
    original: bytes,
    edits: dict[int, Callable[[str], str]],
    backups: Path,
) -> bytes:
    """Rewrites only the given lines of a note. Refuses when the file no longer matches
    `original`; keeps a backup of the old file; verifies the result line by line."""
    path = resolve_inside(vault, relative)
    text_before = original.decode("utf-8")
    lines = text_before.split("\n")
    for number, change in edits.items():
        lines[number] = change(lines[number])
    updated = "\n".join(lines).encode("utf-8")
    after = updated.decode("utf-8").split("\n")
    before = text_before.split("\n")
    changed = {i for i, (a, b) in enumerate(zip(before, after, strict=True)) if a != b}
    if not changed <= set(edits) or len(before) != len(after):
        raise AssertionError(f"unexpected lines changed in {relative}")
    if path.read_bytes() != original:
        raise VaultChanged(relative)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    backup = backups / stamp / relative
    backup.parent.mkdir(parents=True, exist_ok=True)
    backup.write_bytes(original)
    temporary = path.with_name(f".{path.name}.argos-{secrets.token_hex(3)}")
    temporary.write_bytes(updated)
    shutil.copymode(path, temporary)
    if path.read_bytes() != original:  # last look before replacing
        temporary.unlink()
        raise VaultChanged(relative)
    os.replace(temporary, path)
    return updated


def create_note(vault: Path, folder: str, title: str, body: str, day: date) -> str:
    """A new note (never an existing file changed): `<folder>/<YYYY-MM-DD> <title>.md`."""
    directory = resolve_inside(vault, folder)
    if not directory.is_dir():
        raise services.InvalidError(f"볼트에 '{folder}' 폴더가 없어요")
    safe = re.sub(r'[\\/:*?"<>|#^\[\]]', " ", title).strip()[:80] or "노트"
    name = f"{day.isoformat()} {safe}"
    path = directory / f"{name}.md"
    counter = 2
    while path.exists():
        path = directory / f"{name} {counter}.md"
        counter += 1
    path.write_text(f"# {title.strip()}\n\n{body.strip()}\n", encoding="utf-8")
    return path.relative_to(vault.resolve()).as_posix()


# --- index ------------------------------------------------------------------------------------


def _channel_for(relative: str, mapping: Sequence[tuple[str, str]]) -> str | None:
    """Longest channel folder that contains the note."""
    for folder, channel_id in mapping:
        if relative == folder or relative.startswith(folder + "/"):
            return channel_id
    return None


async def channel_folders(session: AsyncSession) -> list[tuple[str, str]]:
    rows = await session.scalars(select(Channel).where(Channel.vault_path.is_not(None)))
    pairs = [
        (c.vault_path.strip("/"), c.id) for c in rows if c.vault_path and c.vault_path.strip("/")
    ]
    return sorted(pairs, key=lambda p: len(p[0]), reverse=True)


def _read_note(path: Path) -> tuple[bytes, str, list[str], str]:
    raw = path.read_bytes()
    content = raw.decode("utf-8", errors="replace")
    try:
        post = frontmatter.loads(content)
        meta, body = post.metadata, post.content
    except Exception:  # broken YAML: index the whole text
        meta, body = {}, content
    title = str(meta.get("title") or path.stem)
    tags_value: object = meta.get("tags") or []
    values = cast(list[object], tags_value) if isinstance(tags_value, list) else [tags_value]
    tags = [str(t) for t in values]
    return raw, title, tags, body


CONFLICT_COPY = re.compile(r"^(?P<base>.+) \d+\.md$")


@dataclass
class IndexResult:
    notes: int = 0
    changed: int = 0
    removed: int = 0
    warnings: list[str] = field(default_factory=list[str])


async def index(session: AsyncSession, vault: Path, now: datetime) -> IndexResult:
    """Brings note_ref and note_fts in line with the vault. Unchanged files (same
    modification time) are not read again. Writes directly, not through services:
    hundreds of index rows are not user activity."""
    result = IndexResult()
    files = await asyncio.to_thread(
        lambda: [(r, p, p.stat().st_mtime) for r, p in walk(vault) if r.endswith(".md")]
    )
    mapping = await channel_folders(session)
    known = {n.vault_path: n for n in (await session.scalars(select(NoteRef))).all()}
    names = {r for r, _, _ in files}
    for relative, path, mtime in files:
        result.notes += 1
        modified = datetime.fromtimestamp(mtime, UTC)
        note = known.get(relative)
        channel_id = _channel_for(relative, mapping)
        if (m := CONFLICT_COPY.match(relative)) and f"{m['base']}.md" in names:
            result.warnings.append(f"동기화 충돌 사본일 수 있어요: {relative}")
        if note is not None and note.modified_at == modified:
            note.channel_id = channel_id
            continue
        try:
            raw, title, tags, body = await asyncio.to_thread(_read_note, path)
        except OSError:
            continue  # e.g. an iCloud placeholder not downloaded yet
        digest = hashlib.sha256(raw).hexdigest()
        if note is None:
            note = NoteRef(
                vault_path=relative,
                title=title,
                tags=tags,
                content_hash="",
                modified_at=modified,
                indexed_at=now,
            )
            session.add(note)
            await session.flush()
        note.channel_id, note.modified_at = channel_id, modified
        if note.content_hash != digest:
            note.title, note.tags, note.content_hash, note.indexed_at = title, tags, digest, now
            await session.execute(text("DELETE FROM note_fts WHERE note_id = :id"), {"id": note.id})
            await session.execute(
                text("INSERT INTO note_fts (note_id, title, body) VALUES (:id, :title, :body)"),
                {"id": note.id, "title": title, "body": body},
            )
            result.changed += 1
    gone = [n for path, n in known.items() if path not in names]
    for note in gone:
        await session.execute(text("DELETE FROM note_fts WHERE note_id = :id"), {"id": note.id})
        await session.delete(note)
    result.removed = len(gone)
    await session.commit()
    if result.changed or result.removed:
        await hub.publish("object.updated", {"object_type": "note_ref", "id": None, "object": {}})
    return result


def _snippet(body: str, query: str, around: int = 30) -> str | None:
    """Text around the first match with the match in [brackets], like FTS snippet()."""
    at = body.lower().find(query.lower())
    if at < 0:
        return None
    start, end = max(0, at - around), min(len(body), at + len(query) + around)
    marked = f"{body[start:at]}[{body[at : at + len(query)]}]{body[at + len(query) : end]}"
    return ("…" if start else "") + " ".join(marked.split()) + ("…" if end < len(body) else "")


def fts_query(query: str) -> str:
    """User text as one FTS5 phrase (quotes escaped), so operators are not interpreted."""
    return '"' + query.replace('"', '""') + '"'


async def search(
    session: AsyncSession, query: str, channel_id: str | None = None, limit: int = 30
) -> list[tuple[NoteRef, str | None]]:
    """Notes matching `query` in title or body, with a snippet around the match. The
    trigram index needs three characters; shorter queries match titles."""
    query = query.strip()
    if len(query) >= 3:
        rows = (
            await session.execute(
                text(
                    "SELECT note_id, snippet(note_fts, 2, '[', ']', '…', 12) FROM note_fts "
                    "WHERE note_fts MATCH :q ORDER BY rank LIMIT :limit"
                ),
                {"q": fts_query(query), "limit": limit * 3 if channel_id else limit},
            )
        ).all()
        snippets = {str(r[0]): str(r[1]) for r in rows}
        notes = (await session.scalars(select(NoteRef).where(NoteRef.id.in_(snippets)))).all()
        order = list(snippets)
        found = sorted(notes, key=lambda n: order.index(n.id))
    else:  # too short for the trigram index (e.g. "과제"): a plain scan is fine here
        pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        rows = (
            await session.execute(
                text(
                    "SELECT note_id, body FROM note_fts WHERE title LIKE :p ESCAPE '\\' "
                    "OR body LIKE :p ESCAPE '\\'"
                ),
                {"p": pattern},
            )
        ).all()
        snippets = {str(r[0]): _snippet(str(r[1]), query) for r in rows}
        found = sorted(
            (await session.scalars(select(NoteRef).where(NoteRef.id.in_(snippets)))).all(),
            key=lambda n: n.modified_at,
            reverse=True,
        )
    if channel_id is not None:
        found = [n for n in found if n.channel_id == channel_id]
    return [(n, snippets.get(n.id)) for n in found[:limit]]


# --- task sync ------------------------------------------------------------------------------


@dataclass
class TaskResult:
    imported: int = 0
    checked_in_argos: int = 0  # boxes ticked in notes because of Argos
    checked_in_note: int = 0  # Argos tasks changed because of notes
    updated: int = 0  # title/due taken from the note
    unlinked: int = 0  # lines that disappeared from their note
    skipped: list[str] = field(default_factory=list[str])  # files that changed mid-edit


def _named_date(relative: str) -> date | None:
    """The date in a daily note's path (the last YYYY-MM-DD in it), if any."""
    found = re.findall(DATE, relative)
    return _date(found[-1]) if found else None


async def _scope(
    session: AsyncSession, vault: Path, settings: Settings, today: date
) -> dict[str, tuple[str, date | None]]:
    """Notes whose tasks come to Argos: under a channel's folder, or recent daily notes
    (→ the #일상 channel). Returns relative path → (channel id, the daily note's date)."""
    mapping = await channel_folders(session)
    daily = settings.vault_daily_folder or await asyncio.to_thread(detect_daily_folder, vault)
    personal = await services.get_personal_channel(session)
    notes = (await session.scalars(select(NoteRef))).all()
    scope: dict[str, tuple[str, date | None]] = {}
    oldest = today - timedelta(days=settings.vault_daily_days)
    for note in notes:
        # Daily notes first: even when #일상 is linked to their folder, only recent ones
        # count and their date is the tasks' due date.
        if daily and personal and note.vault_path.startswith(daily.strip("/") + "/"):
            named = _named_date(note.vault_path)
            if (named or note.modified_at.date()) >= oldest:
                scope[note.vault_path] = (personal.id, named)
        elif (channel_id := _channel_for(note.vault_path, mapping)) is not None:
            scope[note.vault_path] = (channel_id, None)
    return scope


async def sync_tasks(
    session: AsyncSession, vault: Path, settings: Settings, now: datetime, backups: Path
) -> TaskResult:
    result = TaskResult()
    today = now.astimezone(settings.zoneinfo).date()
    scope = await _scope(session, vault, settings, today)
    links = {
        link.external_id: link
        for link in (
            await session.scalars(select(SourceLink).where(SourceLink.source == SOURCE))
        ).all()
    }
    seen: set[str] = set()
    scanned: set[str] = set()
    titles = {n.vault_path: n.title for n in (await session.scalars(select(NoteRef))).all()}

    for relative, (channel_id, day) in sorted(scope.items()):
        path = resolve_inside(vault, relative)
        try:
            original = await asyncio.to_thread(path.read_bytes)
        except OSError:
            continue
        scanned.add(relative)
        edits: dict[int, Callable[[str], str]] = {}
        new_links: list[tuple[NoteTask, str, Task]] = []
        for item in parse_tasks(original.decode("utf-8", errors="replace")):
            if item.due is None:
                item.due = day  # a daily note's task is due on that day
            link = links.get(item.block) if item.block else None
            if link is not None:
                seen.add(link.external_id)
                note_title = titles.get(relative, relative)
                await _follow(session, settings, link, item, relative, note_title, edits, result)
                continue
            if item.block and item.block.startswith("argos-"):
                continue  # linked once, then deleted in Argos: leave it alone
            if item.done or item.mark in SKIP_MARKS:
                continue
            task = await services.create_task(
                session,
                channel_id=channel_id,
                title=item.title[:500],
                actor=ACTOR,
                due_at=_due_at(item.due, item.at, settings),
                priority=item.priority,
                description=_description(item, titles.get(relative, relative)),
            )
            block = item.block or f"argos-{secrets.token_hex(3)}"
            if item.block is None:
                edits[item.line] = lambda line, b=block: with_block(line, b)
            new_links.append((item, block, task))
            result.imported += 1
        if edits:
            try:
                await asyncio.to_thread(edit_lines, vault, relative, original, edits, backups)
            except VaultChanged:
                result.skipped.append(relative)
                # Keep the Argos side as it was; the next pass reads the new file.
                for _, _, task in new_links:
                    await services.delete_task(session, task.id, ACTOR)
                result.imported -= len(new_links)
                await session.rollback()
                continue
        for item, block, task in new_links:
            session.add(
                SourceLink(
                    object_type="task",
                    object_id=task.id,
                    source=SOURCE,
                    external_id=block,
                    container=relative,
                    container_name=titles.get(relative, relative),
                    uid=block,
                    etag="x" if item.done else " ",
                    content_hash=item.digest(),
                    read_only=False,
                    local_updated_at=task.updated_at,
                    last_synced_at=now,
                )
            )
        await session.commit()

    for block, link in links.items():
        if block in seen:
            continue
        # The line is gone from a note we read (or the note is gone): stop tracking it.
        # The Argos task stays; notes out of scope (e.g. old daily notes) are left alone.
        if link.container in scanned or not resolve_inside(vault, link.container).exists():
            await session.delete(link)
            result.unlinked += 1
    await session.commit()
    return result


def _due_at(due: date | None, at: time | None, settings: Settings) -> datetime | None:
    """A note's due date at the time written in the line, else 23:59 that day (like
    dates given to the MCP tools), in the user's zone."""
    if due is None:
        return None
    return datetime.combine(due, at or time(23, 59), settings.zoneinfo).astimezone(UTC)


def _description(item: NoteTask, note_title: str) -> str:
    return "\n".join([f"노트: {note_title}", *item.links])


async def _follow(
    session: AsyncSession,
    settings: Settings,
    link: SourceLink,
    item: NoteTask,
    relative: str,
    title: str,
    edits: dict[int, Callable[[str], str]],
    result: TaskResult,
) -> None:
    """One linked line: whichever side changed its checkbox since the last sync wins
    (the note if both did); title and due date always come from the note."""
    task = await session.get(Task, link.object_id)
    link.container, link.container_name = relative, title
    if task is None:  # deleted in Argos: leave the line, stop tracking
        await session.delete(link)
        return
    was_done = link.etag == "x"
    app_done = task.status == TaskStatus.DONE
    if item.done != was_done:
        target = TaskStatus.DONE if item.done else TaskStatus.TODO
        if app_done != item.done:
            task = await services.update_task(session, task.id, {"status": target}, ACTOR)
            result.checked_in_note += 1
        link.etag = "x" if item.done else " "
    elif app_done != was_done:
        edits[item.line] = lambda line, done=app_done: with_mark(line, done)
        link.etag = "x" if app_done else " "
        result.checked_in_argos += 1
    if item.digest() != link.content_hash:
        changes: dict[str, Any] = {
            "title": item.title[:500],
            "priority": item.priority,
            "due_at": _due_at(item.due, item.at, settings),
            "description": _description(item, title),
        }
        changes = {k: v for k, v in changes.items() if getattr(task, k) != v}
        if changes:
            task = await services.update_task(session, task.id, changes, ACTOR)
            result.updated += 1
        link.content_hash = item.digest()
    link.local_updated_at = task.updated_at


# --- background: index + tasks on start, on file changes, and after Argos edits ------------


@dataclass
class VaultStatus:
    running: bool = False
    last_run_at: datetime | None = None
    last_error: str | None = None
    notes: int = 0
    warnings: list[str] = field(default_factory=list[str])
    last_tasks: dict[str, int] | None = None


class VaultSync:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        settings: Callable[[], Settings],
    ) -> None:
        self._sessionmaker = sessionmaker
        self._settings = settings
        self.status = VaultStatus()
        self.debounce = 1.0
        self._lock = asyncio.Lock()
        self._soon: asyncio.TimerHandle | None = None
        self._tasks: set[asyncio.Task[Any]] = set()
        self._watch: asyncio.Task[None] | None = None
        self._watching: Path | None = None
        self._stop_watch = asyncio.Event()

    def vault(self) -> Path | None:
        path = self._settings().vault_path
        return path.expanduser() if path is not None else None

    async def run(self) -> VaultStatus:
        async with self._lock:
            vault = self.vault()
            if vault is None:
                return self.status
            config = self._settings()
            self.status.running = True
            try:
                if not vault.is_dir():
                    raise services.InvalidError(f"볼트 폴더를 찾을 수 없어요: {vault}")
                now = datetime.now(UTC)
                async with self._sessionmaker() as session:
                    indexed = await index(session, vault, now)
                    tasks = await sync_tasks(session, vault, config, now, config.vault_backup_dir)
                    if tasks.imported or tasks.checked_in_argos:
                        indexed = await index(session, vault, now)  # our own line edits
                self.status.notes = indexed.notes
                self.status.warnings = indexed.warnings + [
                    f"그사이 바뀐 파일이라 다음에 다시 시도해요: {p}" for p in tasks.skipped
                ]
                counts = {k: v for k, v in tasks.__dict__.items() if isinstance(v, int)}
                self.status.last_tasks = counts
                self.status.last_error = None
            except services.InvalidError as exc:
                self.status.last_error = str(exc)
            except Exception:
                log.exception("vault sync failed")
                self.status.last_error = "볼트를 읽는 중 오류가 났어요. 서버 로그를 확인하세요"
            finally:
                self.status.running = False
                self.status.last_run_at = datetime.now(UTC)
            return self.status

    def nudge(self) -> None:
        """Run soon (debounced): a file changed, or a linked task changed in Argos."""
        if self._soon is not None:
            return

        def fire() -> None:
            self._soon = None
            task = asyncio.create_task(self.run())
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

        self._soon = asyncio.get_running_loop().call_later(self.debounce, fire)

    def watch(self) -> None:
        """(Re)starts watching the configured vault for Markdown changes."""
        vault = self.vault()
        if vault == self._watching and self._watch is not None:
            return
        self._cancel_watch()
        self._watching = vault
        if vault is None or not vault.is_dir():
            return
        self._stop_watch = asyncio.Event()

        async def loop() -> None:
            from watchfiles import awatch  # pyright: ignore[reportUnknownVariableType]

            async for changes in awatch(vault, stop_event=self._stop_watch, recursive=True):
                if any(
                    p.endswith(".md")
                    and ".argos-" not in p
                    and "/." not in p.removeprefix(str(vault))
                    for _, p in changes
                ):
                    self.nudge()

        self._watch = asyncio.create_task(loop())

    def _cancel_watch(self) -> None:
        self._stop_watch.set()
        if self._watch is not None:
            self._watch.cancel()
            self._watch = None

    async def stop(self) -> None:
        if self._soon is not None:
            self._soon.cancel()
        self._cancel_watch()
        for task in list(self._tasks):
            task.cancel()


async def note_body(vault: Path, note: NoteRef) -> str:
    path = resolve_inside(vault, note.vault_path)
    content = await asyncio.to_thread(path.read_text, "utf-8", "replace")
    try:
        return frontmatter.loads(content).content
    except Exception:
        return content


async def forget_vault(session: AsyncSession) -> None:
    """Another vault was chosen: drop the old index (links to tasks stay)."""
    await session.execute(text("DELETE FROM note_fts"))
    await session.execute(delete(NoteRef))
    await session.commit()
