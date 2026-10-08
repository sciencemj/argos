import io
import unicodedata
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from files import png, text_pdf
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from argos import attachments, services
from argos.config import Settings
from argos.main import create_app
from argos.models import Attachment


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


# --- API ---------------------------------------------------------------------------------


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
