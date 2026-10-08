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


def test_names_are_cleaned_and_nfc() -> None:
    nfd = unicodedata.normalize("NFD", "한글 노트.pdf")
    assert attachments.safe_name(nfd) == "한글 노트.pdf"
    assert attachments.safe_name("../../etc/passwd") == "passwd"
    assert attachments.safe_name("C:\\Users\\me\\a.txt") == "a.txt"
    assert attachments.safe_name("bad\x00\nname.txt") == "badname.txt"
    assert attachments.safe_name("...") == "file"
    long = attachments.safe_name("a" * 500 + ".txt")
    assert len(long) <= 200 and long.endswith(".txt")


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
