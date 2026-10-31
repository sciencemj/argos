from collections.abc import Iterator
from typing import Any, cast

import httpx2
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from argos import classifier, notify, providers, services
from argos.classifier import OllamaModel, OpenAICompatClassifier
from argos.config import Settings
from argos.main import create_app
from argos.providers import ProviderConfig
from argos.summarizer import classifier_chat

HOME = ProviderConfig("home", "이 Mac", "ollama", "http://127.0.0.1:11434/v1", None, "device", True)
GPU = ProviderConfig("gpu", "GPU", "ollama", "http://gpu.test:11434/v1", "k", "network", False)


def config(**changes: Any) -> Settings:
    base = Settings(_env_file=None, classifier_model="m", model_providers=(HOME, GPU))  # pyright: ignore[reportCallIssue]
    return base.model_copy(update=changes)


def client_url(c: Any) -> str:
    return str(c.base_url)


def test_classifier_uses_classifier_provider() -> None:
    clf: Any = classifier.build_classifier(config(classifier_provider_id="gpu"))
    assert client_url(clf._chat._client) == "http://gpu.test:11434/v1/"
    assert clf._chat._client.api_key == "k"
    assert clf._root == "http://gpu.test:11434"


def test_classifier_defaults_to_default_provider() -> None:
    clf: Any = classifier.build_classifier(config())
    assert client_url(clf._chat._client) == "http://127.0.0.1:11434/v1/"


def test_summarizer_and_matcher_follow_provider() -> None:
    chat = classifier_chat(config(classifier_provider_id="gpu"))
    assert chat is not None
    assert client_url(chat[0]) == "http://gpu.test:11434/v1/"


def hermes(**changes: Any) -> Settings:
    return config(
        classifier_provider="hermes",
        classifier_base_url="http://hermes.test/v1",
        classifier_api_key=SecretStr("hk"),
        classifier_provider_id="gpu",
        **changes,
    )


def test_hermes_classifier_unchanged() -> None:
    clf = classifier.build_classifier(hermes())
    assert isinstance(clf, OpenAICompatClassifier)
    raw: Any = clf
    assert client_url(raw._client) == "http://hermes.test/v1/"
    assert raw._client.api_key == "hk"
    chat = classifier_chat(hermes())
    assert chat is not None and client_url(chat[0]) == "http://hermes.test/v1/"


def mock_http(monkeypatch: pytest.MonkeyPatch, module: Any, body: Any) -> list[str]:
    seen: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(str(request.url))
        return httpx2.Response(200, json=body)

    real = httpx2.AsyncClient
    monkeypatch.setattr(
        module,
        "AsyncClient",
        lambda **kw: real(transport=httpx2.MockTransport(handler), **kw),  # pyright: ignore
    )
    return seen


async def test_embed_uses_classifier_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = mock_http(monkeypatch, httpx2, {"models": [{"name": "embeddinggemma:300m"}]})
    created: list[str] = []

    class FakeEmbeddings:
        async def create(self, **_: Any) -> Any:
            return type("R", (), {"data": []})()

    class FakeClient:
        def __init__(self, *, base_url: str, **_: Any) -> None:
            created.append(base_url)
            self.embeddings = FakeEmbeddings()

    monkeypatch.setattr(notify, "AsyncOpenAI", FakeClient)
    await notify._ollama_embed(config(classifier_provider_id="gpu"))(["a"])  # pyright: ignore[reportPrivateUsage]
    assert seen == ["http://gpu.test:11434/api/tags"]
    assert created == ["http://gpu.test:11434/v1"]
    seen.clear()
    created.clear()
    await notify._ollama_embed(hermes())(["a"])  # pyright: ignore[reportPrivateUsage]
    assert created == ["http://hermes.test/v1"]


async def test_list_ollama_models_reads_classifier_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = mock_http(
        monkeypatch, providers.httpx2, {"models": [{"name": "a", "capabilities": ["completion"]}]}
    )
    models = await classifier.list_ollama_models(config(classifier_provider_id="gpu"))
    assert [m.name for m in models] == ["a"]
    assert seen == ["http://gpu.test:11434/api/tags"]


# --- settings API ------------------------------------------------------------------------


@pytest.fixture
def fake_models(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    asked: list[str] = []

    async def fake(settings: Settings, *, decision: bool = False) -> list[OllamaModel]:
        p = providers.classifier_provider(settings)
        asked.append(p.name)
        return [OllamaModel(f"{p.name}-model", remote=False, parameter_size=None)]

    monkeypatch.setattr(classifier, "list_ollama_models", fake)
    return asked


@pytest.fixture
def app(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as c:
        yield c


def add_provider(client: TestClient, kind: str = "ollama", name: str = "GPU") -> str:
    state: Any = cast(Any, client.app).state
    url = "http://gpu.test:11434" if kind == "ollama" else "https://api.example.test/v1"

    async def make() -> str:
        async with state.sessionmaker() as session:
            p = await services.create_provider(
                session, {"name": name, "kind": kind, "base_url": url}, "user"
            )
            loaded = await providers.load_providers(session, state.provider_keychain)
        state.base_settings = providers.with_providers(state.base_settings, loaded)
        state.settings = providers.with_providers(state.settings, loaded)
        return p.id

    portal: Any = cast(Any, client).portal
    return portal.call(make)


def test_put_classifier_provider_switch(app: TestClient, fake_models: list[str]) -> None:
    gpu = add_provider(app)
    r = app.put("/api/v1/settings/classifier", json={"model": "GPU-model", "provider_id": gpu})
    assert r.status_code == 200, r.text
    assert r.json()["provider_id"] == gpu
    assert fake_models == ["GPU"]
    state: Any = cast(Any, app.app).state
    assert providers.classifier_provider(state.settings).name == "GPU"
    assert providers.classifier_provider(state.runner.settings).name == "GPU"


def test_put_classifier_rejects_openai_provider(app: TestClient, fake_models: list[str]) -> None:
    cloud = add_provider(app, "openai", "Cloud")
    r = app.put("/api/v1/settings/classifier", json={"model": "x", "provider_id": cloud})
    assert r.status_code == 422
    assert "인박스 분류는 Ollama 연결만 쓸 수 있어요" in r.text


def test_classifier_models_for_provider(app: TestClient, fake_models: list[str]) -> None:
    gpu = add_provider(app)
    body = app.get("/api/v1/settings/classifier/models", params={"provider_id": gpu}).json()
    assert [m["name"] for m in body["models"]] == ["GPU-model"]
    body = app.get("/api/v1/settings/classifier/models").json()
    assert [m["name"] for m in body["models"]] == ["이 Mac-model"]


def test_classifier_settings_report_provider(app: TestClient) -> None:
    body = app.get("/api/v1/settings/classifier").json()
    state: Any = cast(Any, app.app).state
    assert body["provider_id"] == providers.classifier_provider(state.settings).id
