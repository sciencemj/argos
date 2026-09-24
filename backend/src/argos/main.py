from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from argos.config import Settings, settings
from argos.db import make_engine, make_sessionmaker, session_scope


def create_app(config: Settings = settings) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        config.db_path.parent.mkdir(parents=True, exist_ok=True)
        engine = make_engine(config.db_url)
        app.state.sessionmaker = make_sessionmaker(engine)
        yield
        await engine.dispose()

    app = FastAPI(title="Argos", lifespan=lifespan)

    async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
        async for session in session_scope(request.app.state.sessionmaker):
            yield session

    @app.get("/api/v1/health")
    async def health(session: Annotated[AsyncSession, Depends(get_session)]) -> dict[str, str]:
        journal_mode = (await session.execute(text("PRAGMA journal_mode"))).scalar_one()
        return {"status": "ok", "db": "ok", "journal_mode": journal_mode}

    return app


app = create_app()
