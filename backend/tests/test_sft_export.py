"""v0.2 Phase 2: SFT trajectory exporter (`sft_export.py`), CLI and API."""

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from argos import services, sft_export
from argos.config import Settings
from argos.db import make_engine, make_sessionmaker
from argos.main import create_app
from argos.models import (
    Agent,
    AgentBackend,
    AuthorType,
    Channel,
    ChannelKind,
    RunStatus,
)


@pytest.fixture
async def maker(settings: Settings) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = make_engine(settings.db_url)
    yield make_sessionmaker(engine)
    await engine.dispose()


async def _seed_channel_and_agent(
    session: AsyncSession,
    *,
    name: str = "study-bot",
    channel_name: str = "알고리즘",
    backend: AgentBackend = AgentBackend.NATIVE,
    tools: list[str] | None = None,
) -> tuple[Channel, Agent]:
    areas = await services.list_areas(session)
    area = (
        areas[0]
        if areas
        else await services.create_area(session, name="학업", icon=None, actor="user")
    )
    channels = await services.list_channels(session)
    channel = next((c for c in channels if c.name == channel_name), None)
    if channel is None:
        channel = await services.create_channel(
            session, name=channel_name, area_id=area.id, kind=ChannelKind.COURSE, actor="user"
        )
    agent = Agent(
        name=name,
        display_name="Study Bot",
        backend=backend,
        model="qwen2.5:7b",
        system_prompt="항상 한국어로 친절하게 답하세요.",
        tools_json=tools if tools is not None else ["add_task", "list_tasks"],
        is_builtin=False,
    )
    session.add(agent)
    await session.commit()
    await session.refresh(agent)
    return channel, agent


async def _make_run(
    session: AsyncSession,
    channel: Channel,
    agent: Agent,
    *,
    user_text: str = "내일까지 과제 1 추가해줘",
    reply_text: str = "알고리즘 채널에 과제 1을 추가했어요.",
    status: RunStatus = RunStatus.DONE,
    steps: list[dict[str, Any]] | None = None,
    session_id: str | None = None,
    earlier_turns: list[tuple[AuthorType, str, str]] | None = None,
) -> str:
    root_id: str | None = None
    if earlier_turns:
        for author_type, author_id, body in earlier_turns:
            msg = await services.create_message(
                session,
                channel_id=channel.id,
                body=body,
                author_type=author_type,
                author_id=author_id,
                thread_root_id=root_id if session_id is None else None,
                session_id=session_id,
                actor="user",
            )
            if root_id is None and session_id is None:
                root_id = msg.id

    trigger = await services.create_message(
        session,
        channel_id=channel.id,
        body=user_text,
        author_type=AuthorType.USER,
        author_id="user",
        thread_root_id=root_id if session_id is None else None,
        session_id=session_id,
        actor="user",
    )
    reply_root = root_id or trigger.id if session_id is None else None
    run, _ = await services.start_run(
        session,
        agent=agent,
        channel_id=channel.id,
        trigger=trigger,
        reply_thread_root_id=reply_root,
        actor="user",
    )
    if steps is None:
        steps = [
            {
                "kind": "tool_call",
                "call_id": "call_1",
                "tool_name": "add_task",
                "args": {"title": "과제 1", "channel": "알고리즘"},
            },
            {
                "kind": "tool_result",
                "call_id": "call_1",
                "tool_name": "add_task",
                "output": json.dumps({"id": "t1", "title": "과제 1"}, ensure_ascii=False),
            },
        ]
    for step in steps:
        await services.add_run_step(session, run.id, **step)
    await services.finish_run(
        session,
        run.id,
        text=reply_text,
        status=status,
        error="boom" if status == RunStatus.ERROR else None,
    )
    return run.id


def test_mask_secrets_redacts_common_patterns() -> None:
    raw = (
        "Here is my OpenAI key sk-proj-AbCdEfGhIjKlMnOpQrStUvWxYz123456 and "
        "GitHub ghp_1234567890abcdefghijklmnopqrstuvwx and "
        "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.secret and "
        "standalone JWT eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0."
        "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c and "
        "Authorization: Basic dXNlcjpzdXBlcnNlY3JldHBhc3N3b3Jk and "
        "api_key = 'supersecretvalue123' plus "
        "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\n-----END RSA PRIVATE KEY-----"
    )
    masked = sft_export.mask_secrets(raw)
    assert "sk-proj-" not in masked
    assert "ghp_" not in masked
    assert "eyJhbGci" not in masked
    assert "dXNlcjpzdXBlcnNlY3JldHBhc3N3b3Jk" not in masked
    assert "supersecretvalue123" not in masked
    assert "MIIEowIBAAKCAQEA" not in masked
    assert masked.count(sft_export.MASK) >= 7


def test_split_of_is_deterministic() -> None:
    splits = [sft_export.split_of(f"run-{i}", 0.2) for i in range(200)]
    assert splits == [sft_export.split_of(f"run-{i}", 0.2) for i in range(200)]
    assert "train" in splits and "eval" in splits
    assert all(sft_export.split_of(f"run-{i}", 0.0) == "train" for i in range(20))
    assert all(sft_export.split_of(f"run-{i}", 1.0) == "eval" for i in range(20))


async def test_export_builds_openai_messages_and_tools(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    async with maker() as s:
        channel, agent = await _seed_channel_and_agent(s)
        run_id = await _make_run(
            s,
            channel,
            agent,
            earlier_turns=[
                (AuthorType.USER, "user", "안녕!"),
                (AuthorType.AGENT, agent.name, "안녕하세요! 무엇을 도와드릴까요?"),
                (AuthorType.AGENT, "other-bot", "참고로 내일 시험이 있어요."),
            ],
            user_text="내일까지 과제 1 추가해줘. 참고로 토큰은 sk-ant-1234567890abcdefghij",
            steps=[
                {
                    "kind": "tool_call",
                    "call_id": "call_1",
                    "tool_name": "add_task",
                    "args": {"title": "과제 1", "channel": "알고리즘"},
                },
                {
                    "kind": "tool_result",
                    "call_id": "call_1",
                    "tool_name": "add_task",
                    "output": '{"id": "t1", "secret": "ghp_1234567890abcdefghijklmnopqrstuvwx"}',
                },
                # Dangling tool_call without tool_result should be ignored
                {
                    "kind": "tool_call",
                    "call_id": "call_dangling",
                    "tool_name": "list_tasks",
                    "args": {},
                },
            ],
        )

    async with maker() as s:
        examples = await sft_export.export(s, sft_export.ExportOptions(eval_ratio=0.0))

    assert len(examples) == 1
    ex = examples[0]
    assert ex.run_id == run_id
    assert ex.split == "train"
    msgs = ex.record["messages"]
    assert msgs[0]["role"] == "system"
    assert "항상 한국어로 친절하게 답하세요." in msgs[0]["content"]
    assert [m["role"] for m in msgs] == [
        "system",
        "user",
        "assistant",
        "user",
        "user",
        "assistant",
        "tool",
        "assistant",
    ]
    assert msgs[3]["content"] == "[other-bot]: 참고로 내일 시험이 있어요."
    assert sft_export.MASK in msgs[4]["content"]
    assert "sk-ant-" not in msgs[4]["content"]
    tc = msgs[5]["tool_calls"][0]
    assert tc["id"] == "call_1"
    assert tc["function"]["name"] == "add_task"
    assert json.loads(tc["function"]["arguments"]) == {"title": "과제 1", "channel": "알고리즘"}
    assert msgs[6]["tool_call_id"] == "call_1"
    assert sft_export.MASK in msgs[6]["content"]
    assert msgs[7]["content"] == "알고리즘 채널에 과제 1을 추가했어요."

    tool_names = [t["function"]["name"] for t in ex.record["tools"]]
    assert tool_names == ["add_task", "list_tasks"]
    assert ex.record["metadata"]["tool_steps"] == 1


async def test_export_filters_and_dm_session(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    async with maker() as s:
        channel, native_agent = await _seed_channel_and_agent(
            s, name="nat", backend=AgentBackend.NATIVE
        )
        _, claude_agent = await _seed_channel_and_agent(
            s, name="claude-custom", backend=AgentBackend.CLAUDE_CODE
        )
        # 1) Done run with 1 tool step (DM session)
        await _make_run(s, channel, native_agent, session_id="dm-sess-1")
        # 2) Done run with 0 tool steps on claude_agent
        await _make_run(s, channel, claude_agent, steps=[])
        # 3) Failed run
        await _make_run(s, channel, native_agent, status=RunStatus.ERROR)

    async with maker() as s:
        only_ok = await sft_export.export(s, sft_export.ExportOptions(only_success=True))
        assert len(only_ok) == 2

        with_failed = await sft_export.export(s, sft_export.ExportOptions(only_success=False))
        assert len(with_failed) == 3

        min_steps = await sft_export.export(s, sft_export.ExportOptions(min_tool_steps=1))
        assert len(min_steps) == 1

        native_only = await sft_export.export(s, sft_export.ExportOptions(native_only=True))
        assert len(native_only) == 1
        assert native_only[0].record["metadata"]["agent"] == "nat"

    # Broken JSON tool_call (_raw) is excluded when only_success=True, kept when False
    async with maker() as s:
        raw_steps = [
            {
                "kind": "tool_call",
                "call_id": "call_raw",
                "tool_name": "add_task",
                "args": {"_raw": "{broken json"},
            },
            {
                "kind": "tool_result",
                "call_id": "call_raw",
                "tool_name": "add_task",
                "output": "JSON parse error",
                "is_error": True,
            },
        ]
        raw_run_id = await _make_run(s, channel, native_agent, steps=raw_steps)

    async with maker() as s:
        strict_examples = await sft_export.export(
            s, sft_export.ExportOptions(only_success=True, min_tool_steps=1)
        )
        assert all(e.run_id != raw_run_id for e in strict_examples)

        failed_allowed = await sft_export.export(
            s, sft_export.ExportOptions(only_success=False, min_tool_steps=1)
        )
        raw_ex = next(e for e in failed_allowed if e.run_id == raw_run_id)
        tc_msg = next(m for m in raw_ex.record["messages"] if m.get("tool_calls"))
        assert tc_msg["tool_calls"][0]["function"]["arguments"] == "{broken json"


def test_cli_and_api_export(
    settings: Settings,
    maker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import asyncio

    async def seed() -> None:
        async with maker() as s:
            channel, agent = await _seed_channel_and_agent(s)
            # Create multiple runs so both train and eval splits are populated
            for i in range(15):
                await _make_run(
                    s,
                    channel,
                    agent,
                    user_text=f"과제 {i} 추가해줘",
                    steps=[] if i == 14 else None,  # newest run has 0 tool steps
                )

    asyncio.run(seed())

    out_dir = tmp_path / "sft_out"
    sft_export.main(["--db", str(settings.db_path), "--out-dir", str(out_dir), "--eval-ratio", "0"])
    captured = json.loads(capsys.readouterr().out)
    assert captured == {"train": 15, "eval": 0}
    lines = (out_dir / "train.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 15
    assert json.loads(lines[0])["metadata"]["agent"] == "study-bot"

    single_file = tmp_path / "single.jsonl"
    sft_export.main(["--db", str(settings.db_path), "--out", str(single_file), "--limit", "3"])
    single_counts = json.loads(capsys.readouterr().out)
    assert single_counts["total"] == 3
    assert single_file.is_file()

    allowed = settings.model_copy(update={"remote_allowed": ["owner@example.com"]})
    with TestClient(create_app(allowed)) as client:
        # Regression: newest run has 0 tool steps, so min_tool_steps=1 & limit=1 must still return 1
        res = client.get("/api/v1/sft/export?min_tool_steps=1&eval_ratio=0&limit=1")
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("application/x-ndjson")
        exported = [json.loads(line) for line in res.text.strip().splitlines()]
        assert len(exported) == 1
        assert exported[0]["metadata"]["tool_steps"] == 1

        # Regression: split=eval&limit=1 applies limit after split filtering
        eval_res = client.get("/api/v1/sft/export?split=eval&eval_ratio=0.5&limit=1")
        assert eval_res.status_code == 200
        eval_exported = [json.loads(line) for line in eval_res.text.strip().splitlines()]
        assert len(eval_exported) == 1
        assert eval_exported[0]["metadata"]["split"] == "eval"

        # Remote requests via Tailscale serve are blocked (local only)
        remote_res = client.get(
            "/argos/api/v1/sft/export",
            headers={
                "Tailscale-Headers-Info": "https://tailscale.com/s/serve-headers",
                "Tailscale-User-Login": "owner@example.com",
            },
        )
        assert remote_res.status_code == 403

    # Non-loopback client host is blocked even without Tailscale headers
    with TestClient(create_app(allowed), client=("192.168.1.50", 50000)) as lan_client:
        assert lan_client.get("/api/v1/sft/export").status_code == 403


def test_training_seed_and_eval_harness_smoke(tmp_path: Path) -> None:
    import importlib
    import sys

    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    eval_harness: Any = importlib.import_module("training.eval_harness")
    generate_seed_data: Any = importlib.import_module("training.generate_seed_data")
    train_sft: Any = importlib.import_module("training.train_sft")

    generate_seed_data.main(["--out-dir", str(tmp_path), "--count", "12", "--eval-ratio", "0.3"])
    assert (tmp_path / "train.jsonl").is_file()
    assert (tmp_path / "eval.jsonl").is_file()

    train_records: list[dict[str, Any]] = train_sft.load_jsonl_records(tmp_path / "train.jsonl")
    eval_records: list[dict[str, Any]] = train_sft.load_jsonl_records(tmp_path / "eval.jsonl")
    assert len(train_records) + len(eval_records) == 12
    assert all("metadata" not in r and "messages" in r and "tools" in r for r in train_records)

    out_report = tmp_path / "eval_report.json"
    eval_harness.main(
        [
            "--eval-file",
            str(tmp_path / "eval.jsonl"),
            "--offline-replay",
            "--no-wandb",
            "--out-json",
            str(out_report),
        ]
    )
    report = json.loads(out_report.read_text(encoding="utf-8"))
    assert report["summary"]["json_validity_rate"] == 1.0
    assert report["summary"]["tool_selection_accuracy"] == 1.0
    assert report["summary"]["schema_compliance_rate"] == 1.0
    assert report["summary"]["exact_argument_match_rate"] == 1.0

    # Extra unexpected keys in predicted args must fail exact_args_match
    extra_key_eval = eval_harness.score_prediction(
        run_id="r1",
        step_index=0,
        prefix=[{"role": "user", "content": "hi"}],
        expected_tool="add_task",
        expected_args={"title": "과제", "channel": "알고리즘"},
        predicted_tool="add_task",
        raw_arguments=json.dumps({"title": "과제", "channel": "알고리즘", "priority": 1}),
        tool_schemas={
            "add_task": {
                "type": "object",
                "required": ["title", "channel"],
                "properties": {
                    "title": {"type": "string"},
                    "channel": {"type": "string"},
                    "priority": {"type": "integer"},
                },
            }
        },
    )
    assert extra_key_eval.schema_valid is True
    assert extra_key_eval.exact_args_match is False
