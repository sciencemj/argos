"""Which agents allow private chat (PLAN Phase 21)."""

from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient

from argos import agents, providers
from argos.config import Settings
from argos.models import Agent, AgentBackend
from argos.providers import ProviderConfig

HOME = ProviderConfig(
    "p-home", "이 Mac", "ollama", "http://127.0.0.1:11434/v1", None, "device", True
)
GPU = ProviderConfig("p-gpu", "GPU", "ollama", "http://gpu.test:11434/v1", None, "network", False)
CLOUD = ProviderConfig(
    "p-cloud", "Router", "openai", "https://api.example.test/v1", "sk", "cloud", False
)


def config(settings: Settings, **changes: Any) -> Settings:
    return settings.model_copy(
        update={"model_providers": (HOME, GPU, CLOUD), "classifier_model": "small:1b", **changes}
    )


def agent(
    backend: AgentBackend = AgentBackend.OLLAMA,
    provider_id: str | None = None,
    model: str | None = "gemma4:e4b",
    builtin: bool = False,
) -> Agent:
    return Agent(
        name="local" if builtin else "gemma",
        display_name="G",
        backend=backend,
        model=model,
        provider_id=provider_id,
        send_images=False,
        is_builtin=builtin,
        tools_json=[],
    )


@pytest.mark.parametrize(
    ("name", "cloud"),
    [
        ("gemma4:e4b", False),
        ("qwen3.5:9b", False),
        ("gpt-oss:120b-cloud", True),
        ("glm-4.6:cloud", True),
        ("deepseek-v3.1:671b-cloud", True),
        ("my-cloudy-model:7b", False),
        ("cloud", False),
    ],
)
def test_cloud_model_name(name: str, cloud: bool) -> None:
    assert agents.cloud_model_name(name) is cloud


def test_private_allowed(settings: Settings) -> None:
    s = config(settings)
    assert agents.private_allowed(agent(), s)  # default provider, this Mac
    assert agents.private_allowed(agent(provider_id="p-gpu"), s)
    assert not agents.private_allowed(agent(provider_id="p-cloud"), s)
    assert not agents.private_allowed(agent(model="gpt-oss:120b-cloud"), s)
    assert not agents.private_allowed(agent(model="glm-4.6:cloud"), s)
    assert not agents.private_allowed(agent(AgentBackend.CLAUDE_CODE), s)
    assert not agents.private_allowed(agent(AgentBackend.HERMES), s)


def test_builtin_local_follows_classifier_provider(settings: Settings) -> None:
    on_gpu = config(settings, classifier_provider_id="p-gpu")
    assert agents.local_target(agent(builtin=True, model=None), on_gpu) == (GPU, "small:1b")
    assert agents.private_allowed(agent(builtin=True, model=None), on_gpu)
    on_cloud = config(settings, classifier_provider_id="p-cloud")
    assert not agents.private_allowed(agent(builtin=True, model=None), on_cloud)


def test_no_model_is_not_allowed(settings: Settings) -> None:
    s = config(settings, classifier_model="", classifier_title_model="")
    assert not agents.private_allowed(agent(model=None), s)


REAL_CLIENT = httpx2.AsyncClient


def _mock_show(monkeypatch: pytest.MonkeyPatch, status: int, body: dict[str, Any]) -> None:
    def answer(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status, json=body)

    real = REAL_CLIENT
    monkeypatch.setattr(
        providers.httpx2,
        "AsyncClient",
        lambda **kw: real(transport=httpx2.MockTransport(answer), **kw),  # pyright: ignore
    )


async def test_remote_host(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_show(monkeypatch, 200, {"remote_host": "https://ollama.com:443"})
    assert await providers.remote_host(GPU, "renamed:7b") == "https://ollama.com:443"
    _mock_show(monkeypatch, 200, {"capabilities": ["completion"]})
    assert await providers.remote_host(GPU, "gemma4:e4b") is None
    _mock_show(monkeypatch, 500, {})
    with pytest.raises(providers.ProviderError):
        await providers.remote_host(GPU, "gemma4:e4b")


def test_agent_list_reports_private_ok(client: TestClient) -> None:
    made = client.post(
        "/api/v1/agents",
        json={"name": "gemma", "display_name": "G", "backend": "ollama", "model": "gemma4:e4b"},
    )
    assert made.json()["private_ok"] is True
    cloud = client.post(
        "/api/v1/agents",
        json={
            "name": "big",
            "display_name": "B",
            "backend": "ollama",
            "model": "gpt-oss:20b-cloud",
        },
    )
    assert cloud.json()["private_ok"] is False
    listed = {a["name"]: a["private_ok"] for a in client.get("/api/v1/agents").json()}
    assert (listed["gemma"], listed["big"], listed["claude"]) == (True, False, False)
