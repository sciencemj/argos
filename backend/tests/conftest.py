import asyncio
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
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
