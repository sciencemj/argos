"""MCP server (PLAN Phase 4): the app's data as tools for Hermes, Claude Code and Codex.

Served over Streamable HTTP inside the API process (`/mcp`), so writes go through the
same domain services and reach open browser tabs over the WebSocket hub. Every tool
call is attributed to the calling agent: `/mcp?agent=claude` or `X-Argos-Agent: claude`
→ actor `agent:claude`. Deletions never run directly; they become approval requests.
"""

import functools
import inspect
import re
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, cast

from fastapi import FastAPI
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from sqlalchemy.ext.asyncio import AsyncSession

from argos import argos_tools, services
from argos.argos_tools import ArgosTool
from argos.config import Settings
from argos.harness import ToolContext

INSTRUCTIONS = """Argos is the user's single source of truth for schedules and tasks.
When the user mentions a task, deadline or appointment, record it here with these tools
instead of keeping it in your own memory. Times are Asia/Seoul unless an offset is given.
Channels are courses/projects; use list_channels to find names. The UI's "내 공간"
combines the capture inbox with the personal "일상" channel, which stores organised
items that belong to no course. Deleting always waits for the user's approval."""

_AGENT = re.compile(r"^[a-z0-9_-]{1,40}$")
# Filled as tools register; the agent form offers these (PLAN Phase 10).
TOOL_NAMES: list[str] = []
DESTRUCTIVE = {t.name for t in argos_tools.mcp_tools() if t.kind == "destructive"}
READ_ONLY = {t.name for t in argos_tools.mcp_tools() if t.kind == "read"}


def _agent(ctx: Context) -> str:
    request = ctx.request_context.request
    raw = ""
    if request is not None:
        raw = request.query_params.get("agent") or request.headers.get("x-argos-agent") or ""
    raw = raw.strip().lower()
    return f"agent:{raw if _AGENT.match(raw) else 'unknown'}"


def build_mcp(app: FastAPI) -> MCPServer:
    """Tools read `app.state` at call time (sessionmaker, settings, classifier). Their
    bodies are the shared `argos_tools`; only deletions differ (approval requests)."""
    mcp = MCPServer("argos", instructions=INSTRUCTIONS)

    @asynccontextmanager
    async def session() -> AsyncGenerator[AsyncSession]:
        async with app.state.sessionmaker() as s:
            yield s

    def settings() -> Settings:
        return app.state.settings

    def tool_context(ctx: Context) -> ToolContext:
        return ToolContext(
            workspace=settings().argos_workspace,
            agent=_agent(ctx).removeprefix("agent:"),
            sessionmaker=app.state.sessionmaker,
            settings=settings(),
            classifier=app.state.classifier,
        )

    async def run[T](work: Callable[[AsyncSession], Awaitable[T]]) -> T:
        """Domain errors become tool errors the agent can read and act on."""
        async with session() as s:
            try:
                return await work(s)
            except argos_tools.DOMAIN_ERRORS as exc:
                raise ToolError(str(exc)) from exc

    async def allow(ctx: Context, name: str) -> None:
        """Custom agents may only use the tools on their list (PLAN Phase 10); built-in
        agents (no list) may use all. Destructive tools still need approval."""
        caller = _agent(ctx).removeprefix("agent:")
        async with session() as s:
            agent = await services.find_agent(s, caller)
        if agent is not None and agent.tools_json is not None and name not in agent.tools_json:
            raise ToolError(
                f"'{agent.display_name}' 에이전트에게 허용되지 않은 도구예요: {name}. "
                "필요하면 사용자에게 에이전트 설정에서 허용해 달라고 하세요."
            )

    def tool[**P, R](fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        """@mcp.tool() behind the whitelist check. Every tool takes `ctx`."""

        @functools.wraps(fn)
        async def guarded(*args: P.args, **kwargs: P.kwargs) -> R:
            ctx = cast(Context, kwargs.get("ctx"))
            await allow(ctx, fn.__name__)
            return await fn(*args, **kwargs)

        mcp.tool()(guarded)  # pyright: ignore[reportUnknownArgumentType]
        if fn.__name__ not in TOOL_NAMES:
            TOOL_NAMES.append(fn.__name__)
        return fn

    def shared(spec: ArgosTool) -> None:
        """An argos_tools function as an MCP tool: same name, docstring and arguments,
        with the MCP Context in front."""

        async def call(ctx: Context, **kwargs: Any) -> Any:
            try:
                return await spec.fn(tool_context(ctx), **kwargs)
            except argos_tools.DOMAIN_ERRORS as exc:
                raise ToolError(str(exc)) from exc

        ctx_param = inspect.Parameter(
            "ctx", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=Context
        )
        returns = inspect.signature(spec.fn).return_annotation
        call.__name__ = call.__qualname__ = spec.name
        call.__doc__ = spec.description
        call.__signature__ = inspect.Signature(  # pyright: ignore[reportFunctionMemberAccess]
            [ctx_param, *spec.params], return_annotation=returns
        )
        call.__annotations__ = {"ctx": Context, **{p.name: p.annotation for p in spec.params}}
        call.__annotations__["return"] = returns
        tool(call)

    for spec in argos_tools.mcp_tools():
        if spec.kind != "destructive":
            shared(spec)

    # --- destructive: approval only (PLAN P5) ----------------------------------------

    @tool
    async def delete_task(
        ctx: Context,
        task_id: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Ask to delete a task. Nothing is deleted until the user approves in Argos."""
        return await _ask(ctx, "delete_task", {"task_id": task_id}, reason)

    @tool
    async def delete_event(
        ctx: Context,
        event_id: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Ask to delete an event. Nothing is deleted until the user approves in Argos."""
        return await _ask(ctx, "delete_event", {"event_id": event_id}, reason)

    async def _ask(
        ctx: Context, action: str, payload: dict[str, str], reason: str | None
    ) -> dict[str, Any]:
        actor = _agent(ctx)

        async def work(s: AsyncSession) -> dict[str, Any]:
            approval = await services.request_approval(
                s, action=action, payload=payload, actor=actor, reason=reason
            )
            return {
                "approval_id": approval.id,
                "status": "pending",
                "message": "사용자 승인을 기다리는 중이에요. 승인 전에는 삭제되지 않아요.",
            }

        return await run(work)

    # Every destructive Argos tool needs an approval-request version above: one added
    # to argos_tools without it must fail loudly, not vanish or run unapproved.
    served = {t.name for t in mcp._tool_manager.list_tools()}  # pyright: ignore[reportPrivateUsage]
    missing = DESTRUCTIVE - served
    if missing:
        raise RuntimeError(f"destructive tools without an MCP approval wrapper: {missing}")
    return mcp
