"""Local-model agents run on the provider they chose (PLAN Phase 20)."""

from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, cast

import httpx2
import openai
import pytest

from argos import providers
from argos.agents import (
    AgentUnavailable,
    LLMToolAdapter,
    OpenAICompatAdapter,
    Turn,
    build_adapter,
)
from argos.config import Settings
from argos.models import Agent, AgentBackend
from argos.providers import ProviderConfig

DEFAULT = ProviderConfig(
    "p-default", "이 Mac", "ollama", "http://127.0.0.1:11434/v1", None, "device", True
)
GPU = ProviderConfig("p-gpu", "GPU", "ollama", "http://gpu.test:11434/v1", None, "network", False)
CLOUD = ProviderConfig(
    "p-cloud", "Cloud", "openai", "https://api.cloud.test/v1", "sk-test", "cloud", False
)


def with_providers(settings: Settings, **changes: Any) -> Settings:
    return settings.model_copy(update={"model_providers": (DEFAULT, GPU, CLOUD), **changes})


def custom(provider_id: str | None, send_images: bool = False) -> Agent:
    return Agent(
        name="helper",
        display_name="Helper",
        backend=AgentBackend.OLLAMA,
        model="gemma4:e4b",
        provider_id=provider_id,
        send_images=send_images,
        is_builtin=False,
    )


def builtin_local() -> Agent:
    return Agent(
        name="local",
        display_name="Local",
        backend=AgentBackend.OLLAMA,
        model="gemma4:e4b",
        is_builtin=True,
    )


def client_of(adapter: object) -> Any:
    return cast(Any, adapter)._client


def test_custom_agent_uses_its_provider(settings: Settings) -> None:
    adapter = build_adapter(custom(GPU.id), with_providers(settings))
    assert str(client_of(adapter).base_url) == "http://gpu.test:11434/v1/"


def test_agent_without_provider_uses_default(settings: Settings) -> None:
    adapter = build_adapter(custom(None), with_providers(settings))
    assert str(client_of(adapter).base_url) == "http://127.0.0.1:11434/v1/"


def test_unknown_provider_id_uses_default(settings: Settings) -> None:
    adapter = build_adapter(custom("deleted"), with_providers(settings))
    assert str(client_of(adapter).base_url) == "http://127.0.0.1:11434/v1/"


def test_builtin_local_follows_classifier_provider(settings: Settings) -> None:
    config = with_providers(settings, classifier_provider_id=GPU.id)
    adapter = build_adapter(builtin_local(), config)
    assert str(client_of(adapter).base_url) == "http://gpu.test:11434/v1/"


def test_builtin_local_with_hermes_classifier_uses_default(settings: Settings) -> None:
    config = with_providers(settings, classifier_provider="hermes", classifier_provider_id=GPU.id)
    adapter = build_adapter(builtin_local(), config)
    assert str(client_of(adapter).base_url) == "http://127.0.0.1:11434/v1/"


def test_ollama_kind_turns_reasoning_off(settings: Settings) -> None:
    adapter = build_adapter(custom(GPU.id), with_providers(settings))
    assert cast(Any, adapter)._extra == {"reasoning_effort": "none"}


def test_openai_kind_sends_key_and_no_reasoning_effort(settings: Settings) -> None:
    adapter = build_adapter(custom(CLOUD.id), with_providers(settings))
    assert client_of(adapter).api_key == "sk-test"
    assert cast(Any, adapter)._extra is None


async def test_openai_kind_images_follow_send_images(settings: Settings) -> None:
    config = with_providers(settings)
    off = cast(Any, build_adapter(custom(CLOUD.id, send_images=False), config))
    assert await off._vision() is False
    on = cast(Any, build_adapter(custom(CLOUD.id, send_images=True), config))
    assert on._vision is None


def test_agent_follows_provider_after_rename(settings: Settings) -> None:
    moved = ProviderConfig(GPU.id, "새 GPU", "ollama", "http://gpu2.test:11434/v1", None,
                           "network", False)  # fmt: skip
    config = settings.model_copy(update={"model_providers": (DEFAULT, moved)})
    adapter = build_adapter(custom(GPU.id), config)
    assert str(client_of(adapter).base_url) == "http://gpu2.test:11434/v1/"


# --- unreachable servers ------------------------------------------------------------------


def _connection_error() -> openai.APIConnectionError:
    return openai.APIConnectionError(request=httpx2.Request("POST", "http://gpu.test"))


def _failing_client() -> Any:
    async def create(**_: Any) -> Any:
        raise _connection_error()

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


class _BreakingStream:
    """A stream that fails after the request was accepted."""

    def __aiter__(self) -> AsyncIterator[Any]:
        return self

    async def __anext__(self) -> Any:
        raise _connection_error()

    async def close(self) -> None:
        pass


def _breaking_client() -> Any:
    async def create(**_: Any) -> Any:
        return _BreakingStream()

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


class _NoTools:
    async def list_tools(self) -> list[dict[str, Any]]:
        return []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        return ""


@asynccontextmanager
async def _fake_mcp(url: str) -> AsyncGenerator[_NoTools]:
    yield _NoTools()


EXPECTED = "GPU에 연결할 수 없어요 (http://gpu.test:11434/v1)"


async def _drain(adapter: Any) -> None:
    [e async for e in adapter.stream([Turn("user", "안녕")], "ctx", "s")]


async def test_unreachable_provider_message() -> None:
    adapter = OpenAICompatAdapter(
        _failing_client(), "m", "helper", unreachable=providers.unreachable_text(GPU)
    )
    with pytest.raises(AgentUnavailable) as caught:
        await _drain(adapter)
    assert str(caught.value) == EXPECTED


async def test_unreachable_mid_stream_message() -> None:
    adapter = OpenAICompatAdapter(
        _breaking_client(), "m", "helper", unreachable=providers.unreachable_text(GPU)
    )
    with pytest.raises(AgentUnavailable) as caught:
        await _drain(adapter)
    assert str(caught.value) == EXPECTED


async def test_unreachable_provider_message_with_tools() -> None:
    adapter = LLMToolAdapter(
        _failing_client(),
        "m",
        "helper",
        "http://mcp.test",
        ["list_tasks"],
        connect=_fake_mcp,
        unreachable=providers.unreachable_text(GPU),
    )
    with pytest.raises(AgentUnavailable) as caught:
        await _drain(adapter)
    assert str(caught.value) == EXPECTED
