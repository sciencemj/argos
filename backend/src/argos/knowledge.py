"""Files agents can search (v0.2 Stage 1.2): uploads in a channel's files folder and the
LMS course materials Argos downloaded. Text is extracted once (PDF, Markdown, plain text
and other text files) and kept in the `knowledge_fts` index next to a `knowledge_doc`
row; `search` is what the `search_knowledge` tool reads.

Channel files live in `argos_workspace/channels/<channel id>/uploads`, inside the native
harness's workspace for that channel, so file tools can read them too. Every path from
outside goes through `resolve_upload`, which refuses anything that would leave the
folder (`..`, absolute paths, symlinks pointing out)."""

import asyncio
import hashlib
import io
import os
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from pypdf import PdfReader
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from argos import attachments
from argos.models import Attachment, KnowledgeDoc, Message, SourceLink
from argos.vault import fts_query

TEXT_LIMIT = 2_000_000  # characters of a file kept in the index
UPLOADS = "uploads"
LMS_SOURCES = ("learningx",)  # SourceLink.source of LMS course materials


@dataclass(frozen=True)
class Hit:
    title: str
    source: str  # upload | lms
    channel_id: str | None
    path: str | None  # uploads: the path inside the channel workspace ("uploads/…")
    snippet: str | None


@dataclass(frozen=True)
class ChannelFile:
    path: str  # inside the uploads folder, "/"-separated
    name: str
    size: int
    modified_at: datetime
    indexed: bool


# --- where channel files live -------------------------------------------------------------


def channel_dir(workspace: Path, channel_id: str) -> Path:
    """The native harness's folder for one channel (its chat runs work here)."""
    return workspace / "channels" / channel_id


def uploads_dir(workspace: Path, channel_id: str) -> Path:
    return channel_dir(workspace, channel_id) / UPLOADS


class PathError(ValueError):
    """A path that would leave the folder it must stay in."""


def resolve_upload(root: Path, relative: str) -> Path:
    """`relative` inside `root`, with symlinks and `..` resolved; raises PathError when
    it would end up outside (or is the folder itself)."""
    cleaned = relative.replace("\\", "/").strip().strip("/")
    if not cleaned or Path(cleaned).is_absolute() or "\x00" in cleaned:
        raise PathError("파일 경로를 확인해 주세요")
    base = root.resolve()
    target = (base / cleaned).resolve()
    if target == base or not target.is_relative_to(base):
        raise PathError("이 채널의 파일 폴더 밖은 다룰 수 없어요")
    return target


def list_files(root: Path, indexed: set[str]) -> list[ChannelFile]:
    """Every regular file under `root` (symlinks skipped), newest first. Blocking."""
    if not root.is_dir():
        return []
    out: list[ChannelFile] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for name in filenames:
            path = Path(dirpath) / name
            if name.startswith(".") or path.is_symlink() or not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            stat = path.stat()
            out.append(
                ChannelFile(
                    rel,
                    name,
                    stat.st_size,
                    datetime.fromtimestamp(stat.st_mtime, UTC),
                    rel in indexed,
                )
            )
    return sorted(out, key=lambda f: f.modified_at, reverse=True)


def save_file(root: Path, folder: str, name: str, source: BinaryIO, limit: int) -> Path:
    """Stores an upload as `<folder>/<name>` inside `root` (an existing name gets " (2)").
    Raises PathError for a folder outside, attachments.TooLarge past `limit`. Blocking."""
    root.mkdir(parents=True, exist_ok=True)
    safe = attachments.safe_name(name)
    parent = resolve_upload(root, f"{folder.strip('/')}/{safe}" if folder.strip("/") else safe)
    target = parent
    stem, suffix = Path(safe).stem, Path(safe).suffix
    n = 2
    while target.exists():
        target = parent.with_name(f"{stem} ({n}){suffix}")
        n += 1
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(f".{target.name}.part")
    size = 0
    try:
        with partial.open("wb") as out:
            while chunk := source.read(1 << 20):
                size += len(chunk)
                if size > limit:
                    raise attachments.TooLarge(limit)
                out.write(chunk)
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)
    return target


# --- text ---------------------------------------------------------------------------------


def extract_text(path: Path, name: str) -> tuple[str, str]:
    """(mime, text) of a file; text "" when it has none Argos can read. Blocking."""
    with path.open("rb") as f:
        head = f.read(4096)
    kind, mime = attachments.sniff(head, name)
    if kind == "pdf":
        try:
            chunks: list[str] = []
            total = 0
            for page in PdfReader(io.BytesIO(path.read_bytes())).pages:
                chunk = page.extract_text() or ""
                chunks.append(chunk)
                total += len(chunk)
                if total > TEXT_LIMIT:
                    break
            return mime, "\n\n".join(chunks).strip()[:TEXT_LIMIT]
        except Exception:  # pypdf raises many kinds of errors on bad files
            return mime, ""
    if kind == "text":
        encoding = attachments.text_encoding(head) or "utf-8"
        with path.open(encoding=encoding, errors="replace") as f:
            return "text/plain", f.read(TEXT_LIMIT)
    return mime, ""


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


# --- the index ----------------------------------------------------------------------------


async def index_file(
    session: AsyncSession,
    *,
    source: str,
    ref: str,
    channel_id: str | None,
    title: str,
    path: Path,
) -> KnowledgeDoc:
    """Adds or refreshes one file (unchanged content: nothing to do). Commits."""
    digest = await asyncio.to_thread(_hash, path)
    doc = await session.scalar(
        select(KnowledgeDoc).where(KnowledgeDoc.source == source, KnowledgeDoc.ref == ref)
    )
    if doc is not None and doc.content_hash == digest and doc.channel_id == channel_id:
        return doc
    mime, body = await asyncio.to_thread(extract_text, path, title)
    size = (await asyncio.to_thread(path.stat)).st_size
    if doc is None:
        doc = KnowledgeDoc(source=source, ref=ref)
        session.add(doc)
    doc.channel_id, doc.title, doc.mime, doc.size = channel_id, title, mime, size
    doc.content_hash, doc.chars = digest, len(body)
    await session.flush()
    await session.execute(text("DELETE FROM knowledge_fts WHERE doc_id = :id"), {"id": doc.id})
    if body:
        await session.execute(
            text("INSERT INTO knowledge_fts (doc_id, title, body) VALUES (:id, :title, :body)"),
            {"id": doc.id, "title": title, "body": body},
        )
    await session.commit()
    return doc


async def remove(session: AsyncSession, source: str, refs: Sequence[str]) -> None:
    if not refs:
        return
    ids = (
        await session.scalars(
            select(KnowledgeDoc.id).where(
                KnowledgeDoc.source == source, KnowledgeDoc.ref.in_(list(refs))
            )
        )
    ).all()
    for doc_id in ids:
        await session.execute(text("DELETE FROM knowledge_fts WHERE doc_id = :id"), {"id": doc_id})
    await session.execute(delete(KnowledgeDoc).where(KnowledgeDoc.id.in_(ids)))
    await session.commit()


async def indexed_uploads(session: AsyncSession, channel_id: str) -> set[str]:
    """Paths (inside the uploads folder) of this channel's files with text in the index."""
    rows = await session.scalars(
        select(KnowledgeDoc.ref).where(
            KnowledgeDoc.source == "upload",
            KnowledgeDoc.channel_id == channel_id,
            KnowledgeDoc.chars > 0,
        )
    )
    return {r.split("/", 1)[1] for r in rows.all() if "/" in r}


async def remove_upload(session: AsyncSession, channel_id: str, relative: str) -> None:
    """Forgets an upload and everything under it when it was a folder."""
    prefix = relative.rstrip("/") + "/"
    refs = [
        r
        for r in (
            await session.scalars(
                select(KnowledgeDoc.ref).where(
                    KnowledgeDoc.source == "upload", KnowledgeDoc.channel_id == channel_id
                )
            )
        ).all()
        if r == upload_ref(channel_id, relative) or r.startswith(upload_ref(channel_id, prefix))
    ]
    await remove(session, "upload", refs)


def upload_ref(channel_id: str, relative: str) -> str:
    return f"{channel_id}/{relative}"


async def index_upload(
    session: AsyncSession, workspace: Path, channel_id: str, path: Path
) -> KnowledgeDoc:
    relative = path.relative_to(uploads_dir(workspace, channel_id).resolve()).as_posix()
    return await index_file(
        session,
        source="upload",
        ref=upload_ref(channel_id, relative),
        channel_id=channel_id,
        title=path.name,
        path=path,
    )


async def index_lms_materials(session: AsyncSession, attachments_dir: Path) -> int:
    """Indexes LMS materials (PDF and text files) not yet in the index or changed since,
    and forgets those gone. Returns how many it (re)indexed."""
    rows = (
        await session.execute(
            select(Attachment, Message.channel_id)
            .join(SourceLink, SourceLink.object_id == Attachment.id)
            .join(Message, Message.id == Attachment.message_id)
            .where(SourceLink.object_type == "attachment", SourceLink.source.in_(LMS_SOURCES))
        )
    ).all()
    known = {
        d.ref: d
        for d in (
            await session.scalars(select(KnowledgeDoc).where(KnowledgeDoc.source == "lms"))
        ).all()
    }
    seen: set[str] = set()
    done = 0
    for attachment, channel_id in rows:
        if attachment.kind not in ("pdf", "text"):
            continue
        path = attachments_dir / attachment.id
        if not path.is_file():
            continue
        seen.add(attachment.id)
        doc = known.get(attachment.id)
        if doc is not None and doc.size == attachment.size and doc.channel_id == channel_id:
            continue  # stored attachments never change in place: a new id replaces them
        await index_file(
            session,
            source="lms",
            ref=attachment.id,
            channel_id=channel_id,
            title=attachment.name,
            path=path,
        )
        done += 1
    await remove(session, "lms", [ref for ref in known if ref not in seen])
    return done


async def channel_docs(session: AsyncSession, channel_id: str) -> list[KnowledgeDoc]:
    """What the index holds for a channel (the workspace panel lists LMS materials)."""
    rows = await session.scalars(
        select(KnowledgeDoc)
        .where(KnowledgeDoc.channel_id == channel_id)
        .order_by(KnowledgeDoc.source, KnowledgeDoc.title)
    )
    return list(rows.all())


async def search(
    session: AsyncSession, query: str, channel_id: str | None = None, limit: int = 8
) -> list[Hit]:
    """Files whose text or name matches `query`, best first, with a snippet. Like note
    search, queries under three characters (the trigram size) scan instead."""
    query = query.strip()
    if not query:
        return []
    if len(query) >= 3:
        rows = (
            await session.execute(
                text(
                    "SELECT doc_id, snippet(knowledge_fts, 2, '[', ']', '…', 16) "
                    "FROM knowledge_fts WHERE knowledge_fts MATCH :q ORDER BY rank LIMIT :n"
                ),
                {"q": fts_query(query), "n": limit * 5 if channel_id else limit},
            )
        ).all()
    else:
        pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        rows = (
            await session.execute(
                text(
                    "SELECT doc_id, substr(body, max(1, instr(body, :q) - 40), 120) "
                    "FROM knowledge_fts WHERE title LIKE :p ESCAPE '\\' "
                    "OR body LIKE :p ESCAPE '\\' LIMIT :n"
                ),
                {"q": query, "p": pattern, "n": limit * 5},
            )
        ).all()
    snippets = {str(r[0]): (str(r[1]) if r[1] is not None else None) for r in rows}
    docs = (
        await session.scalars(select(KnowledgeDoc).where(KnowledgeDoc.id.in_(list(snippets))))
    ).all()
    order = list(snippets)
    found = sorted(docs, key=lambda d: order.index(d.id))
    if channel_id is not None:
        found = [d for d in found if d.channel_id == channel_id]
    return [
        Hit(
            title=d.title,
            source=d.source,
            channel_id=d.channel_id,
            path=f"{UPLOADS}/{d.ref.split('/', 1)[1]}" if d.source == "upload" else None,
            snippet=" ".join((snippets.get(d.id) or "").split()) or None,
        )
        for d in found[:limit]
    ]


def delete_path(path: Path) -> None:
    """A file or a whole folder. Blocking."""
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)
