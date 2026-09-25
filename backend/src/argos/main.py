import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from argos import caldav_sync, notify, ops, services, usage, vault
from argos.api import install_error_handlers, router, ws_router
from argos.classifier import apply_overrides, build_classifier
from argos.config import Settings, settings
from argos.db import make_engine, make_sessionmaker
from argos.hub import hub
from argos.mcp_server import build_mcp
from argos.runner import Runner


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
        app.state.runner = Runner(app.state.sessionmaker, app.state.settings)
        async with app.state.sessionmaker() as session:
            await services.abandon_running_runs(session)
        app.state.calendar_sync = caldav_sync.CalendarSync(
            app.state.sessionmaker, lambda: app.state.settings, caldav_sync.Keychain()
        )
        if config.caldav_poll_minutes > 0:
            app.state.calendar_sync.start()

        app.state.notifier = notify.Notifier(app.state.sessionmaker, lambda: app.state.settings)
        app.state.notifier.start()
        app.state.backups = ops.Backups(lambda: app.state.settings)
        if config.auto_backup:
            app.state.backups.start()
        app.state.usage = usage.UsageMonitor(lambda: app.state.settings)
        app.state.runner.usage = app.state.usage
        app.state.usage.start()
        app.state.vault_sync = vault.VaultSync(app.state.sessionmaker, lambda: app.state.settings)
        app.state.vault_sync.watch()
        app.state.vault_sync.nudge()  # index and tasks once at start

        def on_change(_type: str, data: dict[str, Any]) -> None:
            kind = data.get("object_type")
            if kind == "event":  # push Argos-side edits right away
                app.state.calendar_sync.nudge()
            elif kind == "task":  # a linked task may need its checkbox ticked
                app.state.vault_sync.nudge()
            if _type == "agent.done":  # a run used up some of the plan: look again soon
                app.state.usage.refresh_soon()

        stop_listening = hub.listen(on_change)
        async with mcp.session_manager.run():
            yield
        stop_listening()
        await app.state.vault_sync.stop()
        await app.state.usage.stop()
        await app.state.notifier.stop()
        await app.state.backups.stop()
        await app.state.calendar_sync.stop()
        await app.state.runner.shutdown()
        await engine.dispose()

    app = FastAPI(title="Argos", lifespan=lifespan)
    app.include_router(router)
    app.include_router(ws_router)
    # MCP over Streamable HTTP at /mcp (a single route; DNS-rebinding protection on).
    mcp = build_mcp(app)
    app.state.mcp = mcp  # the agent form lists its tools
    app.router.routes.extend(
        mcp.streamable_http_app(streamable_http_path="/mcp", host=config.host).routes
    )
    install_error_handlers(app)
    return app


app = create_app()
