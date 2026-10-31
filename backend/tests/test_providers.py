from collections.abc import Callable
from typing import Any

import httpx2
import pytest

from argos import providers
from argos.providers import ProviderConfig


def cfg(**changes: Any) -> ProviderConfig:
    fields: dict[str, Any] = {
        "id": "p1",
        "name": "GPU",
        "kind": "ollama",
        "base_url": "http://gpu.test:11434/v1",
        "api_key": None,
        "locality": "network",
        "is_default": False,
    }
    return ProviderConfig(**(fields | changes))


def mock_http(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx2.Request], httpx2.Response]
) -> list[httpx2.Request]:
    seen: list[httpx2.Request] = []

    def record(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return handler(request)

    real = httpx2.AsyncClient
    monkeypatch.setattr(
        providers.httpx2,
        "AsyncClient",
        lambda **kw: real(transport=httpx2.MockTransport(record), **kw),  # pyright: ignore
    )
    return seen


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://127.0.0.1:11434/v1", "device"),
        ("http://localhost:1234/v1", "device"),
        ("http://[::1]:11434/v1", "device"),
        ("http://192.168.0.5:11434/v1", "network"),
        ("http://10.1.2.3/v1", "network"),
        ("http://172.20.0.1/v1", "network"),
        ("http://100.100.1.1:11434/v1", "network"),
        ("https://box.tail1234.ts.net/v1", "network"),
        ("http://gpu.local:11434/v1", "network"),
        ("https://openrouter.ai/api/v1", "cloud"),
        ("http://203.0.113.9/v1", "cloud"),
        ("http://172.32.0.1/v1", "cloud"),
    ],
)
def test_locality_of(url: str, expected: str) -> None:
    assert providers.locality_of(url) == expected


def test_effective_locality_only_gets_stricter() -> None:
    assert providers.effective_locality("network", "cloud") == "cloud"
    assert providers.effective_locality("cloud", "device") == "cloud"
    assert providers.effective_locality("device", None) == "device"


@pytest.mark.parametrize(
    ("kind", "url", "expected"),
    [
        ("ollama", "http://gpu.test:11434", "http://gpu.test:11434/v1"),
        ("ollama", "http://gpu.test:11434/v1/", "http://gpu.test:11434/v1"),
        ("openai", "https://api.example.test/api/v1/", "https://api.example.test/api/v1"),
        ("openai", " http://lm.test:1234/v1 ", "http://lm.test:1234/v1"),
    ],
)
def test_normalize_base_url(kind: providers.ProviderKind, url: str, expected: str) -> None:
    assert providers.normalize_base_url(kind, url) == expected


@pytest.mark.parametrize("url", ["ftp://x.test/v1", "gpu.test:11434", "", "http://"])
def test_normalize_base_url_rejects(url: str) -> None:
    with pytest.raises(ValueError):
        providers.normalize_base_url("ollama", url)


async def test_list_models_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    tags: dict[str, Any] = {
        "models": [
            {"name": "zeta:7b", "capabilities": ["completion"], "details": {}},
            {"name": "embeddinggemma:300m", "capabilities": ["embedding"], "details": {}},
            {"name": "nimble:9b", "capabilities": ["decision"], "details": {}},
            {
                "name": "big:120b-cloud",
                "remote_host": "https://ollama.com:443",
                "capabilities": ["completion"],
                "details": {"parameter_size": "120B"},
            },
            {"name": "alpha:4b", "capabilities": ["completion"], "details": {}},
        ]
    }
    seen = mock_http(monkeypatch, lambda _: httpx2.Response(200, json=tags))
    models = await providers.list_models(cfg())
    assert [(m.name, m.remote) for m in models] == [
        ("alpha:4b", False),
        ("zeta:7b", False),
        ("big:120b-cloud", True),
    ]
    assert models[2].parameter_size == "120B"
    assert [str(r.url) for r in seen] == ["http://gpu.test:11434/api/tags"]
    with_decision = await providers.list_models(cfg(), decision=True)
    assert [m.name for m in with_decision if m.decision] == ["nimble:9b"]


async def test_list_models_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = mock_http(
        monkeypatch, lambda _: httpx2.Response(200, json={"data": [{"id": "b"}, {"id": "a"}]})
    )
    p = cfg(kind="openai", base_url="https://api.example.test/v1", api_key="sk-test")
    models = await providers.list_models(p)
    assert [(m.name, m.remote) for m in models] == [("a", False), ("b", False)]
    assert str(seen[0].url) == "https://api.example.test/v1/models"
    assert seen[0].headers["Authorization"] == "Bearer sk-test"


async def test_list_models_unreachable_raises_provider_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def down(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    mock_http(monkeypatch, down)
    with pytest.raises(providers.ProviderError):
        await providers.list_models(cfg())


async def test_list_models_does_not_follow_redirects(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = mock_http(
        monkeypatch,
        lambda _: httpx2.Response(302, headers={"Location": "http://elsewhere.test/api/tags"}),
    )
    with pytest.raises(providers.ProviderError):
        await providers.list_models(cfg())
    assert len(seen) == 1


async def test_vision_ollama_reads_capabilities(monkeypatch: pytest.MonkeyPatch) -> None:
    caps = {"seeing:4b": ["completion", "vision"], "blind:4b": ["completion"]}
    mock_http(
        monkeypatch,
        lambda r: httpx2.Response(200, json={"capabilities": caps[_model(r)]}),
    )
    p = cfg(base_url="http://vision.test:11434/v1")
    assert await providers.vision(p, "seeing:4b") is True
    assert await providers.vision(p, "blind:4b") is False


async def test_vision_ollama_failure_lets_server_decide(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_http(monkeypatch, lambda _: httpx2.Response(500))
    assert await providers.vision(cfg(base_url="http://broken.test:11434/v1"), "m") is True


async def test_vision_openai_is_false() -> None:
    assert await providers.vision(cfg(kind="openai"), "gpt") is False


def test_provider_config_repr_hides_key() -> None:
    assert "sk-test" not in repr(cfg(api_key="sk-test"))


def test_chat_client_uses_provider() -> None:
    client = providers.chat_client(cfg(api_key="sk-test"), timeout=5, max_retries=0)
    assert str(client.base_url) == "http://gpu.test:11434/v1/"
    assert client.api_key == "sk-test"


def test_unreachable_text() -> None:
    assert providers.unreachable_text(cfg()) == "GPU에 연결할 수 없어요 (http://gpu.test:11434/v1)"


def _model(request: httpx2.Request) -> str:
    import json

    return json.loads(request.content)["model"]
