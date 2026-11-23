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


# Text of uploaded files and LMS materials (v0.2 Stage 1.2), same tokenizer as notes.
KNOWLEDGE_FTS_DDL = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5("
    "doc_id UNINDEXED, title, body, tokenize='trigram')"
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


async def begin_read(session: AsyncSession) -> None:
    """Pins the reads that follow to one SQLite snapshot. The sqlite3 driver only opens a
    transaction before a write, so each SELECT otherwise reads the newest WAL state: a
    page built from several SELECTs could mix rows from before and after another
    session's commit. Read-only endpoints call this first; the transaction is rolled
    back when the session closes. Write sessions keep the driver's default (a
    transaction from their first write), so they never hold a stale read snapshot."""
    connection = await session.connection()
    raw = await connection.get_raw_connection()
    driver: Any = raw.driver_connection
    if getattr(driver, "in_transaction", True):
        return  # not SQLite, or a transaction is already open
    await connection.exec_driver_sql("BEGIN")


async def session_scope(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with sessionmaker() as session:
        yield session
