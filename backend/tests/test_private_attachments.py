"""Attachments read from memory for private chat (PLAN Phase 21): nothing touches disk."""

import io
import os
import tempfile
from pathlib import Path

from files import png, text_pdf
from PIL import Image

from argos.attachments import ImagePart, PdfPart, TextPart, part_from_bytes


def heic() -> bytes:
    # argos.attachments registers pillow_heif, so Pillow can write HEIF too.
    out = io.BytesIO()
    Image.new("RGB", (40, 30), "blue").save(out, "HEIF", quality=50)
    return out.getvalue()


def test_png_stays_png_when_small() -> None:
    data = png(10, 10)
    part = part_from_bytes("a.png", data)
    assert isinstance(part, ImagePart)
    assert (part.mime, part.data, part.path) == ("image/png", data, None)


def test_big_image_is_shrunk_to_jpeg() -> None:
    part = part_from_bytes("wide.png", png(3000, 100))
    assert isinstance(part, ImagePart) and part.mime == "image/jpeg"
    assert max(Image.open(io.BytesIO(part.data)).size) == 2000


def test_heic_becomes_jpeg() -> None:
    part = part_from_bytes("IMG_0001.HEIC", heic())
    assert isinstance(part, ImagePart) and part.mime == "image/jpeg"
    assert Image.open(io.BytesIO(part.data)).size == (40, 30)


def test_pdf_text_extracted() -> None:
    part = part_from_bytes("a.pdf", text_pdf("secret contract"))
    assert isinstance(part, TextPart)
    assert "[첨부: a.pdf]" in part.text and "secret contract" in part.text


def test_pdf_native() -> None:
    assert isinstance(part_from_bytes("a.pdf", text_pdf("x"), pdf_native=True), PdfPart)


def test_textless_pdf_message() -> None:
    part = part_from_bytes("scan.pdf", text_pdf(""))
    assert isinstance(part, TextPart) and "글자를 찾지 못한 PDF" in part.text


def test_text_file() -> None:
    part = part_from_bytes("memo.md", "# 회의\n예산 3억".encode())
    assert isinstance(part, TextPart) and "예산 3억" in part.text


def test_unknown_file_unreadable_note() -> None:
    part = part_from_bytes("tool.bin", b"\x00\x01\x02" * 400)
    assert isinstance(part, TextPart)
    assert part.text == "[첨부: tool.bin, 1.2 KB — 내용 읽기 불가]"


def test_broken_image_reads_as_failed() -> None:
    part = part_from_bytes("broken.png", png()[:40] + b"garbage")
    assert part == TextPart("[첨부: broken.png — 읽지 못함]")


def _listing(root: Path) -> set[str]:
    return {str(p) for p in root.rglob("*")} if root.exists() else set()


def test_no_disk_writes(tmp_path: Path) -> None:
    temp = Path(tempfile.gettempdir())
    before = _listing(temp), set(os.listdir(tmp_path))
    for name, data in [
        ("a.png", png(3000, 100)),
        ("b.HEIC", heic()),
        ("c.pdf", text_pdf("x")),
        ("d.txt", b"hello"),
    ]:
        part_from_bytes(name, data)
    assert (_listing(temp), set(os.listdir(tmp_path))) == before
