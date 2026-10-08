"""Files attached to chat messages (PLAN Phase 13). Stored by id under
data_dir/attachments (never by their own name); the agent half below turns them into
what each backend can read."""

import base64
import io
import mimetypes
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO

from PIL import Image
from pillow_heif import register_heif_opener  # pyright: ignore[reportUnknownVariableType]
from pypdf import PdfReader

if TYPE_CHECKING:
    from argos.models import Attachment

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
        directory / attachment.id,
        attachment.name,
        attachment.mime,
        attachment.kind,
        attachment.size,
    )


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
