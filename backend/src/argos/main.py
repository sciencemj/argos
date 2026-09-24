import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from argos import services
from argos.api import install_error_handlers, router, ws_router
from argos.config import Settings, settings
from argos.db import make_engine, make_sessionmaker


def create_app(config: Settings = settings) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        config.db_path.parent.mkdir(parents=True, exist_ok=True)
        engine = make_engine(config.db_url)
        app.state.settings = config
        app.state.sessionmaker = make_sessionmaker(engine)
        async with app.state.sessionmaker() as session:
            seed = await asyncio.to_thread(services.load_seed, config.seed_path)
            await services.seed_defaults(session, seed)
        yield
        await engine.dispose()

    app = FastAPI(title="Argos", lifespan=lifespan)
    app.include_router(router)
    app.include_router(ws_router)
    install_error_handlers(app)
    return app


app = create_app()
