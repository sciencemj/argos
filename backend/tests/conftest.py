import asyncio
import socket
import threading
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import uvicorn
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from argos.config import Settings
from argos.db import NOTE_FTS_DDL, Base, make_engine, make_sessionmaker
from argos.main import create_app

SEED_EXAMPLE = Path(__file__).parents[1] / "seed.example.toml"


async def _create_schema(url: str) -> None:
    engine = make_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text(NOTE_FTS_DDL))
    await engine.dispose()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    # _env_file=None: tests must not pick up the developer's backend/.env (keys, models).
    # caldav_poll_minutes=0: no background calendar sync; tests call it themselves.
    config = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        db_path=tmp_path / "test.db",
        seed_path=SEED_EXAMPLE,
        caldav_poll_minutes=0,
        usage_poll_minutes=0,  # no Codex polling; Claude usage read from a temp folder
        claude_dir=tmp_path / "claude",
    )
    asyncio.run(_create_schema(config.db_url))
    return config


@pytest.fixture
async def session(settings: Settings) -> AsyncIterator[AsyncSession]:
    engine = make_engine(settings.db_url)
    async with make_sessionmaker(engine)() as s:
        yield s
    await engine.dispose()


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as c:
        yield c


# A real uvicorn server on a free port, for MCP over HTTP (tests/test_mcp.py and others).
@pytest.fixture
def server(settings: Settings) -> Iterator[str]:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    config = uvicorn.Config(
        create_app(settings), host="127.0.0.1", port=port, log_level="warning", ws="none"
    )
    uv = uvicorn.Server(config)
    thread = threading.Thread(target=uv.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not uv.started:
        if time.time() > deadline:
            raise RuntimeError("uvicorn did not start")
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    uv.should_exit = True
    thread.join(timeout=5)
