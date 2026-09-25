from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

# Full-text index of vault notes (PLAN Phase 8). SQLAlchemy has no FTS5 type, so the
# migration creates it with this statement; the trigram tokenizer also matches inside
# Korean words ("알고리즘" in "알고리즘의").
NOTE_FTS_DDL = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS note_fts USING fts5("
    "note_id UNINDEXED, title, body, tokenize='trigram')"
)


class Base(DeclarativeBase):
    pass


def _set_sqlite_pragmas(dbapi_conn: Any, _record: Any) -> None:
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


def make_engine(url: str) -> AsyncEngine:
    engine = create_async_engine(url)
    event.listen(engine.sync_engine, "connect", _set_sqlite_pragmas)
    return engine


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def session_scope(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with sessionmaker() as session:
        yield session
