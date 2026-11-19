"""Claude Agent SDK ↔ Argos harness bridge (v0.2): tool step cards from SDK blocks,
`can_use_tool` through the harness's decide() and approval gate, Argos tools in-process."""

from pathlib import Path
from typing import Any

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    TextBlock,
    ToolPermissionContext,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from argos.agents import (
    ApprovalNeeded,
    ClaudeHarness,
    ClaudeSDKAdapter,
    ClaudeToolSteps,
    ToolCall,
    ToolResult,
    Turn,
)
from argos.config import Settings
from argos.harness import HarnessRun, ToolContext, ToolDef, ToolRegistry
from argos.models import Agent, AgentBackend, PermissionMode


async def _echo(args: dict[str, Any], ctx: ToolContext) -> str:
    return f"ok {args.get('path', '')}"


def _path(args: dict[str, Any]) -> list[str]:
    return [args["path"]] if "path" in args else []


def _no_session(*args: Any, **kwargs: Any) -> None:
    return None


def _registry() -> ToolRegistry:
    return ToolRegistry(
        [
            ToolDef("get_today", "today", {"type": "object"}, "read", _echo),
            ToolDef("write_file", "write", {"type": "object"}, "write", _echo, paths=_path),
            ToolDef("delete_task", "delete", {"type": "object"}, "destructive", _echo),
            ToolDef("render_artifact", "draw", {"type": "object"}, "read", _echo),
        ]
    )


class FakeGate:
    def __init__(self, answer: bool) -> None:
        self.answer = answer
        self.requests: list[tuple[str, dict[str, Any], str]] = []

    async def request(self, tool: ToolDef, args: dict[str, Any], summary: str) -> str:
        self.requests.append((tool.name, args, summary))
        return f"ap{len(self.requests)}"

    async def wait(self, approval_id: str) -> bool:
        return self.answer


def _bridge(
    tmp_path: Path,
    mode: PermissionMode = PermissionMode.AUTO_EDIT,
    gate: FakeGate | None = None,
    trusted: tuple[Path, ...] = (),
) -> ClaudeHarness:
    workspace = tmp_path / "ws"
    workspace.mkdir(exist_ok=True)
    ctx = ToolContext(workspace=workspace, agent="claude", permission_mode=mode, trusted=trusted)
    return ClaudeHarness(ctx, _registry(), gate)


async def _permit(bridge: ClaudeHarness, name: str, args: dict[str, Any]) -> Any:
    announced: list[ApprovalNeeded] = []

    async def announce(event: ApprovalNeeded) -> None:
        announced.append(event)

    result = await bridge.permit(name, args, announce)
    return result, announced


# --- tool steps ---------------------------------------------------------------------


def test_tool_use_and_result_blocks_become_tool_steps() -> None:
    steps = ClaudeToolSteps()
    call = AssistantMessage(
        content=[
            TextBlock("그려 볼게요"),
            ToolUseBlock("t1", "mcp__argos__render_artifact", {"kind": "svg", "code": "<svg/>"}),
            ToolUseBlock("t2", "Bash", {"command": "ls"}),
        ],
        model="claude",
    )
    assert steps.events(call) == [
        ToolCall("t1", "render_artifact", {"kind": "svg", "code": "<svg/>"}),
        ToolCall("t2", "Bash", {"command": "ls"}),
    ]
    assert steps.events(call) == []  # the same blocks again: no duplicate cards
    result = UserMessage(
        content=[
            ToolResultBlock("t1", [{"type": "text", "text": "그렸어요"}]),
            ToolResultBlock("t2", "permission denied", is_error=True),
        ]
    )
    assert steps.events(result) == [
        ToolResult("t1", "render_artifact", "그렸어요", False),
        ToolResult("t2", "Bash", "permission denied", True),
    ]


def test_subagent_tool_blocks_stay_out_of_the_thread() -> None:
    steps = ClaudeToolSteps()
    inner = AssistantMessage(
        content=[ToolUseBlock("s1", "Read", {"file_path": "a"})],
        model="claude",
        parent_tool_use_id="task1",
    )
    assert steps.events(inner) == []
    assert steps.events(UserMessage(content=[ToolResultBlock("s1", "x")])) == []


# --- can_use_tool -------------------------------------------------------------------


async def test_auto_edit_runs_reads_and_workspace_writes_without_asking(tmp_path: Path) -> None:
    gate = FakeGate(True)
    bridge = _bridge(tmp_path, gate=gate)
    for name, args in [
        ("mcp__argos__get_today", {}),
        ("mcp__argos__write_file", {"path": "notes.md"}),
        ("Read", {"file_path": str(tmp_path / "ws" / "a.txt")}),
        ("Edit", {"file_path": "b.txt"}),
    ]:
        result, announced = await _permit(bridge, name, args)
        assert isinstance(result, PermissionResultAllow), name
        assert announced == []
    assert gate.requests == []


async def test_ask_mode_write_waits_for_approval(tmp_path: Path) -> None:
    gate = FakeGate(True)
    bridge = _bridge(tmp_path, PermissionMode.ASK, gate)
    result, announced = await _permit(bridge, "mcp__argos__write_file", {"path": "notes.md"})
    assert isinstance(result, PermissionResultAllow)
    [event] = announced
    assert (event.approval_id, event.tool_name, event.args) == (
        "ap1",
        "write_file",
        {"path": "notes.md"},
    )
    assert gate.requests[0][0] == "write_file"
    # Reads still run on their own in ask mode
    result, announced = await _permit(bridge, "mcp__argos__get_today", {})
    assert isinstance(result, PermissionResultAllow) and announced == []


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("mcp__argos__delete_task", {"task_id": "t"}),
        ("Bash", {"command": "rm -rf build"}),
        ("mcp__github__create_issue", {"title": "x"}),  # unknown tools always ask
    ],
)
async def test_destructive_calls_ask_even_in_auto_edit_and_rejection_denies(
    tmp_path: Path, name: str, args: dict[str, Any]
) -> None:
    gate = FakeGate(False)
    bridge = _bridge(tmp_path, gate=gate)
    result, announced = await _permit(bridge, name, args)
    assert isinstance(result, PermissionResultDeny)
    assert "거절" in result.message
    assert len(announced) == 1 and len(gate.requests) == 1


async def test_outside_workspace_asks_unless_linked(tmp_path: Path) -> None:
    linked = tmp_path / "linked"
    linked.mkdir()
    gate = FakeGate(True)
    bridge = _bridge(tmp_path, gate=gate, trusted=(linked,))
    # A linked folder counts as workspace
    result, announced = await _permit(bridge, "Write", {"file_path": str(linked / "a.md")})
    assert isinstance(result, PermissionResultAllow) and announced == []
    # Argos tool outside the workspace: approval
    result, announced = await _permit(bridge, "mcp__argos__write_file", {"path": "/tmp/x"})
    assert isinstance(result, PermissionResultAllow) and len(announced) == 1


async def test_builtin_tool_outside_allowed_roots_is_refused_without_asking(
    tmp_path: Path,
) -> None:
    gate = FakeGate(True)
    bridge = _bridge(tmp_path, gate=gate)
    result, announced = await _permit(bridge, "Read", {"file_path": "/etc/passwd"})
    assert isinstance(result, PermissionResultDeny)
    assert announced == [] and gate.requests == []


async def test_no_gate_denies_calls_needing_approval(tmp_path: Path) -> None:
    bridge = _bridge(tmp_path)
    result, _ = await _permit(bridge, "Bash", {"command": "ls"})
    assert isinstance(result, PermissionResultDeny)


# --- adapter ------------------------------------------------------------------------


async def test_adapter_streams_tool_cards_and_inline_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import argos.agents as agents_module

    seen: list[Any] = []

    async def fake_query(*, prompt: Any, options: Any) -> Any:
        seen.append(options)
        yield AssistantMessage(
            content=[ToolUseBlock("t1", "mcp__argos__delete_task", {"task_id": "x"})],
            model="claude",
        )
        decision = await options.can_use_tool(
            "mcp__argos__delete_task", {"task_id": "x"}, ToolPermissionContext()
        )
        assert isinstance(decision, PermissionResultAllow)
        yield UserMessage(content=[ToolResultBlock("t1", "deleted")])
        yield ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id="s",
        )

    monkeypatch.setattr(agents_module, "query", fake_query)
    monkeypatch.setattr(agents_module, "get_session_info", _no_session)
    gate = FakeGate(True)
    adapter = ClaudeSDKAdapter(
        "claude",
        None,
        tmp_path / "home",
        "http://x/mcp",
        None,
        harness=_bridge(tmp_path, gate=gate),
    )
    events = [e async for e in adapter.stream([Turn("user", "지워 줘")], "ctx", "s1")]
    assert events == [
        ToolCall("t1", "delete_task", {"task_id": "x"}),
        ApprovalNeeded("ap1", "delete_task", {"task_id": "x"}, gate.requests[0][2]),
        ToolResult("t1", "delete_task", "deleted", False),
    ]
    [options] = seen
    assert options.mcp_servers["argos"]["type"] == "sdk"  # in-process, not /mcp
    assert options.allowed_tools == []  # nothing bypasses can_use_tool


async def test_adapter_without_harness_keeps_the_mcp_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import argos.agents as agents_module

    seen: list[Any] = []

    async def fake_query(*, prompt: Any, options: Any) -> Any:
        seen.append(options)
        yield ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id="s",
        )

    monkeypatch.setattr(agents_module, "query", fake_query)
    monkeypatch.setattr(agents_module, "get_session_info", _no_session)
    adapter = ClaudeSDKAdapter("claude", None, tmp_path, "http://x/mcp", None)
    assert [e async for e in adapter.stream([Turn("user", "hi")], "ctx", "s1")] == []
    assert seen[0].mcp_servers == {"argos": {"type": "http", "url": "http://x/mcp"}}
    assert seen[0].can_use_tool is None


def test_build_adapter_bridges_claude_runs(tmp_path: Path, settings: Settings) -> None:
    from argos.agents import build_adapter
    from argos.file_tools import register_file_tools
    from argos.harness import core_tools

    register_file_tools(core_tools)
    config = settings.model_copy(update={"claude_bin": "/bin/sh"})
    agent = Agent(name="claude", backend=AgentBackend.CLAUDE_CODE)
    linked = tmp_path / "linked"
    run = HarnessRun(run_id="r1", trusted=(linked,), workspace=tmp_path / "chan")
    chat = build_adapter(agent, config, harness=run)
    assert isinstance(chat, ClaudeSDKAdapter)
    bridge = chat._harness  # pyright: ignore[reportPrivateUsage]
    assert bridge is not None
    assert "write_file" in bridge.registry  # chat: Argos file tools in the channel folder
    assert bridge.context.trusted == (linked,)
    assert bridge.context.workspace == (tmp_path / "chan").resolve()
    job = build_adapter(agent, config, job_workspace=tmp_path / "job", harness=run)
    assert isinstance(job, ClaudeSDKAdapter)
    job_bridge = job._harness  # pyright: ignore[reportPrivateUsage]
    assert job_bridge is not None
    assert "write_file" not in job_bridge.registry  # Claude's own file tools instead
    assert build_adapter(agent, config)._harness is None  # type: ignore[attr-defined]
