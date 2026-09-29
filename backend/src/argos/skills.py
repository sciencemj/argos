"""Agent skills as `/name` commands in conversations: each backend's installed skills
(Claude Code skills and commands, Codex skills, Hermes skills), listed for the
composer's completion and resolved when a message starts with `/name`."""

import asyncio
import json
import os
import re
import shutil
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, cast

import httpx2
from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

from argos.agents import CLAUDE_CHAT_SETTINGS, AgentUnavailable, Turn, prepare_codex_home
from argos.config import Settings
from argos.models import Agent, AgentBackend

# "/name rest", optionally after the @mentions that picked the agent.
SKILL_CALL = re.compile(r"^(?:@[\w.-]+\s+)*/([\w:.-]+)(?:\s+(.*))?$", re.DOTALL)
CACHE_SECONDS = 300.0


@dataclass(frozen=True)
class Skill:
    name: str
    description: str = ""
    path: str | None = None  # Codex: the SKILL.md a turn's skill input points at


type SkillLister = Callable[[Agent, Settings], Awaitable[list[Skill]]]


def parse_call(text: str) -> tuple[str, str] | None:
    """`/name args` (after any @mentions) → (name, args)."""
    match = SKILL_CALL.match(text.strip())
    return (match.group(1), (match.group(2) or "").strip()) if match else None


def instruction(name: str, args: str) -> str:
    """What a backend without native `/name` invocation reads instead."""
    head = f"Use your `{name}` skill (load it and follow its instructions) for this request."
    return f"{head}\n\n{args}" if args else head


def apply_skill(transcript: list[Turn], skills: list[Skill]) -> list[Turn]:
    """Marks the last user turn as a skill call when it names one of `skills`."""
    if not transcript or transcript[-1].speaker != "user":
        return transcript
    call = parse_call(transcript[-1].text)
    if call is None:
        return transcript
    name, args = call
    skill = next((s for s in skills if s.name == name), None)
    if skill is None:
        return transcript
    turn = Turn("user", instruction(name, args), skill=name, args=args, skill_path=skill.path)
    return [*transcript[:-1], turn]


class SkillCache:
    """Listing spawns a CLI or calls the gateway; the answer is kept a few minutes."""

    def __init__(self, lister: SkillLister) -> None:
        self._lister = lister
        self._cache: dict[str, tuple[float, list[Skill]]] = {}

    async def __call__(self, agent: Agent, settings: Settings) -> list[Skill]:
        key = f"{agent.backend}:{agent.name}"
        hit = self._cache.get(key)
        if hit is not None and time.monotonic() - hit[0] < CACHE_SECONDS:
            return hit[1]
        try:
            listed = await self._lister(agent, settings)
        except (AgentUnavailable, OSError, httpx2.HTTPError, TimeoutError):
            return []  # unavailable backends have no skills; not cached, try again next time
        # One entry per name (Codex can list a skill from two roots); the first wins.
        unique: dict[str, Skill] = {}
        for skill in listed:
            unique.setdefault(skill.name, skill)
        skills = list(unique.values())
        self._cache[key] = (time.monotonic(), skills)
        return skills


async def list_skills(agent: Agent, settings: Settings) -> list[Skill]:
    match agent.backend:
        case AgentBackend.CLAUDE_CODE:
            return await _claude_skills(settings)
        case AgentBackend.CODEX:
            return await _codex_skills(settings)
        case AgentBackend.HERMES:
            return await _hermes_skills(settings)
        case _:
            return []


async def _claude_skills(settings: Settings) -> list[Skill]:
    """The slash commands Claude Code offers (skills, custom and built-in commands)."""
    cli = shutil.which(settings.claude_bin)
    if cli is None:
        raise AgentUnavailable(f"{settings.claude_bin} 명령을 찾을 수 없어요")
    workspace = settings.agent_workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    options = ClaudeAgentOptions(
        cwd=str(workspace),
        cli_path=cli,
        tools=[],
        skills="all",
        setting_sources=["user"],
        settings=CLAUDE_CHAT_SETTINGS,
        mcp_servers={},
        strict_mcp_config=True,
    )
    info: dict[str, Any] | None = None
    async with asyncio.timeout(30), ClaudeSDKClient(options) as client:
        info = await client.get_server_info()
    commands = cast(list[dict[str, Any]], (info or {}).get("commands") or [])
    return [
        Skill(str(c["name"]), str(c.get("description") or "")) for c in commands if c.get("name")
    ]


async def _codex_skills(settings: Settings) -> list[Skill]:
    """`skills/list` of a short-lived `codex app-server` in Argos' CODEX_HOME."""
    binary = shutil.which(settings.codex_bin)
    if binary is None:
        raise AgentUnavailable(f"{settings.codex_bin} 명령을 찾을 수 없어요")
    home = prepare_codex_home(settings.codex_home, settings.codex_auth)
    workspace = settings.agent_workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    process = await asyncio.create_subprocess_exec(
        binary,
        "app-server",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env={**os.environ, "CODEX_HOME": str(home), "NO_COLOR": "1"},
        limit=16 * 1024 * 1024,
    )
    assert process.stdin is not None and process.stdout is not None
    stdin, stdout = process.stdin, process.stdout

    async def call(request_id: int, method: str, params: dict[str, Any]) -> dict[str, Any]:
        stdin.write(
            (json.dumps({"id": request_id, "method": method, "params": params}) + "\n").encode()
        )
        await stdin.drain()
        while True:
            line = await stdout.readline()
            if not line:
                raise AgentUnavailable("codex app-server가 예기치 않게 종료됐어요")
            message = cast(dict[str, Any], json.loads(line))
            if message.get("id") == request_id and "method" not in message:
                return message

    try:
        async with asyncio.timeout(30):
            await call(1, "initialize", {"clientInfo": {"name": "argos", "version": "0.1"}})
            stdin.write((json.dumps({"method": "initialized", "params": {}}) + "\n").encode())
            reply = await call(2, "skills/list", {"cwds": [str(workspace)]})
    finally:
        if process.returncode is None:
            process.kill()
        await process.wait()
    entries = cast(
        list[dict[str, Any]], cast(dict[str, Any], reply.get("result") or {}).get("data") or []
    )
    return [
        Skill(
            str(s["name"]),
            str(s.get("shortDescription") or s.get("description") or ""),
            str(s["path"]),
        )
        for entry in entries
        for s in cast(list[dict[str, Any]], entry.get("skills") or [])
        if s.get("enabled", True) and s.get("name")
    ]


async def _hermes_skills(settings: Settings) -> list[Skill]:
    """The gateway's `GET /v1/skills` (the set `/skills list` shows in Hermes)."""
    if settings.hermes_api_key is None:
        raise AgentUnavailable("Hermes API 키를 찾지 못했어요")
    async with httpx2.AsyncClient(timeout=10) as client:
        response = await client.get(
            f"{settings.hermes_base_url.rstrip('/')}/skills",
            headers={"Authorization": f"Bearer {settings.hermes_api_key.get_secret_value()}"},
        )
        response.raise_for_status()
    data = cast(list[dict[str, Any]], cast(dict[str, Any], response.json()).get("data") or [])
    return [Skill(str(s["name"]), str(s.get("description") or "")) for s in data if s.get("name")]
