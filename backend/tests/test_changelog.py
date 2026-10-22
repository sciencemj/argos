"""Which changelog version the user has seen (Phase 19)."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from argos.config import Settings
from argos.main import create_app


@pytest.fixture
def app(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as client:
        yield client


def test_nothing_seen_on_a_new_install(app: TestClient) -> None:
    assert app.get("/api/v1/settings/changelog").json() == {"seen": None}


def test_seen_version_is_kept(app: TestClient) -> None:
    saved = app.put("/api/v1/settings/changelog", json={"seen": "0.1.17"})
    assert saved.json() == {"seen": "0.1.17"}
    assert app.get("/api/v1/settings/changelog").json() == {"seen": "0.1.17"}


@pytest.mark.parametrize("bad", ["", "latest", "0.1", "0.1.17-beta", None])
def test_only_plain_versions_are_accepted(app: TestClient, bad: str | None) -> None:
    assert app.put("/api/v1/settings/changelog", json={"seen": bad}).status_code == 422


def test_an_existing_database_starts_from_the_release_before_the_changelog(
    settings: Settings,
) -> None:
    """Users updating from 0.1.16 (no changelog_seen yet) must see the new notes,
    unlike a new install."""
    with TestClient(create_app(settings)) as first:  # new install
        assert first.get("/api/v1/settings/changelog").json() == {"seen": None}
    with TestClient(create_app(settings)) as again:  # same DB, next start
        assert again.get("/api/v1/settings/changelog").json() == {"seen": "0.1.16"}
        again.put("/api/v1/settings/changelog", json={"seen": "0.1.17"})
    with TestClient(create_app(settings)) as later:
        assert later.get("/api/v1/settings/changelog").json() == {"seen": "0.1.17"}
