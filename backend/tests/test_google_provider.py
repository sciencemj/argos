"""Google (Gemini API) provider: addresses, model list, the connection test, and the
GeminiLLM tool loop against a fake google-genai client."""

import json
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx2
import pytest
from fakes import FakeKeychain
from fastapi.testclient import TestClient
from google.genai import errors as genai_errors
from google.genai import types as gt

from argos import providers
from argos.agents import (
    AgentUnavailable,
    Status,
    Token,
    ToolCall,
    ToolResult,
    Turn,
    build_adapter,
)
from argos.config import Settings
from argos.harness import (
    GeminiLLM,
    HarnessRun,
    HMsg,
    LLMDone,
    NativeHarnessAdapter,
    PendingCall,
    ToolContext,
    ToolDef,
    ToolRegistry,
    gemini_contents,
)
from argos.main import create_app
from argos.models import Agent, AgentBackend
from argos.providers import ProviderConfig

ROOT = "https://generativelanguage.googleapis.com"


def google(**changes: Any) -> ProviderConfig:
    fields: dict[str, Any] = {
        "id": "pg",
        "name": "Gemini",
        "kind": "google",
        "base_url": ROOT,
        "api_key": "AIza-test",
        "locality": "cloud",
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


# --- provider ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        ROOT,
        f"{ROOT}/",
        f"{ROOT}/v1beta",
        f"{ROOT}/v1beta/openai/",
        f"{ROOT}/v1",
    ],
)
def test_google_base_url_is_the_root(url: str) -> None:
    assert providers.normalize_base_url("google", url) == ROOT


def test_google_locality_compat_endpoint_and_client() -> None:
    assert providers.locality_of(ROOT) == "cloud"
    p = google()
    assert providers.openai_base_url(p) == f"{ROOT}/v1beta/openai"
    compat = providers.chat_client(p, timeout=1, max_retries=0)
    assert str(compat.base_url).rstrip("/") == f"{ROOT}/v1beta/openai"
    client = providers.gemini_client(p, timeout=30, max_retries=0)
    assert client._api_client.api_key == "AIza-test"  # pyright: ignore[reportPrivateUsage]
    assert client._api_client.vertexai is False  # pyright: ignore[reportPrivateUsage]


async def test_google_vision_is_true() -> None:
    assert await providers.vision(google(), "gemini-2.5-flash") is True


async def test_list_models_google_filters_and_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = [
        {
            "models": [
                {
                    "name": "models/gemini-2.5-pro",
                    "supportedGenerationMethods": ["generateContent"],
                },
                {
                    "name": "models/text-embedding-004",
                    "supportedGenerationMethods": ["embedContent"],
                },
            ],
            "nextPageToken": "next",
        },
        {
            "models": [
                {
                    "name": "models/gemini-2.5-flash",
                    "supportedGenerationMethods": ["generateContent", "countTokens"],
                }
            ]
        },
    ]
    seen = mock_http(monkeypatch, lambda _: httpx2.Response(200, json=pages[len(seen) - 1]))
    models = await providers.list_models(google())
    assert [m.name for m in models] == ["gemini-2.5-flash", "gemini-2.5-pro"]
    assert seen[0].url.path == "/v1beta/models"
    assert seen[0].headers["x-goog-api-key"] == "AIza-test"
    assert "key=" not in str(seen[0].url)  # the key never rides in the URL
    assert seen[1].url.params["pageToken"] == "next"


async def test_list_models_google_bad_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    error = {"error": {"code": 400, "status": "INVALID_ARGUMENT", "message": "API key not valid"}}
    mock_http(monkeypatch, lambda _: httpx2.Response(400, json=error))
    with pytest.raises(providers.ProviderError):
        await providers.list_models(google())


async def test_list_models_google_odd_answer_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_http(
        monkeypatch,
        lambda _: httpx2.Response(
            200, json={"models": [{"supportedGenerationMethods": ["generateContent"]}]}
        ),
    )
    with pytest.raises(providers.ProviderError, match="unexpected"):
        await providers.list_models(google())


@pytest.fixture
def api(settings: Settings) -> Iterator[tuple[TestClient, FakeKeychain]]:
    keychain = FakeKeychain()
    with TestClient(create_app(settings, provider_keychain=keychain)) as c:
        yield c, keychain


def test_google_provider_api_roundtrip(
    api: tuple[TestClient, FakeKeychain], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, keychain = api
    created = client.post(
        "/api/v1/providers",
        json={
            "name": "Gemini",
            "kind": "google",
            "base_url": f"{ROOT}/v1beta/openai/",
            "api_key": "AIza-1",
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["kind"] == "google" and body["base_url"] == ROOT
    assert body["locality"] == "cloud" and body["has_api_key"] is True
    assert keychain.saved == {body["id"]: "AIza-1"}

    seen = mock_http(
        monkeypatch,
        lambda _: httpx2.Response(
            200,
            json={
                "models": [
                    {"name": "models/gemini-x", "supportedGenerationMethods": ["generateContent"]}
                ]
            },
        ),
    )
    ok = client.post(
        "/api/v1/providers/test",
        json={"kind": "google", "base_url": ROOT, "provider_id": body["id"]},
    ).json()
    assert ok["reachable"] is True and ok["locality"] == "cloud"
    assert [m["name"] for m in ok["models"]] == ["gemini-x"]
    assert seen[0].headers["x-goog-api-key"] == "AIza-1"  # the saved key, same host

    mock_http(monkeypatch, lambda _: httpx2.Response(403, json={"error": {"code": 403}}))
    bad = client.post(
        "/api/v1/providers/test", json={"kind": "google", "base_url": ROOT, "api_key": "x"}
    ).json()
    assert bad["reachable"] is False and bad["error"]


# --- GeminiLLM ----------------------------------------------------------------------------


async def _echo(args: dict[str, Any], ctx: ToolContext) -> str:
    return f"echo {args}"


def tool(name: str) -> ToolDef:
    schema = {"type": "object", "properties": {"p": {"type": "integer"}}}
    return ToolDef(name, f"{name} tool", schema, "read", _echo)


def chunk(*parts: gt.Part) -> gt.GenerateContentResponse:
    content = gt.Content(role="model", parts=list(parts))
    return gt.GenerateContentResponse(candidates=[gt.Candidate(content=content)])


class FakeModels:
    """client.aio.models: plays one scripted list of chunks per call, records requests."""

    def __init__(self, turns: list[list[gt.GenerateContentResponse]]) -> None:
        self.turns = turns
        self.requests: list[dict[str, Any]] = []
        self.stream_error: Exception | None = None

    async def generate_content_stream(
        self, *, model: str, contents: Any, config: Any
    ) -> AsyncIterator[gt.GenerateContentResponse]:
        self.requests.append({"model": model, "contents": contents, "config": config})
        if self.stream_error is not None:
            error, self.stream_error = self.stream_error, None
            raise error
        chunks = self.turns.pop(0)

        async def gen() -> AsyncIterator[gt.GenerateContentResponse]:
            for c in chunks:
                yield c

        return gen()

    async def generate_content(
        self, *, model: str, contents: Any, config: Any
    ) -> gt.GenerateContentResponse:
        self.requests.append({"model": model, "contents": contents, "config": config})
        parts = [p for c in self.turns.pop(0) for p in (c.candidates or [])[0].content.parts]  # type: ignore[union-attr]
        return chunk(*parts)


def fake_client(models: FakeModels) -> Any:
    return SimpleNamespace(aio=SimpleNamespace(models=models))


async def _collect(it: AsyncIterator[Any]) -> list[Any]:
    return [x async for x in it]


async def test_gemini_streams_thoughts_text_and_calls() -> None:
    call = gt.FunctionCall(id="fc1", name="read", args={"p": 2})
    models = FakeModels(
        [
            [
                chunk(gt.Part(text="파일을   살펴보는 중", thought=True)),
                chunk(gt.Part(text="볼게요")),
                chunk(gt.Part(function_call=call, thought_signature=b"sig")),
            ]
        ]
    )
    llm = GeminiLLM(fake_client(models), "gemini-x")
    events = await _collect(llm.turn("sys", [HMsg("user", "hi")], [tool("read")]))
    assert events[0] == Status("생각 중: 파일을 살펴보는 중")
    assert events[1] == Token("볼게요")
    done = events[-1]
    assert isinstance(done, LLMDone) and done.text == "볼게요"
    assert [(c.id, c.name, json.loads(c.raw), c.signature) for c in done.calls] == [
        ("fc1", "read", {"p": 2}, b"sig")
    ]
    config: gt.GenerateContentConfig = models.requests[0]["config"]
    assert config.system_instruction == "sys"
    assert config.automatic_function_calling is not None
    assert config.automatic_function_calling.disable is True
    tools: Any = config.tools
    declared = gt.FunctionDeclaration.model_validate(tools[0].function_declarations[0])
    assert declared.name == "read"
    assert declared.parameters_json_schema == tool("read").parameters
    assert config.thinking_config is not None and config.thinking_config.include_thoughts


async def test_gemini_retries_without_thinking_and_falls_back_to_whole() -> None:
    models = FakeModels([[chunk(gt.Part(text="a"))], [chunk(gt.Part(text="b"))]])
    models.stream_error = genai_errors.ClientError(
        400, {"error": {"message": "Thinking is not supported by this model."}}
    )
    llm = GeminiLLM(fake_client(models), "gemini-old")
    events = await _collect(llm.turn("", [HMsg("user", "hi")], []))
    assert events == [Token("a"), LLMDone("a", [])]
    assert models.requests[1]["config"].thinking_config is None
    # A 5xx on the stream: ask for the answer whole, and keep doing so.
    models.stream_error = genai_errors.ServerError(500, {"error": {"message": "boom"}})
    events = await _collect(llm.turn("", [HMsg("user", "hi")], []))
    assert events == [Token("b"), LLMDone("b", [])]


async def test_gemini_client_error_is_unavailable() -> None:
    models = FakeModels([])
    models.stream_error = genai_errors.ClientError(403, {"error": {"message": "denied"}})
    llm = GeminiLLM(fake_client(models), "gemini-x", unreachable="down")
    with pytest.raises(AgentUnavailable, match="ClientError"):
        await _collect(llm.turn("", [HMsg("user", "hi")], []))


async def test_gemini_connection_error_says_unreachable() -> None:
    models = FakeModels([])
    models.stream_error = httpx2.ConnectError("refused")
    llm = GeminiLLM(fake_client(models), "gemini-x", unreachable="Gemini에 연결할 수 없어요")
    with pytest.raises(AgentUnavailable, match="연결할 수 없어요"):
        await _collect(llm.turn("", [HMsg("user", "hi")], []))


def test_gemini_contents_roles_results_and_signatures() -> None:
    call = PendingCall("fc1", "read", '{"p": 1}', {"p": 1}, signature=b"sig")
    history = [
        HMsg("assistant", "earlier"),
        HMsg("user", "hi"),
        HMsg("assistant", "볼게요", calls=(call,)),
        HMsg("tool", "echo", call_id="fc1", name="read"),
        HMsg("tool", "오류: 없음", call_id="fc2", name="read", is_error=True),
    ]
    contents = gemini_contents(history)
    assert [c.role for c in contents] == ["user", "model", "user", "model", "user"]
    model_turn = contents[3].parts or []
    assert model_turn[0].text == "볼게요"
    fc = model_turn[1].function_call
    assert fc is not None and (fc.id, fc.name, fc.args) == ("fc1", "read", {"p": 1})
    assert model_turn[1].thought_signature == b"sig"
    results = [p.function_response for p in contents[4].parts or []]
    assert [(r.id, r.name, r.response) for r in results if r is not None] == [
        ("fc1", "read", {"output": "echo"}),
        ("fc2", "read", {"error": "오류: 없음"}),
    ]


async def test_gemini_tool_loop_sends_results_back(tmp_path: Path) -> None:
    call = gt.FunctionCall(id="fc1", name="read", args={"p": 3})
    models = FakeModels(
        [
            [chunk(gt.Part(function_call=call, thought_signature=b"s1"))],
            [chunk(gt.Part(text="다 봤어요"))],
        ]
    )
    llm = GeminiLLM(fake_client(models), "gemini-x")
    ctx = ToolContext(workspace=tmp_path, agent="bot")
    adapter = NativeHarnessAdapter(llm, "bot", ToolRegistry([tool("read")]), ctx)
    events = [e async for e in adapter.stream([Turn("user", "읽어줘")], "sys", "s")]
    assert ToolCall("fc1", "read", {"p": 3}) in events
    assert ToolResult("fc1", "read", "echo {'p': 3}", False) in events
    assert events[-1] == Token("다 봤어요")
    second: list[gt.Content] = models.requests[1]["contents"]
    assert [c.role for c in second] == ["user", "model", "user"]
    sent = (second[1].parts or [])[0]
    assert sent.thought_signature == b"s1"
    response = (second[2].parts or [])[0].function_response
    assert response is not None and response.response == {"output": "echo {'p': 3}"}


# --- build_adapter ------------------------------------------------------------------------


def _agent(**fields: Any) -> Agent:
    base: dict[str, Any] = {
        "name": "gem",
        "display_name": "Gem",
        "backend": AgentBackend.NATIVE,
        "model": "gemini-2.5-flash",
        "is_builtin": False,
        "tools_json": None,
        "provider_id": "pg",
        "send_images": False,
    }
    return Agent(**(base | fields))


def test_build_adapter_picks_gemini(settings: Settings) -> None:
    config = providers.with_providers(settings, (google(),))
    adapter = build_adapter(_agent(), config, harness=HarnessRun(run_id="r1"))
    assert isinstance(adapter, NativeHarnessAdapter)
    assert isinstance(adapter._llm, GeminiLLM)  # pyright: ignore[reportPrivateUsage]
    # A local-model agent on a Google provider also runs through the harness.
    local = build_adapter(_agent(backend=AgentBackend.OLLAMA), config)
    assert isinstance(local, NativeHarnessAdapter)
    keyless = providers.with_providers(settings, (google(api_key=None),))
    with pytest.raises(AgentUnavailable, match="API 키"):
        build_adapter(_agent(), keyless)
