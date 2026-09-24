import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from argos import services
from argos.api import install_error_handlers, router, ws_router
from argos.classifier import apply_overrides, build_classifier
from argos.config import Settings, settings
from argos.db import make_engine, make_sessionmaker
from argos.mcp_server import build_mcp


def create_app(config: Settings = settings) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        config.db_path.parent.mkdir(parents=True, exist_ok=True)
        engine = make_engine(config.db_url)
        app.state.sessionmaker = make_sessionmaker(engine)
        async with app.state.sessionmaker() as session:
            seed = await asyncio.to_thread(services.load_seed, config.seed_path)
            await services.seed_defaults(session, seed)
            overrides = await services.get_settings_overrides(session)
        app.state.base_settings = config
        app.state.settings = apply_overrides(config, overrides)
        app.state.classifier = build_classifier(app.state.settings)
        async with mcp.session_manager.run():
            yield
        await engine.dispose()

    app = FastAPI(title="Argos", lifespan=lifespan)
    app.include_router(router)
    app.include_router(ws_router)
    # MCP over Streamable HTTP at /mcp (a single route; DNS-rebinding protection on).
    mcp = build_mcp(app)
    app.router.routes.extend(
        mcp.streamable_http_app(streamable_http_path="/mcp", host=config.host).routes
    )
    install_error_handlers(app)
    return app


app = create_app()
