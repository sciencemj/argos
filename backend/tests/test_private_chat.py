"""Private chat (PLAN Phase 21): answers stream from a local model and Argos keeps no
record — no rows, no files (not even temporary ones), no log lines with the content."""

import base64
import json
import logging
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import httpx2
import openai
import pytest
from fastapi.testclient import TestClient
from files import png, text_pdf
from sqlalchemy import func, select

from argos import providers
from argos.config import Settings
from argos.db import Base
from argos.main import create_app

SECRET = "secret contract 123"
GEMMA = {"name": "gemma", "display_name": "Gemma", "backend": "ollama", "model": "gemma4:e4b"}


@pytest.fixture
def temp_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The process's temporary folder, so a spooled upload would show up here."""
    folder = tmp_path / "system-tmp"
    folder.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(folder))
    return folder


@pytest.fixture
def model(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """A fake local model: records each request, answers in tokens."""
    seen: dict[str, Any] = {"requests": [], "tokens": ["비밀 ", "답이에요"], "fail": None}

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen["requests"].append(json.loads(request.content))
        if seen["fail"]:
            raise httpx2.ConnectError("refused", request=request)
        body = ""
        for token in seen["tokens"]:
            delta = {"index": 0, "delta": {"content": token}, "finish_reason": None}
            chunk = {"id": "1", "object": "chat.completion.chunk", "created": 0, "model": "m",
                     "choices": [delta]}  # fmt: skip
            body += f"data: {json.dumps(chunk)}\n\n"
        body += "data: [DONE]\n\n"
        return httpx2.Response(200, content=body, headers={"content-type": "text/event-stream"})

    def client(p: providers.ProviderConfig, *, timeout: float, max_retries: int) -> Any:
        http = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
        return openai.AsyncOpenAI(base_url=p.base_url, api_key="k", http_client=http)

    async def no_remote(p: providers.ProviderConfig, model: str) -> str | None:
        return seen.get("remote")

    async def sees(p: providers.ProviderConfig, model: str) -> bool:
        return True

    monkeypatch.setattr(providers, "chat_client", client)
    monkeypatch.setattr(providers, "remote_host", no_remote)
    monkeypatch.setattr(providers, "vision", sees)
    return seen


@pytest.fixture
def api(settings: Settings, temp_dir: Path) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as c:
        assert c.post("/api/v1/agents", json=GEMMA).status_code == 201
        yield c


def lines(response: Any) -> list[dict[str, Any]]:
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def chat(api: TestClient, turns: list[dict[str, Any]], agent: str = "gemma") -> Any:
    return api.post(f"/api/v1/agents/{agent}/private", json={"turns": turns})


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def counts(api: TestClient) -> dict[str, int]:
    state: Any = cast(Any, api.app).state

    async def read() -> dict[str, int]:
        async with state.sessionmaker() as session:
            return {
                t.name: int(await session.scalar(select(func.count()).select_from(t)) or 0)
                for t in Base.metadata.sorted_tables
            }

    return cast(Any, api).portal.call(read)


def files_under(*roots: Path) -> set[str]:
    return {str(p) for root in roots if root.exists() for p in root.rglob("*")}


def test_private_streams_tokens(api: TestClient, model: dict[str, Any]) -> None:
    r = chat(api, [{"role": "user", "text": "안녕", "parts": []}])
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/x-ndjson")
    assert lines(r) == [{"token": "비밀 "}, {"token": "답이에요"}, {"done": True}]
    sent = model["requests"][0]
    assert sent["model"] == "gemma4:e4b" and sent["stream"] is True
    assert sent["messages"][0]["role"] == "system"
    assert "현재 시각" in sent["messages"][0]["content"]
    assert "Argos 도구" not in sent["messages"][0]["content"]
    assert sent["messages"][-1] == {"role": "user", "content": "안녕"}
    assert sent["reasoning_effort"] == "none"


def test_private_sends_history_and_image(api: TestClient, model: dict[str, Any]) -> None:
    parts = api.post(
        "/api/v1/agents/gemma/private/files",
        json={"files": [{"name": "shot.png", "data": b64(png(4, 4))}]},
    ).json()
    turns: list[dict[str, Any]] = [
        {"role": "user", "text": "첫 질문", "parts": []},
        {"role": "assistant", "text": "첫 답", "parts": []},
        {"role": "user", "text": "이 그림은?", "parts": parts},
    ]
    assert chat(api, turns).status_code == 200
    messages = model["requests"][0]["messages"]
    assert messages[2] == {"role": "assistant", "content": "첫 답"}
    content = messages[3]["content"]
    assert content[0] == {"type": "text", "text": "이 그림은?"}
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_private_files_roundtrip(api: TestClient, model: dict[str, Any]) -> None:
    files = [
        {"name": "a.png", "data": b64(png())},
        {"name": "b.pdf", "data": b64(text_pdf("quarterly plan"))},
        {"name": "c.txt", "data": b64("메모".encode())},
    ]
    parts = api.post("/api/v1/agents/gemma/private/files", json={"files": files}).json()
    assert [p["kind"] for p in parts] == ["image", "text", "text"]
    assert parts[0]["mime"] == "image/png" and parts[0]["data"]
    assert "quarterly plan" in parts[1]["text"] and "메모" in parts[2]["text"]


def _no_trace(
    api: TestClient, settings: Settings, temp_dir: Path, caplog: pytest.LogCaptureFixture, run: Any
) -> None:
    caplog.set_level(logging.DEBUG)
    rows_before = counts(api)
    files_before = files_under(settings.data_dir, temp_dir)
    run()
    assert counts(api) == rows_before
    assert files_under(settings.data_dir, temp_dir) == files_before
    assert SECRET not in caplog.text
    assert "contract.pdf" not in caplog.text
    assert "답이에요" not in caplog.text


def test_private_leaves_no_trace(
    api: TestClient,
    model: dict[str, Any],
    settings: Settings,
    temp_dir: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    big = text_pdf("secret contract 123") + b"%" + b"x" * 2_000_000  # past Starlette's 1MB spool

    def run() -> None:
        parts = api.post(
            "/api/v1/agents/gemma/private/files",
            json={"files": [{"name": "contract.pdf", "data": b64(big)}]},
        )
        assert parts.status_code == 200, parts.text
        r = chat(api, [{"role": "user", "text": SECRET, "parts": parts.json()}])
        assert lines(r)[-1] == {"done": True}

    _no_trace(api, settings, temp_dir, caplog, run)


def test_private_error_leaves_no_trace(
    api: TestClient,
    model: dict[str, Any],
    settings: Settings,
    temp_dir: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    model["fail"] = True

    def run() -> None:
        r = chat(api, [{"role": "user", "text": SECRET, "parts": []}])
        assert lines(r) == [{"error": "이 Mac에 연결할 수 없어요 (http://127.0.0.1:11434/v1)"}]

    _no_trace(api, settings, temp_dir, caplog, run)


def test_private_refuses_cloud_agent(api: TestClient, model: dict[str, Any]) -> None:
    r = chat(api, [{"role": "user", "text": "x", "parts": []}], agent="claude")
    assert r.status_code == 422
    assert r.json()["error"]["message"] == "비공개 대화는 로컬 모델에서만 쓸 수 있어요"
    assert model["requests"] == []


def test_private_refuses_cloud_provider(api: TestClient, model: dict[str, Any]) -> None:
    pid = api.post(
        "/api/v1/providers",
        json={"name": "Router", "kind": "openai", "base_url": "https://api.example.test/v1"},
    ).json()["id"]
    api.patch("/api/v1/agents/gemma", json={"provider_id": pid})
    assert chat(api, [{"role": "user", "text": "x", "parts": []}]).status_code == 422
    assert model["requests"] == []


def test_private_refuses_remote_host_model(api: TestClient, model: dict[str, Any]) -> None:
    model["remote"] = "https://ollama.com:443"
    r = chat(api, [{"role": "user", "text": "x", "parts": []}])
    assert r.status_code == 422
    assert model["requests"] == []


def test_private_refuses_when_show_fails(
    api: TestClient, model: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(p: providers.ProviderConfig, model_name: str) -> str | None:
        raise providers.ProviderError("down")

    monkeypatch.setattr(providers, "remote_host", broken)
    assert chat(api, [{"role": "user", "text": "x", "parts": []}]).status_code == 422
    assert model["requests"] == []


def test_private_bad_body_hides_input(api: TestClient, model: dict[str, Any]) -> None:
    r = api.post(
        "/api/v1/agents/gemma/private",
        content=json.dumps({"turns": [{"role": "boss", "text": SECRET}]}),
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 422
    assert r.json()["error"]["message"] == "요청 형식이 맞지 않아요"
    assert SECRET not in r.text


def test_private_file_too_large(api: TestClient, settings: Settings, model: dict[str, Any]) -> None:
    limit = settings.attachment_max_mb * 1024 * 1024
    r = api.post(
        "/api/v1/agents/gemma/private/files",
        json={"files": [{"name": "big.bin", "data": b64(b"x" * (limit + 1))}]},
    )
    assert r.status_code == 413
    assert r.json()["error"]["message"] == f"{settings.attachment_max_mb}MB까지 올릴 수 있어요"


def test_unknown_agent_404(api: TestClient, model: dict[str, Any]) -> None:
    assert (
        chat(api, [{"role": "user", "text": "x", "parts": []}], agent="nobody").status_code == 404
    )
