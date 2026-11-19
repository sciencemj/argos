"""SFT trajectories from Argos agent runs (v0.2 Phase 2).

Each finished run becomes one training example in the OpenAI chat format that Unsloth /
TRL `SFTTrainer` read (`{"messages": [...], "tools": [...]}`, one JSON object per line):

- a system message (the Argos agent framing plus the agent's own system prompt),
- the conversation the agent saw (its thread, or its DM conversation, up to the
  message that triggered the run): the agent's earlier answers as `assistant`, the
  user's and other agents' messages as `user`,
- the native harness's structured steps (`agent_run_step`): every `tool_call` as an
  assistant message with `tool_calls`, every `tool_result` as a `tool` message,
- the final answer (the run's reply message) as the last `assistant` message,

and `tools`: the JSON schemas of the native tools the agent was allowed to call.

Secrets that slipped into conversations or tool output (API keys, tokens, bearer
headers, private keys) are masked by default. The split into train/eval is a stable
hash of the run id, so re-exporting keeps every run on the same side.

 * CLI: `uv run python -m argos.sft_export --out-dir data/sft` (or `--out data/sft/train.jsonl`);
 * API: `GET /api/v1/sft/export` (local only)."""

import argparse
import asyncio
import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from argos.models import (
    Agent,
    AgentRun,
    AgentRunStep,
    AuthorType,
    Channel,
    Message,
    RunStatus,
)

type Split = Literal["train", "eval"]

SYSTEM_PROMPT = (
    "You are an Argos agent: you help a student manage courses, projects, tasks, "
    "events, notes and files. Use the provided tools when they help, call them with "
    "valid JSON arguments that follow their schemas, and answer in the user's language."
)
TRANSCRIPT_LIMIT = 30
MASK = "[REDACTED]"

# Common secret shapes: provider keys, GitHub/Slack/AWS tokens, JWTs, bearer/basic headers,
# `key = value` assignments of secret-looking names, PEM private keys.
SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_\-]{16,}"),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr|github_pat)_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bxox[abposr]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-~+/]{16,}=*"),
    re.compile(r"(?i)\bbasic\s+[A-Za-z0-9+/]{12,}={0,2}"),
    re.compile(
        r"(?i)\b((?:api[_-]?key|secret|token|password|passwd|access[_-]?key)"
        r"[\"']?\s*[:=]\s*[\"']?)([^\s\"',;]{6,})"
    ),
)


def mask_secrets(text: str) -> str:
    for pattern in SECRET_PATTERNS:
        if pattern.groups >= 2:  # keep the name, hide the value
            text = pattern.sub(lambda m: f"{m.group(1)}{MASK}", text)
        else:
            text = pattern.sub(MASK, text)
    return text


def _mask_value(value: Any) -> Any:
    if isinstance(value, str):
        return mask_secrets(value)
    if isinstance(value, list):
        return [_mask_value(v) for v in value]  # pyright: ignore[reportUnknownVariableType]
    if isinstance(value, dict):
        return {k: _mask_value(v) for k, v in value.items()}  # pyright: ignore[reportUnknownVariableType]
    return value


def split_of(run_id: str, eval_ratio: float) -> Split:
    """Stable train/eval assignment from the run id."""
    if eval_ratio <= 0:
        return "train"
    bucket = int(hashlib.sha256(run_id.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return "eval" if bucket < eval_ratio else "train"


@dataclass
class ExportOptions:
    only_success: bool = True  # finished runs only (status done)
    min_tool_steps: int = 0  # tool calls a run must have made
    mask: bool = True
    eval_ratio: float = 0.1
    native_only: bool = False  # only runs of Argos-harness agents
    limit: int | None = None  # newest matching examples first (applied after filters)
    split: Split | Literal["all"] = "all"  # filter by split before applying limit
    metadata: bool = True  # a `metadata` object per example (dropped by train_sft.py)
    allow_raw_args: bool | None = None  # None: False when only_success, else True


@dataclass
class Example:
    run_id: str
    split: Split
    record: dict[str, Any] = field(default_factory=dict[str, Any])


def tool_specs(names: Sequence[str] | None = None) -> list[dict[str, Any]]:
    """OpenAI `tools` entries for the native tools (all, or the given names), as the
    harness sends them."""
    from argos.argos_tools import register_argos_tools
    from argos.extra_tools import register_extra_tools
    from argos.file_tools import register_file_tools
    from argos.harness import ToolRegistry, core_tools

    registry = ToolRegistry()
    register_argos_tools(registry)
    register_file_tools(registry)
    register_extra_tools(registry)
    for tool in core_tools.tools():  # plugins loaded in this process, if any
        if tool.name not in registry:
            registry.register(tool)
    chosen = registry if names is None else registry.subset(list(names))
    return [t.openai_spec() for t in chosen.tools()]


async def _conversation(session: AsyncSession, run: AgentRun) -> list[Message]:
    """What the agent saw before answering: its thread, or recent DM turns, up to and
    including the trigger; never its own reply or later messages."""
    trigger = await session.get(Message, run.trigger_message_id) if run.trigger_message_id else None
    if trigger is None:
        return []
    root_id = trigger.thread_root_id or (
        run.thread_root_id if run.thread_root_id != trigger.id else None
    )
    if trigger.thread_root_id is not None or root_id is not None:
        root = await session.get(Message, trigger.thread_root_id or root_id)
        replies = (
            await session.scalars(
                select(Message)
                .where(Message.thread_root_id == (root.id if root else trigger.id))
                .order_by(Message.created_at, Message.id)
            )
        ).all()
        messages = [m for m in ([root] if root else []) + list(replies)]
    elif trigger.session_id is not None:
        rows = await session.scalars(
            select(Message)
            .where(
                Message.channel_id == trigger.channel_id,
                Message.session_id == trigger.session_id,
            )
            .order_by(Message.created_at.desc())
            .limit(TRANSCRIPT_LIMIT)
        )
        messages = list(rows.all())[::-1]
    else:
        messages = [trigger]
    cut = trigger.created_at
    return [
        m
        for m in messages
        if m.id != run.reply_message_id
        and (m.created_at < cut or m.id == trigger.id)
        and m.author_type != AuthorType.SYSTEM
        and m.body.strip()
    ][-TRANSCRIPT_LIMIT:]


def _step_messages(
    steps: Sequence[AgentRunStep], *, allow_raw_args: bool = False
) -> tuple[list[dict[str, Any]], int]:
    """Assistant tool calls and tool results, in order. A call without a recorded
    result (the run was stopped) is left out with nothing after it."""
    results = {s.call_id: s for s in steps if s.kind == "tool_result" and s.call_id}
    messages: list[dict[str, Any]] = []
    count = 0
    for step in steps:
        if step.kind != "tool_call" or not step.call_id or step.call_id not in results:
            continue
        result = results[step.call_id]
        args = step.args_json or {}
        if "_raw" in args:  # arguments the model could not write as JSON
            if not allow_raw_args:
                continue
            arguments = str(args["_raw"])
        else:
            arguments = json.dumps(args, ensure_ascii=False)
        messages.append(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": step.call_id,
                        "type": "function",
                        "function": {"name": step.tool_name or "", "arguments": arguments},
                    }
                ],
            }
        )
        messages.append(
            {
                "role": "tool",
                "tool_call_id": step.call_id,
                "name": result.tool_name or step.tool_name or "",
                "content": result.output or "",
            }
        )
        count += 1
    return messages, count


async def build_example(
    session: AsyncSession, run: AgentRun, agent: Agent, options: ExportOptions
) -> Example | None:
    split = split_of(run.id, options.eval_ratio)
    if options.split != "all" and split != options.split:
        return None
    reply = await session.get(Message, run.reply_message_id) if run.reply_message_id else None
    if reply is None or not reply.body.strip():
        return None
    steps = (
        await session.scalars(
            select(AgentRunStep).where(AgentRunStep.run_id == run.id).order_by(AgentRunStep.seq)
        )
    ).all()
    allow_raw = (
        options.allow_raw_args if options.allow_raw_args is not None else not options.only_success
    )
    step_messages, tool_steps = _step_messages(steps, allow_raw_args=allow_raw)
    if tool_steps < options.min_tool_steps:
        return None
    conversation = await _conversation(session, run)
    if not conversation:
        return None
    system = SYSTEM_PROMPT + (f"\n\n{agent.system_prompt.strip()}" if agent.system_prompt else "")
    messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for m in conversation:
        if m.author_type == AuthorType.AGENT and m.author_id == agent.name:
            messages.append({"role": "assistant", "content": m.body})
        elif m.author_type == AuthorType.AGENT:
            messages.append({"role": "user", "content": f"[{m.author_id}]: {m.body}"})
        else:
            messages.append({"role": "user", "content": m.body})
    messages += step_messages
    messages.append({"role": "assistant", "content": reply.body})
    record: dict[str, Any] = {"messages": messages, "tools": tool_specs(agent.tools_json)}
    if options.mask:
        record["messages"] = _mask_value(record["messages"])
    if options.metadata:
        channel = await session.get(Channel, run.channel_id)
        record["metadata"] = {
            "run_id": run.id,
            "agent": agent.name,
            "backend": str(agent.backend),
            "model": agent.model,
            "channel": channel.name if channel else None,
            "kind": run.kind,
            "tool_steps": tool_steps,
            "split": split,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        }
    return Example(run.id, split, record)


EXPORT_BATCH_SIZE = 100


async def export(session: AsyncSession, options: ExportOptions) -> list[Example]:
    base_query = (
        select(AgentRun, Agent)
        .join(Agent, Agent.id == AgentRun.agent_id)
        .order_by(AgentRun.started_at.desc(), AgentRun.id.desc())
    )
    if options.only_success:
        base_query = base_query.where(AgentRun.status == RunStatus.DONE)
    if options.native_only:
        base_query = base_query.where(Agent.backend == "native")

    examples: list[Example] = []
    offset = 0
    batch_size = max(options.limit or EXPORT_BATCH_SIZE, EXPORT_BATCH_SIZE)
    while True:
        rows = (await session.execute(base_query.offset(offset).limit(batch_size))).all()
        if not rows:
            break
        for run, agent in rows:
            example = await build_example(session, run, agent, options)
            if example is not None:
                examples.append(example)
                if options.limit is not None and len(examples) >= options.limit:
                    return examples
        if len(rows) < batch_size:
            break
        offset += batch_size
    return examples


def to_jsonl(examples: Sequence[Example]) -> str:
    return "".join(json.dumps(e.record, ensure_ascii=False) + "\n" for e in examples)


def write(examples: Sequence[Example], out_dir: Path) -> dict[str, int]:
    """train.jsonl and eval.jsonl in `out_dir`; returns how many went to each."""
    out_dir.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    for split in ("train", "eval"):
        chosen = [e for e in examples if e.split == split]
        (out_dir / f"{split}.jsonl").write_text(to_jsonl(chosen), encoding="utf-8")
        counts[split] = len(chosen)
    return counts


def write_file(examples: Sequence[Example], out_path: Path) -> dict[str, int]:
    """Writes all examples (or split if needed) to a single JSONL file `out_path`."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(to_jsonl(examples), encoding="utf-8")
    return {
        "total": len(examples),
        "train": sum(1 for e in examples if e.split == "train"),
        "eval": sum(1 for e in examples if e.split == "eval"),
    }


async def _main(args: argparse.Namespace) -> dict[str, int]:
    from argos.config import Settings
    from argos.db import make_engine, make_sessionmaker

    settings = Settings() if args.db is None else Settings(db_path=Path(args.db))
    engine = make_engine(settings.db_url)
    try:
        async with make_sessionmaker(engine)() as session:
            examples = await export(
                session,
                ExportOptions(
                    only_success=not args.include_failed,
                    min_tool_steps=args.min_tool_steps,
                    mask=not args.no_mask,
                    eval_ratio=args.eval_ratio,
                    native_only=args.native_only,
                    limit=args.limit,
                    metadata=not args.no_metadata,
                ),
            )
    finally:
        await engine.dispose()
    target = Path(args.out_dir or args.out)
    if args.out and target.suffix == ".jsonl":
        return write_file(examples, target)
    return write(examples, target)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m argos.sft_export",
        description="Export Argos agent runs as OpenAI messages+tools JSONL for SFT.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--out-dir", help="writes train.jsonl and eval.jsonl here")
    group.add_argument("--out", help="output directory, or a single .jsonl file path")
    parser.add_argument("--db", help="SQLite database (default: ARGOS_DB_PATH / settings)")
    parser.add_argument("--min-tool-steps", type=int, default=0)
    parser.add_argument("--eval-ratio", type=float, default=0.1)
    parser.add_argument("--include-failed", action="store_true", help="not only finished runs")
    parser.add_argument("--native-only", action="store_true", help="only Argos-harness agents")
    parser.add_argument("--limit", type=int, default=None, help="newest N runs")
    parser.add_argument("--no-mask", action="store_true", help="keep secrets (not advised)")
    parser.add_argument("--no-metadata", action="store_true")
    counts = asyncio.run(_main(parser.parse_args(argv)))
    print(json.dumps(counts))


if __name__ == "__main__":
    main()
