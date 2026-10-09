"""The app owns a stable unpacked-extension folder; Chrome owns its registration."""

import hashlib
import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import TypedDict, cast

FILES = ("collector.js", "background.js", "popup.js", "popup.html", "popup.css", "manifest.json")
BUILD_FILE = "argos-build.json"


class ExtensionBuild(TypedDict):
    revision: str
    permissions: str
    version: str


def source_dir() -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[3]))
    return root / "integrations" / "learningx-extension"


def install_dir(data_dir: Path) -> Path:
    return data_dir.resolve() / "browser-extensions" / "learningx"


def installed(data_dir: Path) -> ExtensionBuild | None:
    try:
        folder = install_dir(data_dir)
        raw: object = json.loads((folder / BUILD_FILE).read_text())
        if not isinstance(raw, dict):
            return None
        value = cast(dict[str, object], raw)
        revision, permissions, version = (
            value.get(key) for key in ("revision", "permissions", "version")
        )
        if isinstance(revision, str) and isinstance(permissions, str) and isinstance(version, str):
            build = ExtensionBuild(
                revision=revision,
                permissions=permissions,
                version=version,
            )
            # Do not trigger reloads into a partially replaced installation.
            marker = "const PACKAGED_BUILD = " + json.dumps(build) + "; // ARGOS_BUILD"
            return build if marker in (folder / "background.js").read_text() else None
        return None
    except (OSError, ValueError):
        return None


def prepare(data_dir: Path) -> ExtensionBuild:
    """Snapshot all bundled files first; publish the revision only after replacement.

    Never replace the folder itself: Chrome registers its absolute path. Nothing is
    fetched from the network, and credentials live in Chrome storage, not this folder.
    """
    files = {name: (source_dir() / name).read_bytes() for name in FILES}
    manifest = json.loads(files["manifest.json"])
    permissions = {
        key: manifest.get(key)
        for key in ("manifest_version", "minimum_chrome_version", "permissions", "host_permissions")
    }
    build: ExtensionBuild = {
        "revision": hashlib.sha256(
            b"".join(name.encode() + b"\0" + content + b"\0" for name, content in files.items())
        ).hexdigest(),
        "permissions": hashlib.sha256(json.dumps(permissions, sort_keys=True).encode()).hexdigest(),
        "version": manifest["version"],
    }
    marker = b"const PACKAGED_BUILD = null; // ARGOS_BUILD"
    if files["background.js"].count(marker) != 1:
        raise ValueError("Missing extension build marker")
    files["background.js"] = files["background.js"].replace(
        marker, b"const PACKAGED_BUILD = " + json.dumps(build).encode() + b"; // ARGOS_BUILD"
    )
    destination = install_dir(data_dir)
    if destination.is_symlink() or destination.parent.is_symlink():
        raise OSError("확장 프로그램 폴더에 심볼릭 링크를 사용할 수 없어요")
    destination.mkdir(parents=True, exist_ok=True)
    files[BUILD_FILE] = json.dumps(build).encode()
    with tempfile.TemporaryDirectory(dir=destination.parent) as staging:
        for name, content in files.items():
            (Path(staging) / name).write_bytes(content)
        # Keep already loaded modules intact on a no-op app restart.
        for name, content in files.items():
            target = destination / name
            if target.is_symlink() or not target.is_file() or target.read_bytes() != content:
                os.replace(Path(staging) / name, target)
    return build


def refresh(data_dir: Path) -> None:
    # Starting Argos updates an existing installation, never opts a new user in.
    if (install_dir(data_dir) / BUILD_FILE).is_file():
        try:
            prepare(data_dir)
        except (OSError, ValueError):
            logging.getLogger(__name__).exception("could not refresh LearningX extension")
