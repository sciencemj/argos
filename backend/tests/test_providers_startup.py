from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest
from fakes import FakeKeychain
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from argos import providers
from argos.config import Settings
from argos.main import create_app
from argos.models import ModelProvider


def start(settings: Settings, keychain: FakeKeychain) -> Iterator[TestClient]:
    with TestClient(create_app(settings, provider_keychain=keychain)) as client:
        yield client


async def rows(client: TestClient) -> list[ModelProvider]:
    state: Any = cast(Any, client.app).state
    session: AsyncSession
    async with state.sessionmaker() as session:
        return list((await session.scalars(select(ModelProvider))).all())


def with_env(settings: Settings, **changes: Any) -> Settings:
    return settings.model_copy(update=changes)


async def test_startup_creates_default_from_env(settings: Settings) -> None:
    keychain = FakeKeychain()
    config = with_env(
        settings, classifier_base_url="http://gpu.test:11434/v1", classifier_api_key=SecretStr("k1")
    )
    for client in start(config, keychain):
        [p] = await rows(client)
        assert (p.name, p.kind, p.base_url) == ("이 Mac", "ollama", "http://gpu.test:11434/v1")
        assert p.is_default and p.has_api_key
        assert keychain.saved == {p.id: "k1"}


async def test_startup_hermes_does_not_copy_key(settings: Settings) -> None:
    keychain = FakeKeychain()
    config = with_env(
        settings,
        classifier_provider="hermes",
        classifier_base_url="http://hermes.test/v1",
        classifier_api_key=SecretStr("hk"),
    )
    for client in start(config, keychain):
        [p] = await rows(client)
        assert p.base_url == "http://127.0.0.1:11434/v1"
        assert p.has_api_key is False
        assert keychain.saved == {}


async def test_second_start_creates_nothing(settings: Settings) -> None:
    for _ in start(settings, FakeKeychain()):
        pass
    for client in start(settings, FakeKeychain()):
        assert len(await rows(client)) == 1


def test_startup_loads_snapshot(settings: Settings) -> None:
    keychain = FakeKeychain()
    config = with_env(settings, classifier_api_key=SecretStr("k1"))
    for client in start(config, keychain):
        state: Any = cast(Any, client.app).state
        settings_now: Settings = state.settings
        [p] = settings_now.model_providers
        assert (p.name, p.locality, p.is_default, p.api_key) == ("이 Mac", "device", True, "k1")
        base: Settings = state.base_settings
        runner_settings: Settings = state.runner.settings
        assert base.model_providers == settings_now.model_providers
        assert runner_settings.model_providers == settings_now.model_providers


def test_startup_does_not_touch_network(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(**_: Any) -> Any:
        raise AssertionError("no network at startup")

    monkeypatch.setattr(providers.httpx2, "AsyncClient", boom)
    for client in start(settings, FakeKeychain()):
        assert client.get("/api/v1/agents").status_code == 200


def test_classifier_provider_fallback_without_snapshot(tmp_path: Path) -> None:
    config = Settings(_env_file=None, classifier_base_url="http://x.test/v1")  # pyright: ignore[reportCallIssue]
    p = providers.classifier_provider(config)
    assert (p.base_url, p.is_default, p.locality) == ("http://x.test/v1", True, "cloud")
    assert providers.classifier_provider(Settings(_env_file=None)).base_url == (  # pyright: ignore[reportCallIssue]
        "http://127.0.0.1:11434/v1"
    )


def test_resolve_falls_back_to_default() -> None:
    a = providers.ProviderConfig(
        "a", "이 Mac", "ollama", "http://127.0.0.1:11434/v1", None, "device", True
    )
    b = providers.ProviderConfig(
        "b", "GPU", "ollama", "http://gpu.test:11434/v1", None, "network", False
    )
    config = Settings(_env_file=None, model_providers=(a, b))  # pyright: ignore[reportCallIssue]
    assert providers.resolve(config, "b") == b
    assert providers.resolve(config, None) == a
    assert providers.resolve(config, "gone") == a
    assert providers.classifier_provider(config) == a
    assert (
        providers.classifier_provider(config.model_copy(update={"classifier_provider_id": "b"}))
        == b
    )
