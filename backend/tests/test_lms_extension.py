import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from argos import lms_extension
from argos.config import Settings
from argos.main import create_app


@pytest.fixture
def bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    source = tmp_path / "bundle"
    shutil.copytree(lms_extension.source_dir(), source)
    monkeypatch.setattr(lms_extension, "source_dir", lambda: source)
    return source


def test_stable_folder_embeds_revision_and_refreshes_only_existing_installations(
    settings: Settings, bundle: Path
) -> None:
    folder = lms_extension.install_dir(settings.data_dir)
    lms_extension.refresh(settings.data_dir)
    assert not folder.exists()
    first = lms_extension.prepare(settings.data_dir)
    assert lms_extension.installed(settings.data_dir) == first
    assert first["revision"] in (folder / "background.js").read_text()
    unchanged_time = (folder / "background.js").stat().st_mtime_ns
    assert lms_extension.prepare(settings.data_dir) == first
    assert (folder / "background.js").stat().st_mtime_ns == unchanged_time
    # Chrome's path and browser storage remain stable when the bundled code changes.
    (bundle / "collector.js").write_text("// changed collector")
    lms_extension.refresh(settings.data_dir)
    second = lms_extension.installed(settings.data_dir)
    assert second and second["revision"] != first["revision"]
    assert second["permissions"] == first["permissions"]
    assert (folder / "collector.js").read_text() == "// changed collector"
    assert second["revision"] in (folder / "background.js").read_text()
    # A broken package cannot publish a new revision or partially replace files.
    (bundle / "popup.js").unlink()
    lms_extension.refresh(settings.data_dir)
    assert lms_extension.installed(settings.data_dir) == second
    assert (folder / "collector.js").read_text() == "// changed collector"


def test_prepare_access_and_paired_update_with_permission_change(
    client: TestClient, settings: Settings, bundle: Path
) -> None:
    endpoint = "/api/v1/lms/extension/prepare"
    assert client.post(endpoint, headers={"Origin": "https://example.com"}).status_code == 403
    assert not lms_extension.install_dir(settings.data_dir).exists()
    prepared = client.post(endpoint)
    assert prepared.status_code == 200
    assert prepared.json()["extension_path"] == str(lms_extension.install_dir(settings.data_dir))
    first = lms_extension.installed(settings.data_dir)
    assert first
    check = "/api/v1/lms/extension/check"
    body = {"revision": first["revision"], "permissions": first["permissions"]}
    assert client.post(check, json=body).status_code == 401
    token = client.post("/api/v1/lms/connection").json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert client.post(check, json=body, headers=headers).json() == {
        "revision": first["revision"],
        "reload": False,
        "manual_update": False,
    }
    assert client.get("/api/v1/lms/status").json()["extension_seen_at"]
    (bundle / "collector.js").write_text("// new collector")
    client.post(endpoint)
    assert client.post(check, json=body, headers=headers).json()["reload"] is True
    manifest = json.loads((bundle / "manifest.json").read_text())
    manifest["host_permissions"].append("https://new-school.example/*")
    (bundle / "manifest.json").write_text(json.dumps(manifest))
    client.post(endpoint)
    update = client.post(check, json=body, headers=headers).json()
    assert update["manual_update"] is True and update["reload"] is False
    assert client.get("/api/v1/lms/status").json()["extension_manual_update"] is True
    client.delete("/api/v1/lms/connection")
    assert client.post(check, json=body, headers=headers).status_code == 401
    assert client.get("/api/v1/lms/status").json()["extension_seen_at"] is None


def test_prepare_does_not_follow_destination_symlink(settings: Settings, tmp_path: Path) -> None:
    folder = lms_extension.install_dir(settings.data_dir)
    folder.parent.mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    folder.symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(OSError):
        lms_extension.prepare(settings.data_dir)
    assert list(elsewhere.iterdir()) == []


def test_app_start_refreshes_previously_prepared_extension(
    settings: Settings, bundle: Path
) -> None:
    first = lms_extension.prepare(settings.data_dir)
    (bundle / "collector.js").write_text("// updated with Argos")
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/v1/lms/status").json()["extension_version"] == first["version"]
        second = lms_extension.installed(settings.data_dir)
        assert second and second["revision"] != first["revision"]
        assert second["permissions"] == first["permissions"]
