# Chat Attachments Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Users attach files and images to chat (⌘V paste, Finder drag-and-drop, 📎 picker) in channels, threads, DMs and the quick-capture window, and agents (Claude, Codex, Hermes, Ollama) actually read them.

**Architecture:** Two-step upload: `POST /api/v1/attachments` stores the file under `data_dir/attachments/<id>` and returns an id; `POST …/messages` links ids to the new message. Agents get attachments through `Turn.attachments`; `attachments.py` turns each file into an image part, inline text, or a PDF part, and each adapter maps those to its backend's native input.

**Tech Stack:** FastAPI (UploadFile, python-multipart already installed), SQLAlchemy + Alembic, Pillow, pillow-heif, pypdf, claude-agent-sdk 0.2.159 streaming input, codex-cli 0.159 app-server, React 19 + TanStack Query + openapi-fetch, Tauri v2.

**Spec:** `docs/superpowers/specs/2026-10-08-chat-attachments-design.md`

## Global Constraints

- Per-file limit setting `attachment_max_mb` default 25; at most 10 attachments per message.
- Files live at `settings.attachments_dir / <attachment id>` (`data_dir / "attachments"`); never a user-supplied name in a path.
- Inline display only for `image/png`, `image/jpeg`, `image/gif`, `image/webp`; every content response carries `X-Content-Type-Options: nosniff` and `Content-Security-Policy: sandbox`.
- Agent images: long side ≤ 2000 px and ≤ 3.75 MB raw (Claude's 5 MB limit is on base64); text per file ≤ 100,000 chars; at most 5 images re-sent from older turns of a whole transcript (all images of the newest turn always go).
- Unsent uploads older than 24 h and files without a row are removed at server start.
- All writes through `services.py` helpers (`_create`/`_update`/`_delete`) so activity_log and WS events happen.
- UI copy Korean with English in `frontend/src/en.ts`; code, identifiers, commits English. Colors only from `index.css` tokens.
- Tests never touch real agents or the developer's DB: backend tests use the `settings` fixture; adapters are tested with fakes.
- Python: ruff (line 100, rules E,F,I,UP,B,ASYNC), pyright strict. Blocking file work inside `async def` goes through `asyncio.to_thread`.
- After API model changes: `make api-types` and commit `frontend/src/api-types.ts`.

## Review Focus

1. Copying cells from Numbers/Excel or text from Notes puts both text and a picture on the clipboard — the paste must stay text (Task 7 `pastedFiles` test).
2. Finder file names are NFD-decomposed Korean (`ㅎㅏㄴ…`) — stored and shown names must be NFC (Task 2 `safe_name` test).
3. Sending while an upload is still running, or after one failed — Send stays disabled until every chip is done or removed (Task 7 `canSend` test).
4. A huge or corrupt "image" (decompression bomb, truncated PNG) — upload succeeds as a plain file instead of crashing (Task 2 test).
5. A message with only attachments and no text in a plain channel — stored in the feed, not sent to the inbox classifier, not a 422 (Task 4 test).

---

## File Structure

**Backend (create)**
- `backend/src/argos/attachments.py` — storage (sniff, save, HEIC, sizes, names) and agent parts (resize, text, PDF, image budget, data URLs). No DB access.
- `backend/tests/files.py` — test file builders (`png`, `text_pdf`).
- `backend/tests/test_attachments.py` — storage, API, linking, sweep, parts, runner.
- `backend/alembic/versions/<rev>_attachments.py` — generated.

**Backend (modify)**
- `models.py` — `Attachment` model.
- `config.py` — `attachment_max_mb`, `attachments_dir` property.
- `services.py` — `TooLargeError`, create/get/delete/check/link/list/sweep attachments.
- `api.py` — `AttachmentOut`, upload/content/delete routes, `MessageCreate.attachment_ids`, `MessageOut.attachments`, 413 handler.
- `chat.py` — accept attachments, attachment-only messages.
- `runner.py` — `build_transcript(..., directory)`, job turn attachments.
- `agents.py` — `Turn.attachments`, `split_attachments`/`with_attachments`, adapters.
- `main.py` — sweep at startup.
- `pyproject.toml` / `uv.lock` — pillow, pillow-heif, pypdf.
- `tests/test_agents.py` — adapter request-shape tests.

**Frontend (create)**
- `frontend/src/attachments.tsx` — draft reducer + hook, paste/drop helpers, `DraftChips`, `AttachmentList`, `Lightbox`.
- `frontend/src/attachments.test.tsx`.

**Frontend (modify)**
- `api.ts` — `Attachment` type, `uploadAttachment`, `deleteAttachment`, `attachmentUrl`, `invalidateFor("attachment")`.
- `feed.tsx` — Composer + MessageItem.
- `pages/QuickCapture.tsx`.
- `icons.tsx` — `PaperclipIcon`, `FileIcon`.
- `desktop.ts` — `openAttachment`.
- `en.ts` — strings.
- `api-types.ts` — generated.

**Desktop (modify)**
- `desktop/src-tauri/tauri.conf.json` — `dragDropEnabled: false` on main.
- `desktop/src-tauri/src/lib.rs` — quick window `disable_drag_drop_handler()`, `open_attachment` command.
- `desktop/src-tauri/build.rs`, `capabilities/default.json` — permission.

**Docs (modify)**: `docs/PLAN.md`, `docs/decisions.md`, `docs/backlog.md`, `CLAUDE.md`.

---

### Task 1: Dependencies and backend capability spike

Verifies the three assumptions the spec left open before code depends on them. The spike scripts are throwaway (scratchpad, not committed); only the dependency change and the decisions rows are committed.

**Files:**
- Modify: `backend/pyproject.toml`, `backend/uv.lock`
- Modify: `docs/decisions.md`

**Interfaces:**
- Produces: importable `PIL`, `pillow_heif`, `pypdf`; recorded answers to (a) Claude CLI accepts `document` blocks via SDK streaming input, (b) Codex app-server accepts `{"type":"image","url":"data:…"}`, (c) Claude CLI expands `/skill` in a streamed text block (informational).

- [ ] **Step 1: Add dependencies**

```bash
cd backend && uv add pillow pillow-heif pypdf
uv run python -c "import PIL, pillow_heif, pypdf; print(PIL.__version__, pillow_heif.__version__, pypdf.__version__)"
```
Expected: three version numbers.

- [ ] **Step 2: Spike Claude streaming input with an image and a PDF**

Write `$SCRATCH/spike_claude.py` (`$SCRATCH` = the session scratchpad dir). It builds a red 64×64 PNG with Pillow and a one-page PDF containing `ARGOS-PDF-42` (copy `text_pdf` from Task 2 Step 1 into the script), then:

```python
import asyncio, base64, io, shutil
from claude_agent_sdk import ClaudeAgentOptions, query, AssistantMessage, TextBlock

async def main() -> None:
    png = ...; pdf = ...
    async def prompt():
        yield {"type": "user", "parent_tool_use_id": None, "message": {"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": base64.b64encode(png).decode()}},
            {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": base64.b64encode(pdf).decode()}, "title": "t.pdf"},
            {"type": "text", "text": "What color is the image, and what code is in the PDF? One line."},
        ]}}
    options = ClaudeAgentOptions(tools=[], model="haiku", setting_sources=[], cli_path=shutil.which("claude"))
    async for m in query(prompt=prompt(), options=options):
        if isinstance(m, AssistantMessage):
            print("".join(b.text for b in m.content if isinstance(b, TextBlock)))

asyncio.run(main())
```
Run: `cd backend && uv run python -I $SCRATCH/spike_claude.py`
Expected: a line naming red and `ARGOS-PDF-42`. If the CLI rejects the document block, record "PDF → text for Claude too" and in Task 6 pass `pdf_native=False` for Claude.

- [ ] **Step 3: Spike Codex app-server data-URL image**

Write `$SCRATCH/spike_codex.py`: start `codex app-server` (`asyncio.create_subprocess_exec`, stdin/stdout pipes), send `initialize`, `initialized`, `thread/start` with `{"cwd": "<empty tmp dir>", "sandbox": "read-only", "approvalPolicy": "never", "ephemeral": True}`, then `turn/start` with `input: [{"type":"image","url":"data:image/png;base64,<red png>"}, {"type":"text","text":"What color is this image? One word."}]`; print `item/agentMessage/delta` deltas until `turn/completed`; refuse any server request by replying an error (same as `CodexAppServerAdapter.refuse`).
Run: `cd backend && uv run python -I $SCRATCH/spike_codex.py`
Expected: "Red". If rejected, Task 6 uses `{"type":"localImage","path":…}` with files written to a per-turn temp dir (named `<n>.<ext>`) instead.

- [ ] **Step 4: Record results**

Append rows to `docs/decisions.md` (table format `| 날짜 | 결정 | 이유 |`):

```markdown
| 2026-10-08 | 첨부 파일: 2단계 업로드(`POST /attachments` → 메시지에 `attachment_ids`), 파일은 `data_dir/attachments/<id>`, 에이전트에는 어댑터별 네이티브 입력(Claude content 블록, Codex 이미지 입력, Hermes `input_image`, Ollama `image_url`) | 사용자 결정(설계 `docs/superpowers/specs/2026-10-08-chat-attachments-design.md`). 붙이는 동안 업로드되고, MCP 도구 방식은 백엔드마다 지원이 달라서 제외 |
| 2026-10-08 | Pillow·pillow-heif·pypdf 추가 | 큰 이미지 축소와 아이폰 HEIC 변환, Claude 외 에이전트용 PDF 텍스트 추출 |
| 2026-10-08 | 첨부 저장 위치는 설정이 아니라 `data_dir/attachments` 고정 | 데스크톱 앱이 파일을 열 때 같은 경로를 알아야 함(Tauri `open_attachment`). 설계의 `attachments_dir` 설정 대신 |
| 2026-10-08 | <spike 결과: Claude PDF 문서 블록 지원 여부, Codex data URL 이미지 지원 여부> | 실측(claude-agent-sdk 0.2.159, codex-cli 0.159) |
```
Replace the last row's placeholder with the observed outcomes before committing.

- [ ] **Step 5: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock docs/decisions.md
git commit -m "build: add pillow, pillow-heif and pypdf for chat attachments"
```

---

### Task 2: Attachment model, storage module, migration

**Files:**
- Create: `backend/src/argos/attachments.py`
- Create: `backend/tests/files.py`
- Create: `backend/tests/test_attachments.py`
- Modify: `backend/src/argos/models.py` (after `Message`, ~line 201)
- Modify: `backend/src/argos/config.py` (fields + property)
- Create: `backend/alembic/versions/<rev>_attachments.py` (autogenerated)

**Interfaces:**
- Produces:
  - `models.Attachment(Record)`: `message_id: str | None`, `name: str`, `mime: str`, `size: int`, `kind: str` (`"image" | "text" | "pdf" | "file"`), `width: int | None`, `height: int | None`.
  - `Settings.attachment_max_mb: int = 25`; `Settings.attachments_dir -> Path` (property, `data_dir / "attachments"`).
  - `attachments.MAX_FILES = 10`, `attachments.INLINE_MIMES: frozenset[str]`.
  - `attachments.TooLarge(Exception)` with `.limit: int`.
  - `attachments.Stored` dataclass: `name, mime, size, kind, width, height`.
  - `attachments.safe_name(name: str) -> str`, `attachments.sniff(head: bytes, name: str) -> tuple[str, str]`, `attachments.save_upload(source: BinaryIO, name: str, path: Path, limit: int) -> Stored`.

- [ ] **Step 1: Write test file builders**

`backend/tests/files.py`:

```python
"""Small files built in code so the repository holds no binary fixtures."""

import io

from PIL import Image


def png(width: int = 8, height: int = 6, color: str = "red") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, "PNG")
    return buffer.getvalue()


def text_pdf(text: str) -> bytes:
    """A one-page PDF showing `text` in Helvetica (enough for pypdf to extract)."""
    stream = f"BT /F1 24 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return bytes(out)
```

- [ ] **Step 2: Write the failing storage tests**

`backend/tests/test_attachments.py`:

```python
import io
import unicodedata
from pathlib import Path

import pytest
from files import png, text_pdf
from PIL import Image

from argos import attachments


def save(tmp_path: Path, data: bytes, name: str, limit: int = 10_000_000) -> attachments.Stored:
    return attachments.save_upload(io.BytesIO(data), name, tmp_path / "a1", limit)


def test_png_is_an_image_with_its_size(tmp_path: Path) -> None:
    stored = save(tmp_path, png(40, 30), "shot.png")
    assert (stored.kind, stored.mime, stored.width, stored.height) == ("image", "image/png", 40, 30)
    assert (tmp_path / "a1").read_bytes() == png(40, 30)


def test_kind_comes_from_content_not_extension(tmp_path: Path) -> None:
    assert save(tmp_path, png(), "notes.txt").kind == "image"
    assert save(tmp_path, b"<svg xmlns='http://www.w3.org/2000/svg'/>", "x.png").kind == "file"
    assert save(tmp_path, text_pdf("hi"), "paper").kind == "pdf"


def test_text_needs_text_extension_and_text_bytes(tmp_path: Path) -> None:
    text = save(tmp_path, "print('안녕')\n".encode(), "main.py")
    assert (text.kind, text.mime) == ("text", "text/plain")
    assert save(tmp_path, b"\x00\x01binary", "data.json").kind == "file"
    assert save(tmp_path, b"hello", "archive.zip").kind == "file"


def test_utf8_cut_mid_character_is_still_text(tmp_path: Path) -> None:
    data = ("가" * 300).encode()  # 900 bytes: the 512-byte head ends inside a character
    assert save(tmp_path, data, "a.md").kind == "text"


def test_names_are_cleaned_and_nfc(tmp_path: Path) -> None:
    nfd = unicodedata.normalize("NFD", "한글 노트.pdf")
    assert attachments.safe_name(nfd) == "한글 노트.pdf"
    assert attachments.safe_name("../../etc/passwd") == "passwd"
    assert attachments.safe_name("C:\\Users\\me\\a.txt") == "a.txt"
    assert attachments.safe_name("bad\x00\nname.txt") == "badname.txt"
    assert attachments.safe_name("...") == "file"
    assert len(attachments.safe_name("a" * 500 + ".txt")) <= 200


def test_too_large_leaves_nothing(tmp_path: Path) -> None:
    with pytest.raises(attachments.TooLarge):
        save(tmp_path, b"x" * 2_000, "big.txt", limit=1_000)
    assert not (tmp_path / "a1").exists()


def test_corrupt_image_becomes_a_plain_file(tmp_path: Path) -> None:
    stored = save(tmp_path, png()[:40], "broken.png")  # PNG signature, truncated body
    assert (stored.kind, stored.width) == ("file", None)


def test_heic_is_stored_as_jpeg(tmp_path: Path) -> None:
    pillow_heif = pytest.importorskip("pillow_heif")
    buffer = io.BytesIO()
    try:
        pillow_heif.from_pillow(Image.new("RGB", (10, 8), "blue")).save(buffer, format="HEIF")
    except Exception as exc:  # wheels without an encoder cannot make test data
        pytest.skip(f"no HEIF encoder: {exc}")
    stored = save(tmp_path, buffer.getvalue(), "IMG_0001.HEIC")
    assert (stored.kind, stored.mime, stored.name) == ("image", "image/jpeg", "IMG_0001.jpg")
    assert (stored.width, stored.height) == (10, 8)
    assert (tmp_path / "a1").read_bytes()[:3] == b"\xff\xd8\xff"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_attachments.py -q`
Expected: FAIL — `ImportError: cannot import name 'attachments' from 'argos'`.

- [ ] **Step 4: Implement the storage half of `attachments.py`**

```python
"""Files attached to chat messages (PLAN Phase 13). Stored by id under
data_dir/attachments (never by their own name); the agent half below turns them into
what each backend can read."""

import mimetypes
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from PIL import Image
from pillow_heif import register_heif_opener

register_heif_opener()  # Pillow opens HEIC/HEIF photos from now on

MAX_FILES = 10  # per message
INLINE_MIMES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})
TEXT_EXTENSIONS = frozenset(
    ".txt .md .markdown .csv .tsv .json .jsonl .yaml .yml .toml .xml .html .css .js .mjs "
    ".ts .tsx .jsx .py .rs .go .java .kt .swift .c .h .cc .cpp .hpp .cs .rb .php .sh .zsh "
    ".sql .tex .bib .log .ini .cfg .conf .r .m .lua .dart .vue .svelte".split()
)
_HEAD = 512
_HEIF_BRANDS = (b"heic", b"heix", b"heim", b"heis", b"hevc", b"mif1", b"msf1", b"heif")


class TooLarge(Exception):
    def __init__(self, limit: int) -> None:
        super().__init__(f"{limit} bytes")
        self.limit = limit


@dataclass(frozen=True)
class Stored:
    name: str
    mime: str
    size: int
    kind: str  # image | text | pdf | file
    width: int | None = None
    height: int | None = None


def safe_name(name: str) -> str:
    """The original file name for display and downloads: last path part, NFC (Finder
    hands over decomposed Korean), no control characters, at most 200 characters."""
    base = unicodedata.normalize("NFC", name.replace("\\", "/").rsplit("/", 1)[-1])
    base = "".join(ch for ch in base if ch.isprintable()).strip().strip(".")
    if len(base) > 200:
        stem, dot, suffix = base.rpartition(".")
        base = f"{stem[: 199 - len(suffix)]}.{suffix}" if dot and len(suffix) < 20 else base[:200]
    return base or "file"


def _is_text(head: bytes) -> bool:
    if b"\x00" in head:
        return False
    try:
        head.decode("utf-8")
    except UnicodeDecodeError as exc:
        return exc.start >= len(head) - 3  # the head ended inside a character
    return True


def sniff(head: bytes, name: str) -> tuple[str, str]:
    """(kind, mime) from the first bytes; the extension only decides text files.
    "heic" is a temporary kind: such photos are converted to JPEG on upload."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image", "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image", "image/jpeg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image", "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image", "image/webp"
    if head[4:8] == b"ftyp" and head[8:12] in _HEIF_BRANDS:
        return "heic", "image/heic"
    if head.startswith(b"%PDF-"):
        return "pdf", "application/pdf"
    if Path(name).suffix.lower() in TEXT_EXTENSIONS and _is_text(head):
        return "text", "text/plain"
    return "file", mimetypes.guess_type(name)[0] or "application/octet-stream"


def _image_size(path: Path) -> tuple[int, int] | None:
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            image.load()  # truncated files fail here, not later in the feed
            return image.size
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        return None


def _heic_to_jpeg(path: Path) -> None:
    with Image.open(path) as image:
        rgb = image.convert("RGB")
    rgb.save(path, "JPEG", quality=90)


def save_upload(source: BinaryIO, name: str, path: Path, limit: int) -> Stored:
    """Copies an upload to `path` and describes it. Past `limit` bytes it raises TooLarge
    and leaves nothing behind. Blocking: call through asyncio.to_thread."""
    path.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    head = b""
    try:
        with path.open("wb") as out:
            while chunk := source.read(1 << 20):
                size += len(chunk)
                if size > limit:
                    raise TooLarge(limit)
                if len(head) < _HEAD:
                    head += chunk[: _HEAD - len(head)]
                out.write(chunk)
        name = safe_name(name)
        kind, mime = sniff(head, name)
        if kind == "heic":
            try:
                _heic_to_jpeg(path)
                name, kind, mime = str(Path(name).with_suffix(".jpg")), "image", "image/jpeg"
                size = path.stat().st_size
            except (OSError, ValueError):
                kind, mime = "file", "image/heic"
        if kind == "image":
            dimensions = _image_size(path)
            if dimensions is None:
                return Stored(name, "application/octet-stream", size, "file")
            return Stored(name, mime, size, kind, *dimensions)
        return Stored(name, mime, size, kind)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
```

- [ ] **Step 5: Run storage tests**

Run: `cd backend && uv run pytest tests/test_attachments.py -q`
Expected: PASS (HEIC test may SKIP).

- [ ] **Step 6: Add the model and settings**

`models.py`, after `class Message`:

```python
class Attachment(Record):
    """A file attached to a chat message (PLAN Phase 13). Stored at
    data_dir/attachments/<id>; `message_id` stays empty between upload and send."""

    __tablename__ = "attachment"

    message_id: Mapped[str | None] = mapped_column(
        ForeignKey("message.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(255))
    mime: Mapped[str] = mapped_column(String(100))
    size: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(10))  # image | text | pdf | file
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
```
Add `Integer` to the `sqlalchemy` import if missing.

`config.py`, next to `dm_session_idle_hours`:

```python
    attachment_max_mb: int = 25  # per file attached to a chat message
```
and below `mcp_url` (a property next to it):

```python
    @property
    def attachments_dir(self) -> Path:
        """Chat attachments by id. Fixed inside data_dir: the desktop app opens them there."""
        return self.data_dir / "attachments"
```

- [ ] **Step 7: Generate and check the migration**

```bash
cd backend && uv run alembic revision --autogenerate -m "attachments" && uv run alembic check
```
Expected: new file under `alembic/versions/` creating table `attachment` with index `ix_attachment_message_id`; `alembic check` prints "No new upgrade operations detected." Open the file and confirm the FK has `ondelete="CASCADE"`.

- [ ] **Step 8: Commit**

```bash
git add backend/src/argos/attachments.py backend/src/argos/models.py backend/src/argos/config.py backend/alembic/versions backend/tests/files.py backend/tests/test_attachments.py
git commit -m "feat: store chat attachments by id with content-based kinds"
```

---

### Task 3: Upload, content and delete API with startup sweep

**Files:**
- Modify: `backend/src/argos/services.py` (errors near line 56; new section "attachments" after messages, ~line 906)
- Modify: `backend/src/argos/api.py` (models near `MessageOut` ~line 403; routes after message routes ~line 860; error handler ~line 2607)
- Modify: `backend/src/argos/main.py` (lifespan, after `abandon_running_runs`)
- Test: `backend/tests/test_attachments.py`

**Interfaces:**
- Consumes: `attachments.save_upload`, `attachments.TooLarge`, `attachments.INLINE_MIMES`, `models.Attachment`, `Settings.attachments_dir`, `Settings.attachment_max_mb`.
- Produces:
  - `services.TooLargeError(Exception)` → HTTP 413 code `too_large`.
  - `services.create_attachment(session, source: BinaryIO, name: str, directory: Path, limit: int, actor: str) -> Attachment`
  - `services.get_attachment(session, attachment_id: str) -> Attachment`
  - `services.delete_attachment(session, attachment_id: str, directory: Path, actor: str) -> None` (409 if sent)
  - `services.sweep_attachments(session, directory: Path, now: datetime) -> None`
  - `api.AttachmentOut(Out)`: `id, name, mime, size, kind: Literal["image","text","pdf","file"], width, height, missing: bool = False`
  - `api._attachments_out(items: Sequence[Attachment], directory: Path) -> list[AttachmentOut]`
  - Routes: `POST /api/v1/attachments` (201), `GET /api/v1/attachments/{id}/content`, `DELETE /api/v1/attachments/{id}` (204).

- [ ] **Step 1: Write the failing API tests** (append to `tests/test_attachments.py`; here and in later tasks, move the new imports to the top of the file so ruff's import rules pass)

```python
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from argos import services
from argos.config import Settings
from argos.main import create_app
from argos.models import Attachment


@pytest.fixture
def api(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings.model_copy(update={"attachment_max_mb": 1}))) as client:
        yield client


def upload(client: TestClient, data: bytes, name: str) -> dict[str, Any]:
    response = client.post("/api/v1/attachments", files={"file": (name, data)})
    assert response.status_code == 201, response.text
    return response.json()


def test_upload_and_serve_image_inline(api: TestClient) -> None:
    item = upload(api, png(4, 3), "a.png")
    assert {k: item[k] for k in ("name", "kind", "width", "height", "missing")} == {
        "name": "a.png", "kind": "image", "width": 4, "height": 3, "missing": False,
    }  # fmt: skip
    content = api.get(f"/api/v1/attachments/{item['id']}/content")
    assert content.content == png(4, 3)
    assert content.headers["content-type"] == "image/png"
    assert content.headers["content-disposition"].startswith("inline")
    assert content.headers["x-content-type-options"] == "nosniff"
    assert content.headers["content-security-policy"] == "sandbox"


def test_svg_and_html_download_instead_of_rendering(api: TestClient) -> None:
    for name, data in (("x.svg", b"<svg onload='alert(1)'/>"), ("x.html", b"<script>1</script>")):
        item = upload(api, data, name)
        content = api.get(f"/api/v1/attachments/{item['id']}/content")
        assert content.headers["content-disposition"].startswith("attachment")
        assert content.headers["content-security-policy"] == "sandbox"


def test_korean_name_survives_download(api: TestClient) -> None:
    item = upload(api, b"hello", "강의 노트.md")
    disposition = api.get(f"/api/v1/attachments/{item['id']}/content").headers[
        "content-disposition"
    ]
    assert "filename*=utf-8''%EA%B0%95" in disposition


def test_over_limit_is_413(api: TestClient) -> None:
    response = api.post("/api/v1/attachments", files={"file": ("big.bin", b"x" * 1_100_000)})
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "too_large"
    assert "1MB" in response.json()["error"]["message"]


def test_delete_unsent_upload_removes_file(api: TestClient, settings: Settings) -> None:
    item = upload(api, b"hello", "a.txt")
    assert api.delete(f"/api/v1/attachments/{item['id']}").status_code == 204
    assert not (settings.attachments_dir / item["id"]).exists()
    assert api.get(f"/api/v1/attachments/{item['id']}/content").status_code == 404


def test_missing_file_is_404(api: TestClient, settings: Settings) -> None:
    item = upload(api, b"hello", "a.txt")
    (settings.attachments_dir / item["id"]).unlink()
    assert api.get(f"/api/v1/attachments/{item['id']}/content").status_code == 404


async def test_sweep_removes_stale_uploads_and_stray_files(
    session: AsyncSession, settings: Settings
) -> None:
    directory = settings.attachments_dir
    directory.mkdir(parents=True)
    now = datetime.now(UTC)
    old = Attachment(name="old.txt", mime="text/plain", size=1, kind="text",
                     created_at=now - timedelta(hours=25))  # fmt: skip
    fresh = Attachment(name="new.txt", mime="text/plain", size=1, kind="text", created_at=now)
    session.add_all([old, fresh])
    await session.commit()
    for attachment in (old, fresh):
        (directory / attachment.id).write_text("x")
    (directory / "stray").write_text("x")

    await services.sweep_attachments(session, directory, now)

    left = (await session.scalars(select(Attachment.id))).all()
    assert left == [fresh.id]
    assert sorted(p.name for p in directory.iterdir()) == [fresh.id]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_attachments.py -q`
Expected: new tests FAIL with 404 (route missing) / `AttributeError: module 'argos.services' has no attribute 'sweep_attachments'`.

- [ ] **Step 3: Implement services**

Near the other errors (~line 66):

```python
class TooLargeError(Exception):
    pass
```

New section after `create_message`:

```python
# --- attachments (PLAN Phase 13) -----------------------------------------------------


async def create_attachment(
    session: AsyncSession, source: BinaryIO, name: str, directory: Path, limit: int, actor: str
) -> Attachment:
    attachment_id = new_id()
    try:
        stored = await asyncio.to_thread(
            attachments.save_upload, source, name, directory / attachment_id, limit
        )
    except attachments.TooLarge as exc:
        raise TooLargeError(f"{exc.limit // (1024 * 1024)}MB까지 올릴 수 있어요") from exc
    return await _create(session, Attachment(id=attachment_id, **asdict(stored)), actor)


async def get_attachment(session: AsyncSession, attachment_id: str) -> Attachment:
    return await _get(session, Attachment, attachment_id)


async def delete_attachment(
    session: AsyncSession, attachment_id: str, directory: Path, actor: str
) -> None:
    """Only an upload that was not sent yet (the composer's ✕)."""
    attachment = await get_attachment(session, attachment_id)
    if attachment.message_id is not None:
        raise ConflictError("이미 보낸 첨부는 지울 수 없어요")
    await _delete(session, attachment, actor)
    await asyncio.to_thread((directory / attachment_id).unlink, missing_ok=True)


def _remove_files_except(directory: Path, keep: set[str]) -> None:
    if directory.is_dir():
        for path in directory.iterdir():
            if path.is_file() and path.name not in keep:
                path.unlink(missing_ok=True)


async def sweep_attachments(session: AsyncSession, directory: Path, now: datetime) -> None:
    """At start: uploads never sent within a day, and files whose row is gone (a deleted
    channel's messages take their attachment rows with them)."""
    stale = (
        await session.scalars(
            select(Attachment).where(
                Attachment.message_id.is_(None), Attachment.created_at < now - timedelta(days=1)
            )
        )
    ).all()
    for attachment in stale:
        await _delete(session, attachment, "system")
    keep = set((await session.scalars(select(Attachment.id))).all())
    await asyncio.to_thread(_remove_files_except, directory, keep)
```
Imports to add at the top of `services.py` if missing: `asyncio`, `from dataclasses import asdict`, `from pathlib import Path`, `from typing import BinaryIO`, `from datetime import timedelta`, `from argos import attachments`, `Attachment` from `argos.models`, and `new_id` from `argos.models`.

- [ ] **Step 4: Implement API**

Models, next to `MessageOut`:

```python
class AttachmentOut(Out):
    id: str
    name: str
    mime: str
    size: int
    kind: Literal["image", "text", "pdf", "file"]
    width: int | None
    height: int | None
    missing: bool = False  # the file is gone (e.g. restored from a database-only backup)


def _attachments_out(items: Sequence[Attachment], directory: Path) -> list[AttachmentOut]:
    out: list[AttachmentOut] = []
    for attachment in items:
        item = AttachmentOut.model_validate(attachment)
        item.missing = not (directory / attachment.id).is_file()
        out.append(item)
    return out
```

Routes, after the message routes:

```python
ATTACHMENT_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    # Even if a browser renders it, an uploaded SVG/HTML runs no script with the API's origin.
    "Content-Security-Policy": "sandbox",
    "Cache-Control": "private, max-age=31536000, immutable",  # an id's file never changes
}


@router.post("/attachments", status_code=status.HTTP_201_CREATED)
async def upload_attachment(session: Session, config: Config, file: UploadFile) -> AttachmentOut:
    attachment = await services.create_attachment(
        session,
        file.file,
        file.filename or "file",
        config.attachments_dir,
        config.attachment_max_mb * 1024 * 1024,
        USER,
    )
    [out] = _attachments_out([attachment], config.attachments_dir)
    return out


@router.get("/attachments/{attachment_id}/content", response_class=FileResponse)
async def attachment_content(session: Session, config: Config, attachment_id: str) -> FileResponse:
    attachment = await services.get_attachment(session, attachment_id)
    path = config.attachments_dir / attachment.id
    if not await asyncio.to_thread(path.is_file):
        raise services.NotFoundError("attachment file", attachment.id)
    inline = attachment.mime in attachments.INLINE_MIMES
    return FileResponse(
        path,
        media_type=attachment.mime,
        filename=attachment.name,
        content_disposition_type="inline" if inline else "attachment",
        headers=ATTACHMENT_HEADERS,
    )


@router.delete("/attachments/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_attachment(session: Session, config: Config, attachment_id: str) -> None:
    await services.delete_attachment(session, attachment_id, config.attachments_dir, USER)
```
Add `UploadFile` to the `fastapi` import, `attachments` to the `from argos import (...)` list, `Attachment` to the models import.

Error handler in `install_error_handlers`:

```python
    @app.exception_handler(services.TooLargeError)
    async def too_large(_: Request, exc: services.TooLargeError) -> JSONResponse:
        return _error(413, "too_large", str(exc))
```

- [ ] **Step 5: Sweep at startup** — `main.py`, right after `abandon_running_runs`:

```python
        async with app.state.sessionmaker() as session:
            await services.sweep_attachments(session, config.attachments_dir, datetime.now(UTC))
```
Add `from datetime import UTC, datetime`.

- [ ] **Step 6: Run tests**

Run: `cd backend && uv run pytest tests/test_attachments.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/src/argos/services.py backend/src/argos/api.py backend/src/argos/main.py backend/tests/test_attachments.py
git commit -m "feat: attachment upload, download and cleanup endpoints"
```

---

### Task 4: Send messages with attachments

**Files:**
- Modify: `backend/src/argos/services.py` (attachments section)
- Modify: `backend/src/argos/chat.py:70-260`
- Modify: `backend/src/argos/api.py` (`MessageCreate` ~line 499, `MessageOut` ~line 403, `post_message` ~line 801, `_messages_out` ~line 682)
- Test: `backend/tests/test_attachments.py`

**Interfaces:**
- Consumes: `services.get_attachment`, `_attachments_out`.
- Produces:
  - `services.check_attachments(session, ids: Sequence[str]) -> list[Attachment]` (422 >10 or duplicates, 404 unknown, 409 already sent)
  - `services.link_attachments(session, message: Message, items: Sequence[Attachment], actor: str) -> None`
  - `services.attachments_for(session, message_ids: Sequence[str]) -> dict[str, list[Attachment]]` (each list in upload order)
  - `chat.post_message(..., attachment_ids: Sequence[str] = ())`
  - `MessageCreate.attachment_ids: list[str]`, `MessageCreate.body` default `""`.
  - `MessageOut.attachments: list[AttachmentOut]`.

- [ ] **Step 1: Write the failing tests** (append; reuses `api` fixture and `upload`)

```python
def channel_id(client: TestClient, name: str) -> str:
    channels = client.get("/api/v1/channels").json()["channels"]
    return next(c["id"] for c in channels if c["name"] == name)


def post(client: TestClient, channel: str, body: str, ids: list[str]) -> Any:
    return client.post(
        f"/api/v1/channels/{channel}/messages", json={"body": body, "attachment_ids": ids}
    )


def test_message_carries_its_attachments(api: TestClient) -> None:
    course = channel_id(api, "컴퓨터구조")
    first, second = upload(api, png(), "1.png"), upload(api, b"x", "2.txt")
    sent = post(api, course, "자료", [first["id"], second["id"]])
    assert sent.status_code == 201, sent.text
    assert [a["name"] for a in sent.json()["attachments"]] == ["1.png", "2.txt"]
    feed = api.get(f"/api/v1/channels/{course}/messages").json()["items"]
    assert [a["id"] for a in feed[-1]["attachments"]] == [first["id"], second["id"]]


def test_attachment_only_message_skips_the_inbox(api: TestClient) -> None:
    course = channel_id(api, "컴퓨터구조")
    sent = post(api, course, "", [upload(api, png(), "board.png")["id"]])
    assert sent.status_code == 201, sent.text
    assert sent.json()["body"] == "" and sent.json()["ref_type"] is None
    assert api.get("/api/v1/inbox").json()["items"] == []


def test_empty_message_without_attachments_is_422(api: TestClient) -> None:
    assert post(api, channel_id(api, "컴퓨터구조"), "  ", []).status_code == 422


def test_attachment_cannot_be_sent_twice_or_deleted_after(api: TestClient) -> None:
    course = channel_id(api, "컴퓨터구조")
    item = upload(api, b"x", "a.txt")
    assert post(api, course, "one", [item["id"]]).status_code == 201
    assert post(api, course, "two", [item["id"]]).status_code == 409
    assert api.delete(f"/api/v1/attachments/{item['id']}").status_code == 409


def test_unknown_duplicate_and_too_many_ids(api: TestClient) -> None:
    course = channel_id(api, "컴퓨터구조")
    assert post(api, course, "x", ["nope"]).status_code == 404
    item = upload(api, b"x", "a.txt")
    assert post(api, course, "x", [item["id"], item["id"]]).status_code == 422
    many = [upload(api, b"x", f"{i}.txt")["id"] for i in range(11)]
    assert post(api, course, "x", many).status_code == 422
    # Nothing was stored by the failed sends.
    assert all(m["body"] != "x" for m in api.get(f"/api/v1/channels/{course}/messages").json()["items"])


def test_deleting_the_channel_drops_attachment_rows(api: TestClient) -> None:
    created = api.post("/api/v1/channels", json={"name": "임시", "kind": "project"})
    assert created.status_code == 201, created.text
    channel = created.json()["id"]
    item = upload(api, b"x", "a.txt")
    assert post(api, channel, "x", [item["id"]]).status_code == 201
    assert api.delete(f"/api/v1/channels/{channel}?force=true").status_code == 204
    assert api.get(f"/api/v1/attachments/{item['id']}/content").status_code == 404
```
If `POST /api/v1/channels` needs other fields (e.g. `area_id`), copy the body from an existing channel-creation test in `tests/test_api.py`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_attachments.py -q -k "message or attachment_only or empty or twice or unknown or channel"`
Expected: FAIL (`attachments` key missing; empty body 422 from `min_length`).

- [ ] **Step 3: Services**

```python
async def check_attachments(session: AsyncSession, ids: Sequence[str]) -> list[Attachment]:
    """Uploads that may go with a new message, in the order given. Runs before the
    message is stored so a bad id stores nothing."""
    if len(ids) > attachments.MAX_FILES:
        raise InvalidError(f"첨부는 {attachments.MAX_FILES}개까지 보낼 수 있어요")
    if len(set(ids)) != len(ids):
        raise InvalidError("같은 첨부가 두 번 들어 있어요")
    found = {
        a.id: a
        for a in (await session.scalars(select(Attachment).where(Attachment.id.in_(ids)))).all()
    }
    for attachment_id in ids:
        attachment = found.get(attachment_id)
        if attachment is None:
            raise NotFoundError("attachment", attachment_id)
        if attachment.message_id is not None:
            raise ConflictError("이미 보낸 첨부예요")
    return [found[i] for i in ids]


async def link_attachments(
    session: AsyncSession, message: Message, items: Sequence[Attachment], actor: str
) -> None:
    for attachment in items:
        await _update(session, attachment, {"message_id": message.id}, actor)


async def attachments_for(
    session: AsyncSession, message_ids: Sequence[str]
) -> dict[str, list[Attachment]]:
    if not message_ids:
        return {}
    rows = await session.scalars(
        select(Attachment)
        .where(Attachment.message_id.in_(message_ids))
        .order_by(Attachment.created_at, Attachment.id)
    )
    grouped: dict[str, list[Attachment]] = {}
    for attachment in rows.all():
        grouped.setdefault(attachment.message_id or "", []).append(attachment)
    return grouped
```

- [ ] **Step 4: chat.post_message**

Rename the existing `post_message` to `_route` (same parameters, minus the body check at the top), and add a new `post_message` above it:

```python
async def post_message(
    session: AsyncSession,
    *,
    channel_id: str,
    body: str,
    now: datetime,
    settings: Settings,
    thread_root_id: str | None = None,
    session_id: str | None = None,
    coding: bool | None = None,
    attachment_ids: Sequence[str] = (),
) -> Posted:
    """Stores the message and decides what happens next (see runner.py for routing):
    agents to answer, or an inbox item to classify, or neither. Attachments are checked
    first and linked before any agent starts, so the agent's transcript has them."""
    body = body.strip()
    files = await services.check_attachments(session, attachment_ids)
    if not body and not files:
        raise services.InvalidError("빈 메시지는 보낼 수 없어요")
    posted = await _route(
        session,
        channel_id=channel_id,
        body=body,
        now=now,
        settings=settings,
        thread_root_id=thread_root_id,
        session_id=session_id,
        coding=coding,
    )
    if files:
        await services.link_attachments(session, posted.message, files, "user")
    return posted
```
In `_route`, delete the old `body = body.strip()` / empty check lines, and at the top of `case None:` add:

```python
        case None if not body:  # only attachments: nothing for the classifier to read
            return Posted(await _say(session, channel, body, None, in_thread))
        case None:
```
Add `from collections.abc import Sequence`.

- [ ] **Step 5: API models and route**

`MessageCreate`:

```python
class MessageCreate(BaseModel):
    # May be empty when attachments go with it (chat.post_message checks).
    body: str = Field(default="", max_length=10_000)
    thread_root_id: str | None = None
    session_id: str | None = None
    coding: bool | None = None
    attachment_ids: list[str] = Field(default_factory=list[str], max_length=10)
```
Keep the existing comments on `session_id` and `coding`.

`MessageOut`: add `attachments: list[AttachmentOut] = Field(default_factory=list[AttachmentOut])` (define `AttachmentOut` above `MessageOut`).

`post_message` route: pass `attachment_ids=body.attachment_ids` and give the route `config` (already there).

`_messages_out(session, messages)` gains `directory: Path` — pass `config.attachments_dir` from every caller. Find them with `graft callers _messages_out` (every route returning messages: feed, thread, post, pin, search…); those routes need a `config: Config` parameter if they lack one. Inside, after `counts`:

```python
    files = await services.attachments_for(session, [m.id for m in messages])
```
and in the loop:

```python
        item.attachments = _attachments_out(files.get(m.id, []), directory)
```
and the recursive call `await _messages_out(session, replies_by_root[m.id], directory)`.

- [ ] **Step 6: Run tests**

Run: `cd backend && uv run pytest tests/test_attachments.py tests/test_chat.py tests/test_agents.py -q`
Expected: PASS.

- [ ] **Step 7: Regenerate API types and commit**

```bash
make api-types
git add backend/src/argos frontend/src/api-types.ts backend/tests/test_attachments.py
git commit -m "feat: send chat messages with attachments"
```

---

### Task 5: Attachments in agent transcripts

**Files:**
- Modify: `backend/src/argos/attachments.py` (agent half)
- Modify: `backend/src/argos/agents.py:62-71` (`Turn`) + new helpers after `render_transcript`
- Modify: `backend/src/argos/runner.py:148-170` (`build_transcript`), `:337` (caller), `:381` (job turn)
- Test: `backend/tests/test_attachments.py`

**Interfaces:**
- Consumes: `services.attachments_for`, `models.Attachment`.
- Produces:
  - `attachments.AttachmentRef(path: Path, name: str, mime: str, kind: str, size: int)` frozen dataclass; `attachments.ref(a: Attachment, directory: Path) -> AttachmentRef`.
  - `attachments.ImagePart(name, mime, data: bytes, path: Path)`, `attachments.PdfPart(name, data: bytes, mime="application/pdf")`, `attachments.TextPart(text: str)`; `AgentPart = ImagePart | PdfPart | TextPart`.
  - `attachments.to_part(ref, *, pdf_native: bool) -> AgentPart`
  - `attachments.parts_for(groups: Sequence[Sequence[AttachmentRef]], *, pdf_native: bool) -> list[list[AgentPart]]`
  - `attachments.data_url(part: ImagePart) -> str`
  - `agents.Turn.attachments: tuple[AttachmentRef, ...] = ()`
  - `agents.Media = ImagePart | PdfPart`
  - `agents.split_attachments(turns: Sequence[Turn], *, pdf_native: bool = False) -> list[tuple[Turn, list[Media]]]`
  - `agents.with_attachments(turns: Sequence[Turn], *, pdf_native: bool = False) -> tuple[list[Turn], list[Media]]`
  - `runner.build_transcript(session, trigger, directory: Path) -> list[Turn]`

- [ ] **Step 1: Write the failing tests** (append)

```python
from argos.agents import Turn, split_attachments, with_attachments
from argos.attachments import AttachmentRef, ImagePart, PdfPart, TextPart, parts_for, to_part


def ref(tmp_path: Path, name: str, data: bytes, kind: str, mime: str) -> AttachmentRef:
    path = tmp_path / name
    path.write_bytes(data)
    return AttachmentRef(path, name, mime, kind, len(data))


def test_large_image_is_shrunk_for_agents(tmp_path: Path) -> None:
    part = to_part(ref(tmp_path, "big.png", png(4000, 1000), "image", "image/png"), pdf_native=False)
    assert isinstance(part, ImagePart)
    with Image.open(io.BytesIO(part.data)) as image:
        assert max(image.size) == 2000
    small = to_part(ref(tmp_path, "s.png", png(), "image", "image/png"), pdf_native=False)
    assert isinstance(small, ImagePart) and small.data == png() and small.mime == "image/png"


def test_text_is_inlined_and_cut(tmp_path: Path) -> None:
    part = to_part(ref(tmp_path, "a.md", ("x" * 100_005).encode(), "text", "text/plain"),
                   pdf_native=False)  # fmt: skip
    assert isinstance(part, TextPart)
    assert part.text.startswith("[첨부: a.md]\n```\n") and "이후 생략" in part.text
    assert part.text.count("x") == 100_000


def test_fence_outlasts_backticks_in_the_file(tmp_path: Path) -> None:
    part = to_part(ref(tmp_path, "a.md", b"```py\nx\n```", "text", "text/plain"), pdf_native=False)
    assert isinstance(part, TextPart) and "\n````\n" in part.text


def test_pdf_native_or_extracted(tmp_path: Path) -> None:
    pdf = ref(tmp_path, "p.pdf", text_pdf("ARGOS42"), "pdf", "application/pdf")
    assert isinstance(to_part(pdf, pdf_native=True), PdfPart)
    text = to_part(pdf, pdf_native=False)
    assert isinstance(text, TextPart) and "ARGOS42" in text.text


def test_unreadable_and_other_files_become_notes(tmp_path: Path) -> None:
    gone = AttachmentRef(tmp_path / "nope", "lost.png", "image/png", "image", 10)
    assert to_part(gone, pdf_native=False) == TextPart("[첨부: lost.png — 읽지 못함]")
    other = to_part(ref(tmp_path, "a.zip", b"PK", "file", "application/zip"), pdf_native=False)
    assert other == TextPart("[첨부: a.zip, 2 B — 내용 읽기 불가]")


def test_older_images_are_capped_newest_turn_keeps_all(tmp_path: Path) -> None:
    image = ref(tmp_path, "i.png", png(), "image", "image/png")
    groups = parts_for([[image] * 4, [image] * 4, [image] * 3], pdf_native=False)
    kinds = [[type(p).__name__ for p in g] for g in groups]
    assert kinds[2] == ["ImagePart"] * 3  # the newest turn: all
    assert kinds[1] == ["ImagePart"] * 2 + ["TextPart"] * 2  # 5 total
    assert kinds[0] == ["TextPart"] * 4


def test_split_attachments_writes_notes_into_turn_text(tmp_path: Path) -> None:
    image = ref(tmp_path, "i.png", png(), "image", "image/png")
    note = ref(tmp_path, "n.txt", b"memo", "text", "text/plain")
    turns = [Turn("user", "봐줘", attachments=(image, note)), Turn("claude", "응")]
    [(first, media), (second, none)] = split_attachments(turns)
    assert first.attachments == () and none == [] and second.text == "응"
    assert first.text.startswith("봐줘\n\n[첨부: n.txt]") and "[첨부: i.png — 함께 보냄]" in first.text
    assert [m.name for m in media] == ["i.png"]
    flat, all_media = with_attachments(turns)
    assert flat[0].text == first.text and len(all_media) == 1
```

Runner test (append; uses agent fakes):

```python
import time

from fakes import FakeAgent, FakeClassifier, fake_agents


def test_agent_gets_attachments_of_the_thread(settings: Settings) -> None:
    local = FakeAgent("local", ["봤어요"])
    config = settings.model_copy(update={"default_agent": "local"})
    with TestClient(create_app(config)) as client:
        client.app.state.runner.adapter_factory = fake_agents(local=local)  # type: ignore[attr-defined]
        client.app.state.classifier = FakeClassifier(error="no model")  # type: ignore[attr-defined]
        course = channel_id(client, "컴퓨터구조")
        first = upload(client, png(), "board.png")
        asked = post(client, course, "/ask 이거 뭐야", [first["id"]]).json()
        second = upload(client, b"memo", "note.txt")
        reply = client.post(  # an attachment-only reply in the thread
            f"/api/v1/channels/{course}/messages",
            json={"body": "", "thread_root_id": asked["id"], "attachment_ids": [second["id"]]},
        )
        assert reply.status_code == 201, reply.text
        deadline = time.time() + 5
        while len(local.transcripts) < 2 and time.time() < deadline:
            time.sleep(0.02)
    first_turn = local.transcripts[0][-1]
    assert [a.name for a in first_turn.attachments] == ["board.png"]
    assert first_turn.attachments[0].path == config.attachments_dir / first["id"]
    last = local.transcripts[1]
    assert [a.name for a in last[-1].attachments] == ["note.txt"] and last[-1].text == ""
    assert [a.name for a in last[0].attachments] == ["board.png"]  # the root's file too
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_attachments.py -q`
Expected: FAIL — `ImportError: cannot import name 'split_attachments'`.

- [ ] **Step 3: Agent half of `attachments.py`** (append)

```python
# --- what agents get ----------------------------------------------------------------------

MAX_IMAGE_SIDE = 2000
MAX_IMAGE_BYTES = 3_750_000  # Claude refuses images over 5 MB of base64
TEXT_LIMIT = 100_000  # characters per file
MAX_IMAGES = 5  # images re-sent from older turns when a whole transcript goes out


@dataclass(frozen=True)
class AttachmentRef:
    path: Path
    name: str
    mime: str
    kind: str
    size: int


@dataclass(frozen=True)
class ImagePart:
    name: str
    mime: str
    data: bytes  # shrunk when large
    path: Path  # the stored original


@dataclass(frozen=True)
class PdfPart:
    name: str
    data: bytes
    mime: str = "application/pdf"


@dataclass(frozen=True)
class TextPart:
    text: str


AgentPart = ImagePart | PdfPart | TextPart


def ref(attachment: "Attachment", directory: Path) -> AttachmentRef:
    return AttachmentRef(
        directory / attachment.id, attachment.name, attachment.mime, attachment.kind,
        attachment.size,
    )  # fmt: skip


def human_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def _image(path: Path, mime: str) -> tuple[bytes, str]:
    data = path.read_bytes()
    with Image.open(path) as image:
        if max(image.size) <= MAX_IMAGE_SIDE and len(data) <= MAX_IMAGE_BYTES:
            return data, mime
        image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
        out = io.BytesIO()
        image.convert("RGB").save(out, "JPEG", quality=85)
        return out.getvalue(), "image/jpeg"


def _pdf_text(path: Path) -> str:
    chunks: list[str] = []
    total = 0
    for page in PdfReader(path).pages:
        text = page.extract_text() or ""
        chunks.append(text)
        total += len(text)
        if total > TEXT_LIMIT:
            break
    return "\n\n".join(chunks).strip() or "(글자를 찾지 못한 PDF예요. 스캔본일 수 있어요)"


def _fenced(name: str, text: str) -> TextPart:
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    tail = "\n…(이후 생략)" if len(text) > TEXT_LIMIT else ""
    return TextPart(f"[첨부: {name}]\n{fence}\n{text[:TEXT_LIMIT]}{tail}\n{fence}")


def to_part(attachment: AttachmentRef, *, pdf_native: bool) -> AgentPart:
    """One attachment as an agent reads it. Blocking (file reads, resizing). A file that
    cannot be read becomes a note, so one bad file never fails the whole answer."""
    try:
        match attachment.kind:
            case "image":
                data, mime = _image(attachment.path, attachment.mime)
                return ImagePart(attachment.name, mime, data, attachment.path)
            case "pdf" if pdf_native:
                return PdfPart(attachment.name, attachment.path.read_bytes())
            case "pdf":
                return _fenced(attachment.name, _pdf_text(attachment.path))
            case "text":
                return _fenced(attachment.name, attachment.path.read_text(errors="replace"))
            case _:
                size = human_size(attachment.size)
                return TextPart(f"[첨부: {attachment.name}, {size} — 내용 읽기 불가]")
    except Exception:  # Pillow and pypdf raise many kinds of errors on bad files
        return TextPart(f"[첨부: {attachment.name} — 읽지 못함]")


def parts_for(
    groups: Sequence[Sequence[AttachmentRef]], *, pdf_native: bool
) -> list[list[AgentPart]]:
    """Each turn's attachments as parts. Every image of the newest turn goes; older
    turns' images fill the rest of MAX_IMAGES, newest first, and the others are named."""
    budget = MAX_IMAGES
    out: list[list[AgentPart]] = []
    for index in range(len(groups) - 1, -1, -1):
        newest = index == len(groups) - 1
        parts: list[AgentPart] = []
        for attachment in groups[index]:
            if attachment.kind == "image":
                if not newest and budget <= 0:
                    parts.append(TextPart(f"[첨부: {attachment.name} — 이전 이미지라 생략]"))
                    continue
                budget -= 1
            parts.append(to_part(attachment, pdf_native=pdf_native))
        out.append(parts)
    return out[::-1]


def data_url(part: ImagePart) -> str:
    return f"data:{part.mime};base64,{base64.b64encode(part.data).decode()}"
```
Add imports: `base64`, `io`, `re`, `from collections.abc import Sequence`, `from typing import TYPE_CHECKING`, `from pypdf import PdfReader`, and

```python
if TYPE_CHECKING:
    from argos.models import Attachment
```
- [ ] **Step 4: `Turn` and transcript helpers in `agents.py`**

```python
@dataclass(frozen=True)
class Turn:
    speaker: str  # "user" or an agent name
    text: str
    # A `/name args` skill call (skills.apply_skill): `text` then says to use the skill,
    # for backends that cannot invoke it natively.
    skill: str | None = None
    args: str = ""
    skill_path: str | None = None  # Codex: the skill's SKILL.md
    attachments: tuple[AttachmentRef, ...] = ()  # files sent with this message
```
After `render_transcript`:

```python
Media = ImagePart | PdfPart


def split_attachments(
    turns: Sequence[Turn], *, pdf_native: bool = False
) -> list[tuple[Turn, list[Media]]]:
    """Each turn with its text-like attachments written into its text (and a line naming
    each image or PDF), plus the images (and, for Claude, PDFs) to send with it.
    Blocking when there are attachments: call through asyncio.to_thread."""
    if not any(t.attachments for t in turns):
        return [(t, []) for t in turns]
    groups = parts_for([t.attachments for t in turns], pdf_native=pdf_native)
    out: list[tuple[Turn, list[Media]]] = []
    for turn, parts in zip(turns, groups, strict=True):
        media: list[Media] = [p for p in parts if not isinstance(p, TextPart)]
        notes = [p.text for p in parts if isinstance(p, TextPart)]
        notes += [f"[첨부: {m.name} — 함께 보냄]" for m in media]
        text = "\n\n".join(x for x in (turn.text, *notes) if x)
        out.append((replace(turn, text=text, attachments=()), media))
    return out


def with_attachments(
    turns: Sequence[Turn], *, pdf_native: bool = False
) -> tuple[list[Turn], list[Media]]:
    pairs = split_attachments(turns, pdf_native=pdf_native)
    return [t for t, _ in pairs], [m for _, media in pairs for m in media]
```
Imports: `from dataclasses import dataclass, replace`, `from collections.abc import Sequence`, `from argos.attachments import AttachmentRef, ImagePart, PdfPart, TextPart, data_url, parts_for`. Check `skills.apply_skill` (skills.py:60) keeps attachments: change its `Turn("user", instruction(...), skill=..., args=..., skill_path=...)` to `replace(last, text=instruction(name, args), skill=name, args=args, skill_path=skill.path)` where `last` is the turn it replaces — read the function first and keep its behavior otherwise.

- [ ] **Step 5: Runner**

`build_transcript`:

```python
async def build_transcript(
    session: AsyncSession, trigger: Message, directory: Path
) -> list[Turn]:
    """The thread the trigger is in; in a DM, the recent turns of its conversation.
    `directory`: where attachments are stored (Settings.attachments_dir)."""
    ...  # unchanged message selection
    files = await services.attachments_for(session, [m.id for m in messages])
    turns = [
        Turn(
            "user" if m.author_type == "user" else (m.author_id or "system"),
            m.body,
            attachments=tuple(attachments.ref(a, directory) for a in files.get(m.id, [])),
        )
        for m in messages
        if m.body.strip() or m.id in files
    ]
    return turns[-TRANSCRIPT_LIMIT:]
```
Caller in `respond`: `await build_transcript(session, trigger, self.settings.attachments_dir)`.

`start_job`, before building `coro`:

```python
        files = await services.attachments_for(session, [trigger.id])
        refs = tuple(
            attachments.ref(a, self.settings.attachments_dir) for a in files.get(trigger.id, [])
        )
```
and use `[Turn("user", instructions, attachments=refs)]`. Import `from argos import attachments`.

- [ ] **Step 6: Run tests**

Run: `cd backend && uv run pytest tests/test_attachments.py tests/test_agents.py tests/test_jobs.py tests/test_coding.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/src/argos backend/tests/test_attachments.py
git commit -m "feat: carry attachments into agent transcripts"
```

---

### Task 6: Each adapter sends attachments natively

**Files:**
- Modify: `backend/src/argos/agents.py` (adapters at lines ~106, ~177, ~284, ~368, ~482, ~649; `build_adapter` ~930)
- Test: `backend/tests/test_agents.py`

**Interfaces:**
- Consumes: `with_attachments`, `split_attachments`, `Media`, `data_url`, `ImagePart`, `PdfPart` (Task 5).
- Produces:
  - `agents.claude_prompt(text: str, media: list[Media]) -> AsyncIterator[dict[str, Any]]`
  - `agents.chat_messages(pairs: list[tuple[Turn, list[Media]]], context: str, me: str, vision: bool) -> list[dict[str, Any]]`
  - `agents.ollama_vision(base_url: str, model: str) -> Awaitable[bool]` (cached)
  - `OpenAICompatAdapter(..., vision: Callable[[], Awaitable[bool]] | None = None)`, same keyword on `LLMToolAdapter`.
  - `CLIAdapter(..., image_flag: str | None = None)`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_agents.py`)

```python
from files import png, text_pdf

from argos.attachments import AttachmentRef


def _file(tmp_path: Path, name: str, data: bytes, kind: str, mime: str) -> AttachmentRef:
    path = tmp_path / name
    path.write_bytes(data)
    return AttachmentRef(path, name, mime, kind, len(data))


async def test_claude_sdk_sends_images_and_pdfs_as_blocks(fake_sdk: Any, tmp_path: Path) -> None:
    from argos.agents import Turn

    adapter, calls = fake_sdk
    files = (
        _file(tmp_path, "a.png", png(), "image", "image/png"),
        _file(tmp_path, "p.pdf", text_pdf("X"), "pdf", "application/pdf"),
    )
    [e async for e in adapter.stream([Turn("user", "봐줘", attachments=files)], "ctx", "t-1")]
    prompt, _options = calls[0]
    [message] = [m async for m in prompt]
    content = message["message"]["content"]
    assert [b["type"] for b in content] == ["image", "document", "text"]
    assert content[0]["source"]["media_type"] == "image/png"
    assert content[1]["title"] == "p.pdf"
    assert "봐줘" in content[2]["text"] and "[첨부: a.png — 함께 보냄]" in content[2]["text"]


async def test_claude_sdk_without_attachments_still_sends_a_string(fake_sdk: Any) -> None:
    from argos.agents import Turn

    adapter, calls = fake_sdk
    [e async for e in adapter.stream([Turn("user", "안녕")], "ctx", "t-2")]
    assert isinstance(calls[0][0], str)


async def test_codex_app_server_sends_image_input(fake_codex: Any, tmp_path: Path) -> None:
    from argos.agents import Turn

    adapter, _ids, seen = fake_codex
    image = _file(tmp_path, "a.png", png(), "image", "image/png")
    [e async for e in adapter.stream([Turn("user", "봐줘", attachments=(image,))], "ctx", "t-3")]
    turn = next(r for r in seen()["requests"] if r["method"] == "turn/start")
    kinds = [item["type"] for item in turn["params"]["input"]]
    assert kinds == ["text", "image"]
    assert turn["params"]["input"][1]["url"].startswith("data:image/png;base64,")


async def test_hermes_sends_input_image(tmp_path: Path) -> None:
    import httpx2
    from openai import AsyncOpenAI

    from argos.agents import HermesResponsesAdapter, Turn

    sent: list[dict[str, Any]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(json.loads(request.content))
        body = (FIXTURES / "hermes_responses.sse").read_bytes()
        return httpx2.Response(200, content=body, headers={"content-type": "text/event-stream"})

    http = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    client = AsyncOpenAI(base_url="http://hermes.test/v1", api_key="k", http_client=http)
    adapter = HermesResponsesAdapter(client, "hermes-agent", "hermes")
    files = (
        _file(tmp_path, "a.png", png(), "image", "image/png"),
        _file(tmp_path, "p.pdf", text_pdf("PDFTEXT"), "pdf", "application/pdf"),
    )
    [e async for e in adapter.stream([Turn("user", "봐줘", attachments=files)], "ctx", "c")]
    [message] = sent[0]["input"]
    assert message["role"] == "user"
    assert [p["type"] for p in message["content"]] == ["input_text", "input_image"]
    assert "PDFTEXT" in message["content"][0]["text"]  # PDFs go as text to Hermes
    assert message["content"][1]["image_url"].startswith("data:image/png;base64,")


def _ollama(handler: Any, vision: bool) -> Any:
    import httpx2
    from openai import AsyncOpenAI

    from argos.agents import OpenAICompatAdapter

    async def sees() -> bool:
        return vision

    http = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    client = AsyncOpenAI(base_url="http://ollama.test/v1", api_key="k", http_client=http)
    return OpenAICompatAdapter(client, "gemma", "local", vision=sees)


def _sse_done(sent: list[dict[str, Any]]) -> Any:
    import httpx2

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(json.loads(request.content))
        chunk = {"id": "1", "object": "chat.completion.chunk", "created": 0, "model": "m",
                 "choices": [{"index": 0, "delta": {"content": "ok"}, "finish_reason": None}]}  # fmt: skip
        body = f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n"
        return httpx2.Response(200, content=body, headers={"content-type": "text/event-stream"})

    return handler


async def test_ollama_vision_model_gets_image_url(tmp_path: Path) -> None:
    from argos.agents import Turn

    sent: list[dict[str, Any]] = []
    adapter = _ollama(_sse_done(sent), vision=True)
    image = _file(tmp_path, "a.png", png(), "image", "image/png")
    [e async for e in adapter.stream([Turn("user", "봐줘", attachments=(image,))], "ctx", "s")]
    user = sent[0]["messages"][-1]
    assert [p["type"] for p in user["content"]] == ["text", "image_url"]


async def test_ollama_text_model_is_told_it_cannot_see(tmp_path: Path) -> None:
    from argos.agents import Turn

    sent: list[dict[str, Any]] = []
    adapter = _ollama(_sse_done(sent), vision=False)
    image = _file(tmp_path, "a.png", png(), "image", "image/png")
    [e async for e in adapter.stream([Turn("user", "봐줘", attachments=(image,))], "ctx", "s")]
    user = sent[0]["messages"][-1]
    assert isinstance(user["content"], str) and "이미지를 볼 수 없어" in user["content"]


async def test_cli_adapter_passes_images_as_files(tmp_path: Path) -> None:
    from argos.agents import CLIAdapter, Turn, is_codex_final

    script = tmp_path / "fake-cli"
    record = tmp_path / "argv.txt"
    done = json.dumps({"type": "turn.completed"})
    script.write_text(
        f"#!/bin/sh\nfor a in \"$@\"; do echo \"$a\" >> {record}; done\n"
        f"for a in \"$@\"; do case \"$a\" in *.png) test -s \"$a\" && echo exists >> {record};; esac; done\n"
        f"echo '{done}'\n"
    )
    script.chmod(0o755)
    adapter = CLIAdapter(
        [str(script)], tmp_path, parse_codex_line, is_codex_final, "codex", image_flag="--image"
    )
    image = _file(tmp_path, "a.png", png(), "image", "image/png")
    [e async for e in adapter.stream([Turn("user", "봐줘", attachments=(image,))], "ctx", "s")]
    lines = record.read_text().splitlines()
    flag = lines.index("--image")
    assert lines[flag + 1].endswith(".png") and "exists" in lines
```
If the Task 1 spike showed Codex rejects data URLs, replace the app-server assertion with `kinds == ["text", "localImage"]` and `Path(input[1]["path"]).suffix == ".png"`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_agents.py -q -k "attachments or image or pdf or vision or cannot_see or blocks or string"`
Expected: FAIL (`unexpected keyword argument 'vision'`, prompt is a str, etc.).

- [ ] **Step 3: Shared helpers in `agents.py`** (after `with_attachments`)

```python
async def claude_prompt(text: str, media: list[Media]) -> AsyncIterator[dict[str, Any]]:
    """Stream-JSON input for the Claude SDK: one user message, files before the words."""
    content: list[dict[str, Any]] = []
    for item in media:
        source = {
            "type": "base64",
            "media_type": item.mime,
            "data": base64.b64encode(item.data).decode(),
        }
        if isinstance(item, ImagePart):
            content.append({"type": "image", "source": source})
        else:
            content.append({"type": "document", "source": source, "title": item.name})
    content.append({"type": "text", "text": text})
    yield {"type": "user", "message": {"role": "user", "content": content}, "parent_tool_use_id": None}


NO_VISION = "(이 모델은 이미지를 볼 수 없어서 이미지는 빠졌어요. 비전 모델을 고르면 볼 수 있어요)"


def chat_messages(
    pairs: list[tuple[Turn, list[Media]]], context: str, me: str, vision: bool
) -> list[dict[str, Any]]:
    """OpenAI chat messages: images as image_url parts on their turn (vision models), or a
    note that they were left out."""
    messages: list[dict[str, Any]] = [{"role": "system", "content": context}]
    for turn, media in pairs:
        if turn.speaker == me:
            messages.append({"role": "assistant", "content": turn.text})
            continue
        text = turn.text if turn.speaker == "user" else f"[{turn.speaker}]: {turn.text}"
        images = [m for m in media if isinstance(m, ImagePart)]
        if images and vision:
            parts: list[dict[str, Any]] = [{"type": "text", "text": text}]
            parts += [{"type": "image_url", "image_url": {"url": data_url(m)}} for m in images]
            messages.append({"role": "user", "content": parts})
        else:
            messages.append({"role": "user", "content": f"{text}\n\n{NO_VISION}" if images else text})
    return messages


_VISION: dict[tuple[str, str], bool] = {}


async def ollama_vision(base_url: str, model: str) -> bool:
    """Whether an Ollama model takes images (`/api/show` capabilities). A server that
    does not answer that (another OpenAI-compatible one) gets the images and decides."""
    key = (base_url, model)
    if key not in _VISION:
        root = base_url.rstrip("/").removesuffix("/v1")
        try:
            async with httpx2.AsyncClient(timeout=5) as http:
                response = await http.post(f"{root}/api/show", json={"model": model})
                response.raise_for_status()
                capabilities = response.json().get("capabilities") or []
        except (httpx2.HTTPError, ValueError):
            return True
        _VISION[key] = "vision" in capabilities
    return _VISION[key]
```
Imports: `base64`, `httpx2`, `from collections.abc import Awaitable, Callable` (if not present).

- [ ] **Step 4: Adapters**

`OpenAICompatAdapter`: constructor gains `vision: Callable[[], Awaitable[bool]] | None = None` stored as `self._vision`; `stream` becomes:

```python
        pairs = await asyncio.to_thread(split_attachments, transcript)
        has_images = any(isinstance(m, ImagePart) for _, media in pairs for m in media)
        vision = has_images and (self._vision is None or await self._vision())
        messages = chat_messages(pairs, context, self._name, vision)
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=cast(Any, messages),
                stream=True,
                extra_body=self._extra,
            )
```
(the old per-turn loop is deleted; `ChatCompletionMessageParam` import goes if unused).

`LLMToolAdapter`: same `vision` keyword; replace its message loop with:

```python
        pairs = await asyncio.to_thread(split_attachments, transcript)
        has_images = any(isinstance(m, ImagePart) for _, media in pairs for m in media)
        vision = has_images and (self._vision is None or await self._vision())
        messages = chat_messages(pairs, context, self._name, vision)
```

`HermesResponsesAdapter.stream`:

```python
        turns, media = await asyncio.to_thread(
            with_attachments, pending_turns(transcript, self._name) or transcript[-1:]
        )
        text = render_new_turns(turns)
        images = [m for m in media if isinstance(m, ImagePart)]
        request_input: Any = text
        if images:
            content = [{"type": "input_text", "text": text}]
            content += [{"type": "input_image", "image_url": data_url(m)} for m in images]
            request_input = [{"role": "user", "content": content}]
        try:
            response = await self._client.responses.create(
                model=self._model,
                input=request_input,
                ...  # unchanged
```

`ClaudeSDKAdapter.stream`, replacing the block from `last = transcript[-1]` to the `prompt = …` lines:

```python
        last = transcript[-1] if transcript else None
        skill = last is not None and last.skill is not None and self._tools != []
        if skill or exists:
            sent = [last] if skill and last else pending_turns(transcript, self._name) or transcript[-1:]
        else:
            sent = transcript
        turns, media = await asyncio.to_thread(with_attachments, sent, pdf_native=True)
        if skill and last is not None:
            text = f"/{last.skill} {last.args}".strip()  # Claude Code expands it itself
        elif exists:
            text = render_new_turns(turns)
        else:
            text = render_transcript(turns, self._name)
        prompt: str | AsyncIterator[dict[str, Any]] = (
            claude_prompt(text, media) if media else text
        )
```
(`pdf_native=True` unless the Task 1 spike showed otherwise.) The `query(prompt=prompt, …)` call is unchanged.

`CodexAppServerAdapter._converse`, replacing the `text = (...)` and `items = [...]` lines:

```python
        sent = (pending_turns(transcript, self._name) or transcript[-1:]) if resumed else transcript
        turns, media = await asyncio.to_thread(with_attachments, sent)
        text = render_new_turns(turns) if resumed else render_transcript(turns, self._name)
        items: list[dict[str, Any]] = [{"type": "text", "text": text}]
        items += [{"type": "image", "url": data_url(m)} for m in media if isinstance(m, ImagePart)]
```
(Keep the skill item insertion that follows.)

`CLIAdapter`: constructor gains `image_flag: str | None = None`; `stream` becomes:

```python
        turns, media = await asyncio.to_thread(with_attachments, transcript)
        prompt = f"{context}\n\n{render_transcript(turns, self._name)}"
        images = [m for m in media if isinstance(m, ImagePart)] if self._image_flag else []
        with tempfile.TemporaryDirectory(prefix="argos-images-") as folder:
            extra: list[str] = []
            for number, image in enumerate(images, 1):
                suffix = mimetypes.guess_extension(image.mime) or ".img"
                path = Path(folder) / f"{number}{suffix}"
                await asyncio.to_thread(path.write_bytes, image.data)
                extra += [self._image_flag or "", str(path)]
            async for event in self._exec(extra, prompt):
                yield event
```
Imports: `tempfile`, `mimetypes`. In `build_adapter`'s codex exec branch pass `image_flag="--image"`.

`build_adapter` Ollama branch: build `sees = lambda: ollama_vision(base, model)` (define as a nested `async def sees() -> bool: return await ollama_vision(base, model)` to keep pyright strict happy) and pass `vision=sees` to both `LLMToolAdapter` and `OpenAICompatAdapter`.

- [ ] **Step 5: Run the whole agent suite**

Run: `cd backend && uv run pytest tests/test_agents.py tests/test_custom_agents.py tests/test_debate.py tests/test_jobs.py -q`
Expected: PASS. The fake SDK fixture's `fake_query(*, prompt: str, …)` annotation becomes `prompt: Any`.

- [ ] **Step 6: Lint and commit**

```bash
cd backend && uv run ruff check . && uv run ruff format --check . && uv run pyright
git add backend/src/argos/agents.py backend/tests/test_agents.py
git commit -m "feat: send attachments to Claude, Codex, Hermes and Ollama natively"
```

---

### Task 7: Frontend attachment module

**Files:**
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/icons.tsx`
- Modify: `frontend/src/desktop.ts`
- Create: `frontend/src/attachments.tsx`
- Create: `frontend/src/attachments.test.tsx`
- Modify: `frontend/src/en.ts`

**Interfaces:**
- Consumes: generated `components["schemas"]["AttachmentOut"]`, `MessageCreate.attachment_ids`, `MessageOut.attachments` (Task 4).
- Produces:
  - `api.ts`: `type Attachment`, `uploadAttachment(file: File): Promise<Attachment>`, `deleteAttachment(id: string): Promise<void>`, `attachmentUrl(id: string): string`.
  - `desktop.ts`: `openAttachment(id: string, name: string)`.
  - `icons.tsx`: `PaperclipIcon`, `FileIcon`.
  - `attachments.tsx`: `MAX_FILES`, `type Draft`, `draftsReducer`, `canSend(text, drafts)`, `pastedFiles(data)`, `hasFiles(data)`, `formatSize(bytes)`, `useAttachmentDrafts()` → `{ drafts, add(files), remove(key), retry(key), clear(), ids }`, `useFileDrop(add)` → `{ over, handlers }`, `DraftChips`, `AttachmentList`.

- [ ] **Step 1: API helpers in `api.ts`**

```ts
export type Attachment = Schemas["AttachmentOut"];

export const attachmentUrl = (id: string) =>
  `/api/v1/attachments/${encodeURIComponent(id)}/content`;

export const uploadAttachment = (file: File) =>
  call(
    client.POST("/api/v1/attachments", {
      // The generated type says string (OpenAPI "binary"); the form carries the File.
      body: { file: file as unknown as string },
      bodySerializer: () => {
        const form = new FormData();
        form.append("file", file, file.name);
        return form;
      },
    }),
  );

export const deleteAttachment = (id: string) =>
  call(
    client.DELETE("/api/v1/attachments/{attachment_id}", {
      params: { path: { attachment_id: id } },
    }),
  );
```
In `invalidateFor`'s `keys`: `attachment: [...feeds],`.

- [ ] **Step 2: Icons and desktop bridge**

`icons.tsx`:

```tsx
export const PaperclipIcon = ({ size = 16 }: P) => (
  <Stroke size={size}>
    <path d="M21 11.5 12.6 19.9a5 5 0 0 1-7.1-7.1l8.5-8.5a3.3 3.3 0 0 1 4.7 4.7l-8.5 8.5a1.7 1.7 0 0 1-2.4-2.4l7.8-7.8" />
  </Stroke>
);

export const FileIcon = ({ size = 16 }: P) => (
  <Stroke size={size}>
    <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
    <path d="M14 3v5h5" />
  </Stroke>
);
```
`desktop.ts`:

```ts
/** Opens an attachment in its default app (Preview, …); the app copies it out first. */
export const openAttachment = (id: string, name: string) =>
  invoke("open_attachment", { id, name });
```

- [ ] **Step 3: Write the failing tests** — `frontend/src/attachments.test.tsx`

```tsx
import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import type { Attachment } from "./api";
import {
  AttachmentList,
  canSend,
  type Draft,
  draftsReducer,
  formatSize,
  pastedFiles,
} from "./attachments";

const file = (name: string, type = "image/png") => new File(["x"], name, { type });

const transfer = (files: File[], text = "") =>
  ({ files, getData: (t: string) => (t === "text/plain" ? text : "") }) as unknown as DataTransfer;

test("a screenshot paste is a file", () => {
  expect(pastedFiles(transfer([file("Screenshot.png")]))).toHaveLength(1);
});

test("copied spreadsheet cells stay text even with a picture on the clipboard", () => {
  expect(pastedFiles(transfer([file("image.png")], "a\tb\n1\t2"))).toEqual([]);
});

test("a Finder copy names its files in the text and is still a file", () => {
  expect(pastedFiles(transfer([file("강의.pdf", "application/pdf")], "강의.pdf"))).toHaveLength(1);
});

test("plain text paste has no files", () => {
  expect(pastedFiles(transfer([], "hello"))).toEqual([]);
  expect(pastedFiles(null)).toEqual([]);
});

const draft = (key: string, status: Draft["status"]): Draft => ({ key, file: file(`${key}.png`), status });

test("send waits for every upload and needs text or a file", () => {
  expect(canSend("", [])).toBe(false);
  expect(canSend("hi", [])).toBe(true);
  expect(canSend("", [draft("a", "done")])).toBe(true);
  expect(canSend("hi", [draft("a", "done"), draft("b", "uploading")])).toBe(false);
  expect(canSend("hi", [draft("a", "error")])).toBe(false);
});

test("reducer tracks upload results, retries and removal", () => {
  const uploaded = { id: "1", name: "a.png" } as Attachment;
  let state = draftsReducer([], { type: "add", drafts: [draft("a", "uploading"), draft("b", "uploading")] });
  state = draftsReducer(state, { type: "done", key: "a", attachment: uploaded });
  state = draftsReducer(state, { type: "error", key: "b", error: "25MB까지 올릴 수 있어요" });
  expect(state.map((d) => d.status)).toEqual(["done", "error"]);
  expect(state[1].error).toBe("25MB까지 올릴 수 있어요");
  state = draftsReducer(state, { type: "retry", key: "b" });
  expect(state[1]).toMatchObject({ status: "uploading", error: undefined });
  state = draftsReducer(state, { type: "remove", key: "a" });
  expect(state.map((d) => d.key)).toEqual(["b"]);
  expect(draftsReducer(state, { type: "clear" })).toEqual([]);
});

test("formats sizes", () => {
  expect(formatSize(512)).toBe("512 B");
  expect(formatSize(2048)).toBe("2.0 KB");
  expect(formatSize(3 * 1024 * 1024)).toBe("3.0 MB");
});

const attachment = (over: Partial<Attachment>): Attachment => ({
  id: "1", name: "a.png", mime: "image/png", size: 10, kind: "image",
  width: 40, height: 30, missing: false, ...over,
});

test("images show as pictures, files as cards, missing files greyed", () => {
  render(
    <AttachmentList
      attachments={[
        attachment({ id: "i" }),
        attachment({ id: "f", name: "강의.pdf", kind: "pdf", mime: "application/pdf", width: null, height: null }),
        attachment({ id: "m", name: "gone.png", missing: true }),
      ]}
    />,
  );
  expect(screen.getByRole("img", { name: "a.png" })).toHaveAttribute("src", "/api/v1/attachments/i/content");
  expect(screen.getByRole("link", { name: /강의\.pdf/ })).toHaveAttribute("download", "강의.pdf");
  expect(screen.getByText("파일 없음")).toBeInTheDocument();
});
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `cd frontend && bunx vitest run src/attachments.test.tsx`
Expected: FAIL — cannot resolve `./attachments`.

- [ ] **Step 5: Implement `attachments.tsx`**

```tsx
import {
  type DragEvent,
  useEffect,
  useReducer,
  useRef,
  useState,
} from "react";
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
      return patch(action.key, { status: "done", attachment: action.attachment });
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
      error: index < room ? undefined : tr("첨부는 10개까지 붙일 수 있어요"),
      preview: file.type.startsWith("image/") ? URL.createObjectURL(file) : undefined,
    }));
    dispatch({ type: "add", drafts: fresh });
    for (const d of fresh) if (d.status === "uploading") void upload(d);
  };

  const forget = (d: Draft) => {
    if (d.preview) URL.revokeObjectURL(d.preview);
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
    if (!d || d.error === tr("첨부는 10개까지 붙일 수 있어요")) return;
    dispatch({ type: "retry", key });
    void upload(d);
  };

  /** After a send: the files now belong to the message, only the previews go. */
  const clear = () => {
    for (const d of latest.current) forget(d);
    dispatch({ type: "clear" });
  };

  useEffect(() => () => latest.current.forEach(forget), []);

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
      if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setOver(false);
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
  compact?: boolean;
}) {
  if (drafts.length === 0) return null;
  return (
    <ul className="m-0 flex list-none flex-wrap gap-1.5 p-0" aria-label={tr("첨부")}>
      {drafts.map((d) => (
        <li
          key={d.key}
          className={`flex max-w-[240px] items-center gap-2 rounded-xl border bg-inset py-1 pr-1 pl-1.5 text-[12px] ${d.status === "error" ? "border-danger" : "border-line-soft"}`}
        >
          {!compact && d.preview ? (
            <img src={d.preview} alt="" className="size-8 rounded-lg object-cover" />
          ) : (
            <span className="text-meta"><FileIcon size={compact ? 13 : 16} /></span>
          )}
          <span className="flex min-w-0 flex-col">
            <span className="truncate text-text">{d.file.name}</span>
            {!compact && (
              <span className={d.status === "error" ? "text-danger" : "text-meta"}>
                {d.status === "error" ? d.error : formatSize(d.file.size)}
              </span>
            )}
          </span>
          {d.status === "uploading" && <PawTrail />}
          {d.status === "error" && d.error !== tr("첨부는 10개까지 붙일 수 있어요") && (
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
  const images = attachments.filter((a) => a.kind === "image" && !a.missing);
  const others = attachments.filter((a) => !(a.kind === "image" && !a.missing));
  if (attachments.length === 0) return null;
  const single = images.length === 1;
  return (
    <div className="flex flex-col gap-2">
      {images.length > 0 && (
        <div className={single ? "flex" : "grid max-w-[480px] grid-cols-3 gap-1.5"}>
          {images.map((a, index) => (
            <button
              key={a.id}
              type="button"
              onClick={() => setOpen(index)}
              className="cursor-zoom-in overflow-hidden rounded-2xl border border-line-soft bg-inset p-0"
              style={
                single && a.width && a.height
                  ? { aspectRatio: `${a.width} / ${a.height}`, width: Math.min(420, a.width), maxHeight: 320 }
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
            <span className="ml-auto shrink-0 text-[12px] text-meta">{tr("파일 없음")}</span>
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
            <span className="text-meta"><FileIcon /></span>
            <span className="truncate text-[13px]">{a.name}</span>
            <span className="ml-auto shrink-0 font-mono text-[11.5px] text-meta">
              {formatSize(a.size)}
            </span>
          </a>
        ),
      )}
      {open !== null && images[open] && (
        <Lightbox images={images} index={open} onIndex={setOpen} onClose={() => setOpen(null)} />
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
  return (
    <dialog
      ref={ref}
      aria-label={image.name}
      onClose={onClose}
      onClick={(e) => {
        if (e.target === e.currentTarget) ref.current?.close();
      }}
      onKeyDown={(e) => {
        if (e.key === "ArrowRight") onIndex((index + 1) % images.length);
        if (e.key === "ArrowLeft") onIndex((index - 1 + images.length) % images.length);
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
```
Check `text-on-dark`, `border-danger`, `border-line` exist in `index.css` (`grep -n "\-\-on-dark\|\-\-danger\|\-\-line:" frontend/src/index.css`); if `border-line` is missing use `border-line-soft`.

jsdom has no `HTMLDialogElement.showModal`; the test does not open the lightbox, so nothing to stub.

- [ ] **Step 6: English strings** — add to `en.ts`:

```ts
  첨부: "Attachments",
  "첨부 빼기": "Remove attachment",
  "다시 시도": "Retry",
  "파일 없음": "File missing",
  "첨부는 10개까지 붙일 수 있어요": "You can attach up to 10 files.",
  "파일 붙이기": "Attach files",
  "여기에 놓아 첨부": "Drop to attach",
  "이미 보낸 첨부는 지울 수 없어요": "An attachment that was sent cannot be removed.",
  "빈 메시지는 보낼 수 없어요": "Type a message or attach a file.",
```
(If `"다시 시도"` or `"빈 메시지는 보낼 수 없어요"` already exist in `en.ts`, skip the duplicate — biome flags duplicate keys.) Server messages like `"25MB까지 올릴 수 있어요"` are dynamic; add to `errorText` replacements in `api.ts`: `[/^(\d+)MB까지 올릴 수 있어요$/, "Files can be up to $1 MB."]`, `[/^첨부는 (\d+)개까지 보낼 수 있어요$/, "Up to $1 attachments per message."]`.

- [ ] **Step 7: Run tests and checks**

Run: `cd frontend && bunx vitest run src/attachments.test.tsx && bunx tsc -b && bunx biome check src`
Expected: PASS, no type or lint errors (`bunx biome check --write src` for formatting).

- [ ] **Step 8: Commit**

```bash
git add frontend/src/attachments.tsx frontend/src/attachments.test.tsx frontend/src/api.ts frontend/src/icons.tsx frontend/src/desktop.ts frontend/src/en.ts
git commit -m "feat: attachment chips, previews and file cards in the frontend"
```

---

### Task 8: Wire attachments into Composer, quick capture and the feed

**Files:**
- Modify: `frontend/src/feed.tsx` (`MessageItem` ~line 179, `Composer` ~line 618)
- Modify: `frontend/src/pages/QuickCapture.tsx`
- Test: `frontend/src/attachments.test.tsx` (helpers already covered); manual check in Task 10.

**Interfaces:**
- Consumes: everything Task 7 produces; `Message.attachments`.

- [ ] **Step 1: MessageItem**

Replace the plain-body branch so an attachment-only message renders no empty text block, then the list:

```tsx
        ) : message.body ? (
          <div
            className={`select-text whitespace-pre-wrap ${system || agent ? "text-text-2" : "text-[15px] text-text"}`}
          >
            {message.body}
          </div>
        ) : null}
        {message.attachments.length > 0 && (
          <AttachmentList attachments={message.attachments} />
        )}
```

- [ ] **Step 2: Composer**

Inside `Composer`:

```tsx
  const files = useAttachmentDrafts();
  const drop = useFileDrop(files.add);
  const picker = useRef<HTMLInputElement>(null);
```
`send`:

```tsx
  const send = () => {
    const body = text.trim();
    if (!canSend(text, files.drafts) || post.isPending) return;
    post.mutate(
      {
        channelId: channel.id,
        body,
        attachment_ids: files.ids,
        thread_root_id: threadRootId,
        session_id: sessionId,
        coding: codeable ? (threadRootId ? coding : coding || null) : null,
      },
      {
        onSuccess: () => {
          setText("");
          files.clear();
        },
      },
    );
  };
```
(keep the existing comment above `coding`). Card wrapper gets the drop handlers and a highlight:

```tsx
      <div
        {...drop.handlers}
        className={`${card} relative flex flex-col gap-3 px-[18px] pt-4 pb-3 ${drop.over ? "border-ink" : ""}`}
      >
        {drop.over && (
          <span className="pointer-events-none absolute inset-0 flex items-center justify-center rounded-3xl bg-card/90 text-[13px] font-medium text-ink">
            {tr("여기에 놓아 첨부")}
          </span>
        )}
        <DraftChips drafts={files.drafts} onRemove={files.remove} onRetry={files.retry} />
```
Textarea gets:

```tsx
          onPaste={(e) => {
            const pasted = pastedFiles(e.clipboardData);
            if (pasted.length === 0) return;
            e.preventDefault();
            files.add(pasted);
          }}
```
Toolbar, first item (before the slash buttons):

```tsx
          <button
            type="button"
            aria-label={tr("파일 붙이기")}
            title={tr("파일 붙이기")}
            onClick={() => picker.current?.click()}
            className="flex size-7 cursor-pointer items-center justify-center rounded-full bg-inset text-text-2 hover:text-ink"
          >
            <PaperclipIcon size={14} />
          </button>
          <input
            ref={picker}
            type="file"
            multiple
            hidden
            onChange={(e) => {
              files.add(Array.from(e.target.files ?? []));
              e.target.value = ""; // the same file can be picked again
            }}
          />
```
Send button: `disabled={!canSend(text, files.drafts) || post.isPending}`.

Imports: `AttachmentList, canSend, DraftChips, pastedFiles, useAttachmentDrafts, useFileDrop` from `./attachments`; `PaperclipIcon` from `./icons`.

- [ ] **Step 3: QuickCapture**

```tsx
  const files = useAttachmentDrafts();
  const drop = useFileDrop(files.add);
  const picker = useRef<HTMLInputElement>(null);
```
`close` also calls `files.clear()` only after a successful send; on Esc without sending, call `for (const d of files.drafts) files.remove(d.key)` so unsent uploads are deleted right away. `send`:

```tsx
  const send = () => {
    const body = text.trim();
    if (!canSend(text, files.drafts) || !inbox || post.isPending) return;
    post.mutate(
      { channelId: inbox.id, body, attachment_ids: files.ids },
      {
        onSuccess: () => {
          setText("");
          files.clear();
          setSent(true);
          setTimeout(close, 700);
        },
      },
    );
  };
```
Root div gets `{...drop.handlers}` and `${drop.over ? "border-ink" : ""}`; textarea gets the same `onPaste` as Composer. The footer row (the window is 132 px tall, so no thumbnails): before the hint span insert

```tsx
        <button
          type="button"
          aria-label={tr("파일 붙이기")}
          onClick={() => picker.current?.click()}
          className="cursor-pointer text-text-3 hover:text-ink"
        >
          <PaperclipIcon size={13} />
        </button>
        <input ref={picker} type="file" multiple hidden onChange={(e) => { files.add(Array.from(e.target.files ?? [])); e.target.value = ""; }} />
```
and render `<DraftChips compact drafts={files.drafts} onRemove={files.remove} onRetry={files.retry} />` in place of the hint text while `files.drafts.length > 0` (the hint returns when there are none). Update the placeholder copy to `"생각난 걸 적거나 붙여넣고 Enter — 인박스로 가요 (/task, /event도 돼요)"` and add its English to `en.ts`: `"Type or paste, then Enter — it goes to the inbox (/task, /event work too)"`.

- [ ] **Step 4: Checks**

Run: `cd frontend && bunx vitest run && bunx tsc -b && bunx biome check src`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/feed.tsx frontend/src/pages/QuickCapture.tsx frontend/src/en.ts
git commit -m "feat: paste, drop and pick files in every chat composer"
```

---

### Task 9: Desktop app — file drops reach the page, files open in their app

**Files:**
- Modify: `desktop/src-tauri/tauri.conf.json` (main window)
- Modify: `desktop/src-tauri/src/lib.rs` (`quick_window` ~line 287, new command, `generate_handler!` ~line 388)
- Modify: `desktop/src-tauri/build.rs`, `desktop/src-tauri/capabilities/default.json`

**Interfaces:**
- Consumes: `openAttachment(id, name)` in `desktop.ts` (Task 7); server stores files at `data_dir()/attachments/<id>` (Task 2).
- Produces: Tauri command `open_attachment(id: String, name: String) -> Result<(), String>`, permission `allow-open-attachment`.

- [ ] **Step 1: Let drops through to the page**

`tauri.conf.json`, main window object: add `"dragDropEnabled": false` (Tauri otherwise takes file drops for its own `DragDrop` event and the page never sees them). `quick_window`: add `.disable_drag_drop_handler()` to the builder chain.

- [ ] **Step 2: `open_attachment` command**

```rust
/// Extensions opened in their default app (Preview, Pages, …). Anything else — scripts,
/// `.command`, apps — is only shown in Finder, so a click never runs a file.
const OPENABLE: &[&str] = &[
    "pdf", "png", "jpg", "jpeg", "gif", "webp", "heic", "txt", "md", "csv", "json", "rtf",
    "doc", "docx", "xls", "xlsx", "ppt", "pptx", "key", "pages", "numbers", "hwp", "hwpx",
    "zip", "mp3", "m4a", "wav", "mp4", "mov",
];

/// Opens a chat attachment (data_dir/attachments/<id>). The stored file has no name, so a
/// copy with its real name goes to the cache first.
#[tauri::command]
fn open_attachment(id: String, name: String) -> Result<(), String> {
    if id.is_empty() || !id.chars().all(|c| c.is_ascii_hexdigit() || c == '-') {
        return Err("bad attachment id".into());
    }
    let file_name = std::path::Path::new(&name)
        .file_name()
        .ok_or("bad attachment name")?
        .to_owned();
    let source = data_dir().join("attachments").join(&id);
    let home = std::env::var_os("HOME").map(PathBuf::from).unwrap_or_default();
    let folder = home.join("Library/Caches/Argos/opened").join(&id);
    std::fs::create_dir_all(&folder).map_err(|e| e.to_string())?;
    let target = folder.join(file_name);
    std::fs::copy(&source, &target).map_err(|e| e.to_string())?;
    let openable = target
        .extension()
        .and_then(|e| e.to_str())
        .is_some_and(|e| OPENABLE.contains(&e.to_ascii_lowercase().as_str()));
    let mut open = std::process::Command::new("open");
    if !openable {
        open.arg("-R"); // reveal in Finder
    }
    open.arg(&target).status().map_err(|e| e.to_string())?;
    Ok(())
}
```
Register in `generate_handler![…, open_attachment]`; add `"open_attachment"` to `COMMANDS` in `build.rs`; add `"allow-open-attachment"` to `capabilities/default.json` permissions and extend its description: "…opening a chat attachment in its default app (copied from the app's own data folder)…".

- [ ] **Step 3: Build check**

Run: `cd desktop/src-tauri && cargo check`
Expected: compiles without warnings about the new code.

- [ ] **Step 4: Commit**

```bash
git add desktop/src-tauri
git commit -m "feat(desktop): accept file drops and open chat attachments"
```

---

### Task 10: Docs, bundle check, full verification

**Files:**
- Modify: `docs/PLAN.md` (after Phase 12, ~line 450)
- Modify: `docs/backlog.md`
- Modify: `CLAUDE.md`
- Modify: `desktop/argos-server.spec` (only if the bundle check fails)

- [ ] **Step 1: PLAN Phase 13**

```markdown
### Phase 13. 첨부 파일 (추가 범위, 2026-10-08 사용자 요청)

**목표**: 채팅에 파일과 이미지를 붙이고, 에이전트가 그 내용을 읽는다. 설계는
`docs/superpowers/specs/2026-10-08-chat-attachments-design.md`.

- 넣기: ⌘V 붙여넣기, Finder 드래그 앤 드롭, 📎 버튼. 채널·스레드·DM·빠른 입력 창
- 저장: `data_dir/attachments/<id>`, 파일당 `attachment_max_mb`(기본 25), 메시지당 10개
- 에이전트: 이미지는 각 백엔드의 이미지 입력, 텍스트 파일은 본문에, PDF는 Claude 문서 블록·나머지는 추출 텍스트
- 보안: 이미지 4종만 화면 표시, 나머지는 다운로드, `nosniff`·`CSP: sandbox`

**완료 기준**: 스크린샷을 붙여 @Claude·@Codex·@Hermes·로컬 비전 모델에게 물으면 내용을 답하고, PDF를 붙이면
내용을 요약하며, 데스크톱 앱에서 Finder 드래그와 빠른 입력 창 붙여넣기가 된다.
```

- [ ] **Step 2: Backlog row**

```markdown
| 2026-10-08 | 첨부 파일 백업 | 매일 백업은 DB만 복사해 `data_dir/attachments`의 파일은 빠짐. 복원하면 "파일 없음"으로 보임 | Phase 13 설계 | 대기 |
```
Add it under a fitting section (create `## 기능` with the same table header if no section fits).

- [ ] **Step 3: CLAUDE.md** — under 백엔드 architecture, after the `runner.py` bullet:

```markdown
- **`attachments.py`**: 채팅 첨부(Phase 13). 파일은 `data_dir/attachments/<id>`(이름은 DB에만), 종류는 내용으로
  판별. `Turn.attachments` → `split_attachments`/`with_attachments`(agents.py)가 이미지·PDF 파트와 본문 텍스트로
  나누고 어댑터가 백엔드별 형식으로 보낸다. 미전송 업로드는 서버 시작 때 24시간 지나면 정리.
```

- [ ] **Step 4: Bundle check**

Run: `make app` (long). Then start the built server binary briefly to import the new modules: `desktop/build/dist/argos-server --help 2>&1 | head -5` is not enough — instead run `ARGOS_DATA_DIR=$SCRATCH/appdata ARGOS_PORT=8011 desktop/build/dist/argos-server &` , `curl -s -F file=@<some .png> http://127.0.0.1:8011/api/v1/attachments`, then stop it by its PID (`kill <pid>`, never `pkill`).
Expected: JSON with `"kind":"image"`. If it fails with a missing module (e.g. `pillow_heif`), add `*collect_submodules("pillow_heif")` / `*collect_dynamic_libs("pillow_heif")` to `hidden`/binaries in `argos-server.spec` and rebuild.

- [ ] **Step 5: Full checks**

```bash
make lint && make test
cd backend && uv run alembic check
make api-types && git diff --exit-code frontend/src/api-types.ts
```
Expected: all pass; no api-types diff.

- [ ] **Step 6: Manual end-to-end on a separate stack** (never the user's dev DB)

```bash
cd backend && ARGOS_PORT=8001 ARGOS_DB_PATH=$SCRATCH/e2e.db ARGOS_DATA_DIR=$SCRATCH/e2e uv run alembic upgrade head
cd backend && ARGOS_PORT=8001 ARGOS_DB_PATH=$SCRATCH/e2e.db ARGOS_DATA_DIR=$SCRATCH/e2e uv run uvicorn argos.main:app --port 8001 &
cd frontend && ARGOS_BACKEND=http://127.0.0.1:8001 bunx vite --port 5174 &
```
In a browser at `http://127.0.0.1:5174`: paste a screenshot, drop a PDF, pick a `.py` file; send with `@claude 이 이미지 뭐야`; repeat with `@codex`, `@hermes` and a local vision model (Ollama) if installed; send an attachment-only message; open an image in the lightbox; download a PDF. Note which agents answered from the content. Stop both servers by PID.

- [ ] **Step 7: Commit**

```bash
git add docs/PLAN.md docs/backlog.md CLAUDE.md desktop/argos-server.spec
git commit -m "docs: Phase 13 chat attachments"
```
