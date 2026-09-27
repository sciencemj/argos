from collections.abc import Iterator
from typing import Any, cast

import httpx2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from argos import classifier
from argos.classifier import ClassifierError, OllamaModel, list_ollama_models
from argos.config import Settings
from argos.main import create_app

INSTALLED = [
    OllamaModel("qwen3.5:9b-mlx", remote=False, parameter_size=None),
    OllamaModel("gpt-oss:20b-cloud", remote=True, parameter_size="20.9B"),
]


@pytest.fixture
def ollama(monkeypatch: pytest.MonkeyPatch) -> list[OllamaModel]:
    installed = list(INSTALLED)

    async def fake(settings: Settings) -> list[OllamaModel]:
        return installed

    monkeypatch.setattr(classifier, "list_ollama_models", fake)
    return installed


def app_client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as client:
        yield client


def test_lists_installed_models(client: TestClient, ollama: list[OllamaModel]) -> None:
    body = client.get("/api/v1/settings/classifier/models").json()
    assert body["reachable"] is True
    assert [(m["name"], m["remote"]) for m in body["models"]] == [
        ("qwen3.5:9b-mlx", False),
        ("gpt-oss:20b-cloud", True),
    ]


def test_unreachable_ollama(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def down(settings: Settings) -> list[OllamaModel]:
        raise ClassifierError("connection refused")

    monkeypatch.setattr(classifier, "list_ollama_models", down)
    body = client.get("/api/v1/settings/classifier/models").json()
    assert body["reachable"] is False
    assert "Ollama" in body["error"]


def test_pick_model_persists_across_restart(settings: Settings, ollama: Any) -> None:
    for client in app_client(settings):
        assert client.get("/api/v1/settings/classifier").json()["source"] == "none"
        picked = client.put("/api/v1/settings/classifier", json={"model": "qwen3.5:9b-mlx"})
        assert picked.json() == {
            "provider": "ollama",
            "model": "qwen3.5:9b-mlx",
            "source": "app",
            "enabled": True,
        }
        assert client.get("/api/v1/config").json()["classifier_enabled"] is True

    for client in app_client(settings):  # restart: the choice comes back from the DB
        current = client.get("/api/v1/settings/classifier").json()
        assert (current["model"], current["enabled"]) == ("qwen3.5:9b-mlx", True)
        off = client.put("/api/v1/settings/classifier", json={"model": None}).json()
        assert (off["source"], off["enabled"]) == ("none", False)

    env_model = settings.model_copy(update={"classifier_model": "qwen3.5:9b-mlx"})
    for client in app_client(env_model):  # "off" in the app beats a model in .env
        assert client.get("/api/v1/settings/classifier").json()["enabled"] is False


def test_language_persists_across_restart(settings: Settings) -> None:
    for client in app_client(settings):
        response = client.put("/api/v1/settings/language", json={"language": "en"})
        assert response.status_code == 200
        app = cast(FastAPI, client.app)
        assert cast(Settings, app.state.settings).language == "en"
        assert cast(Settings, app.state.runner.settings).language == "en"

    for client in app_client(settings):
        app = cast(FastAPI, client.app)
        assert cast(Settings, app.state.settings).language == "en"
        assert client.put("/api/v1/settings/language", json={"language": "fr"}).status_code == 422


def test_env_model_is_reported(settings: Settings) -> None:
    env_model = settings.model_copy(update={"classifier_model": "qwen3.5:9b-mlx"})
    for client in app_client(env_model):
        assert client.get("/api/v1/settings/classifier").json()["source"] == "env"


def test_rejects_models_not_installed(client: TestClient, ollama: Any) -> None:
    bad = client.put("/api/v1/settings/classifier", json={"model": "llama9:404b"})
    assert bad.status_code == 422
    assert client.get("/api/v1/settings/classifier").json()["enabled"] is False


async def test_list_ollama_models_filters_embedding_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tags: dict[str, Any] = {
        "models": [
            {
                "name": "gpt-oss:20b-cloud",
                "remote_host": "https://ollama.com:443",
                "capabilities": ["completion"],
                "details": {"parameter_size": "20.9B"},
            },
            {"name": "embeddinggemma:300m", "capabilities": ["embedding"], "details": {}},
            {"name": "qwen3.5:9b-mlx", "capabilities": ["completion", "thinking"], "details": {}},
            {"name": "old-ollama-model", "details": {}},  # no capabilities field: keep
        ]
    }
    seen: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(str(request.url))
        return httpx2.Response(200, json=tags)

    real = httpx2.AsyncClient
    monkeypatch.setattr(
        classifier.httpx2,
        "AsyncClient",
        lambda **kw: real(transport=httpx2.MockTransport(handler), **kw),  # pyright: ignore
    )
    models = await list_ollama_models(Settings(classifier_base_url="http://ollama.test/v1"))
    assert [m.name for m in models] == ["old-ollama-model", "qwen3.5:9b-mlx", "gpt-oss:20b-cloud"]
    assert seen == ["http://ollama.test/api/tags"]
