"""Files attached to chat messages (PLAN Phase 13). Stored by id under
data_dir/attachments (never by their own name); the agent half below turns them into
what each backend can read."""

import mimetypes
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from PIL import Image
from pillow_heif import register_heif_opener  # pyright: ignore[reportUnknownVariableType]

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
