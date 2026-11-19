"""Evaluation harness for Argos native tool-calling accuracy against an OpenAI-compatible URL.

Compares a base model or SFT-tuned model (served via Ollama, vLLM, or any OpenAI-compatible
endpoint) on `eval.jsonl` trajectories and measures:
- `json_validity_rate`: fraction of predicted tool calls with valid JSON object arguments,
- `tool_selection_accuracy`: fraction of turns where the model selects the expected tool name,
- `schema_compliance_rate`: fraction of turns where arguments satisfy the tool's JSON Schema,
- `exact_argument_match_rate`: fraction of turns where all ground-truth argument key/values match.

Logs per-step predictions as a Weights & Biases Table and summary metrics to W&B.

Usage:
    python training/eval_harness.py \
        --eval-file training/data/eval.jsonl \
        --base-url http://127.0.0.1:11434/v1 \
        --model qwen2.5:7b \
        --wandb-project argos-sft
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

TYPE_MAP: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
}


@dataclass
class StepEval:
    run_id: str
    step_index: int
    prompt_preview: str
    expected_tool: str
    expected_args: dict[str, Any]
    predicted_tool: str | None
    raw_arguments: str
    json_valid: bool
    tool_match: bool
    schema_valid: bool
    exact_args_match: bool
    error: str | None = None
    # False: a turn where the right move is answering without a tool (a concept
    # question, a clarifying question, the final answer after tool results).
    expects_tool: bool = True


def check_schema_compliance(args: Any, schema: dict[str, Any]) -> tuple[bool, str | None]:
    """Checks required fields and property types against an OpenAI function `parameters` schema."""
    if not isinstance(args, dict):
        return False, "arguments is not a JSON object"
    required = schema.get("required") or []
    for req in required:
        if req not in args or args[req] is None:
            return False, f"missing required field: {req}"
    props = schema.get("properties") or {}
    for key, value in args.items():
        if key not in props:
            return False, f"unexpected property: {key}"
        if value is None:
            continue
        prop_schema = props[key]
        allowed_types = _allowed_json_types(prop_schema)
        if allowed_types and not _matches_types(value, allowed_types):
            return (
                False,
                f"invalid type for {key}: got {type(value).__name__}, expected {allowed_types}",
            )
    return True, None


def _allowed_json_types(prop_schema: dict[str, Any]) -> set[str]:
    found: set[str] = set()
    if "type" in prop_schema:
        t = prop_schema["type"]
        if isinstance(t, list):
            found.update(str(x) for x in t if x != "null")
        elif t != "null":
            found.add(str(t))
    for union_key in ("anyOf", "oneOf"):
        for sub in prop_schema.get(union_key) or []:
            if isinstance(sub, dict) and sub.get("type") and sub.get("type") != "null":
                found.add(str(sub["type"]))
    return found


def _matches_types(value: Any, json_types: set[str]) -> bool:
    # In Python `bool` is a subclass of `int`; guard against bool matching integer/number.
    if isinstance(value, bool):
        return "boolean" in json_types
    for jt in json_types:
        py_types = TYPE_MAP.get(jt)
        if py_types and isinstance(value, py_types):
            return True
    return False


def extract_tool_steps(
    record: dict[str, Any],
    include_no_tool: bool = False,
) -> list[tuple[list[dict[str, Any]], str, dict[str, Any]]]:
    """Splits a trajectory into (prefix_messages, expected_tool, expected_args) per tool call.
    With `include_no_tool`, assistant turns without a tool call are steps too, with
    expected_tool "" (the model should answer, not call a tool)."""
    messages: list[dict[str, Any]] = record.get("messages") or []
    steps: list[tuple[list[dict[str, Any]], str, dict[str, Any]]] = []
    for idx, msg in enumerate(messages):
        if msg.get("role") != "assistant":
            continue
        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            if include_no_tool and (msg.get("content") or "").strip():
                steps.append((messages[:idx], "", {}))
            continue
        first = tool_calls[0].get("function") or {}
        name = str(first.get("name") or "")
        raw_args = first.get("arguments") or "{}"
        try:
            parsed_args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
        except json.JSONDecodeError:
            parsed_args = {}
        steps.append((messages[:idx], name, parsed_args))
    return steps


def score_prediction(
    *,
    run_id: str,
    step_index: int,
    prefix: list[dict[str, Any]],
    expected_tool: str,
    expected_args: dict[str, Any],
    predicted_tool: str | None,
    raw_arguments: str,
    tool_schemas: dict[str, dict[str, Any]],
) -> StepEval:
    last_user = next(
        (m.get("content", "") for m in reversed(prefix) if m.get("role") == "user"),
        "",
    )
    if not expected_tool:  # an answer turn: right when the model calls nothing
        return StepEval(
            run_id=run_id,
            step_index=step_index,
            prompt_preview=str(last_user)[:120],
            expected_tool="",
            expected_args={},
            predicted_tool=predicted_tool,
            raw_arguments=raw_arguments,
            json_valid=False,
            tool_match=predicted_tool is None,
            schema_valid=False,
            exact_args_match=False,
            error=None
            if predicted_tool is None
            else f"called {predicted_tool} instead of answering",
            expects_tool=False,
        )
    if not predicted_tool:
        return StepEval(
            run_id=run_id,
            step_index=step_index,
            prompt_preview=str(last_user)[:120],
            expected_tool=expected_tool,
            expected_args=expected_args,
            predicted_tool=None,
            raw_arguments="",
            json_valid=False,
            tool_match=False,
            schema_valid=False,
            exact_args_match=False,
            error="no tool_call emitted",
        )

    try:
        parsed = json.loads(raw_arguments)
        json_valid = isinstance(parsed, dict)
    except json.JSONDecodeError as exc:
        return StepEval(
            run_id=run_id,
            step_index=step_index,
            prompt_preview=str(last_user)[:120],
            expected_tool=expected_tool,
            expected_args=expected_args,
            predicted_tool=predicted_tool,
            raw_arguments=raw_arguments,
            json_valid=False,
            tool_match=(predicted_tool == expected_tool),
            schema_valid=False,
            exact_args_match=False,
            error=f"invalid JSON: {exc}",
        )

    tool_match = predicted_tool == expected_tool
    schema = tool_schemas.get(predicted_tool)
    if schema is None:
        schema_valid, err = False, f"unknown tool: {predicted_tool}"
    else:
        schema_valid, err = check_schema_compliance(parsed, schema)

    exact_match = tool_match and json_valid and parsed == expected_args
    return StepEval(
        run_id=run_id,
        step_index=step_index,
        prompt_preview=str(last_user)[:120],
        expected_tool=expected_tool,
        expected_args=expected_args,
        predicted_tool=predicted_tool,
        raw_arguments=raw_arguments,
        json_valid=json_valid,
        tool_match=tool_match,
        schema_valid=schema_valid,
        exact_args_match=exact_match,
        error=err,
    )


def summarize_results(results: list[StepEval]) -> dict[str, Any]:
    answers = [r for r in results if not r.expects_tool]
    extra: dict[str, Any] = {}
    if answers:
        # Knowing when not to call a tool: chat turns, clarifications, stopping.
        extra = {
            "no_tool_steps": len(answers),
            "no_tool_accuracy": round(sum(r.tool_match for r in answers) / len(answers), 4),
        }
    results = [r for r in results if r.expects_tool]
    total = len(results)
    if total == 0:
        return {
            "total_steps": 0,
            "json_validity_rate": 0.0,
            "tool_selection_accuracy": 0.0,
            "schema_compliance_rate": 0.0,
            "exact_argument_match_rate": 0.0,
            **extra,
        }
    return {
        **extra,
        "total_steps": total,
        "json_validity_rate": round(sum(r.json_valid for r in results) / total, 4),
        "tool_selection_accuracy": round(sum(r.tool_match for r in results) / total, 4),
        "schema_compliance_rate": round(sum(r.schema_valid for r in results) / total, 4),
        "exact_argument_match_rate": round(sum(r.exact_args_match for r in results) / total, 4),
    }


DEFAULT_REQUEST_TIMEOUT = 60.0


async def evaluate_dataset(
    records: list[dict[str, Any]],
    *,
    base_url: str,
    api_key: str,
    model: str,
    temperature: float = 0.0,
    request_timeout: float = DEFAULT_REQUEST_TIMEOUT,
    offline_replay: bool = False,
    include_no_tool: bool = False,
) -> list[StepEval]:
    from openai import AsyncOpenAI

    client = (
        None
        if offline_replay
        else AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=request_timeout)
    )
    results: list[StepEval] = []
    try:
        for rec_idx, record in enumerate(records):
            run_id = str((record.get("metadata") or {}).get("run_id") or f"eval-{rec_idx}")
            tools: list[dict[str, Any]] = record.get("tools") or []
            schemas = {
                t["function"]["name"]: t["function"].get("parameters") or {}
                for t in tools
                if "function" in t and "name" in t["function"]
            }
            for step_idx, (prefix, expected_tool, expected_args) in enumerate(
                extract_tool_steps(record, include_no_tool)
            ):
                if offline_replay:
                    pred_tool: str | None = expected_tool or None
                    raw_args = json.dumps(expected_args, ensure_ascii=False)
                else:
                    assert client is not None
                    try:
                        resp = await client.chat.completions.create(
                            model=model,
                            messages=prefix,  # type: ignore[arg-type]
                            tools=tools,  # type: ignore[arg-type]
                            temperature=temperature,
                            timeout=request_timeout,
                        )
                    except Exception as exc:  # noqa: BLE001
                        last_user = next(
                            (
                                m.get("content", "")
                                for m in reversed(prefix)
                                if m.get("role") == "user"
                            ),
                            "",
                        )
                        results.append(
                            StepEval(
                                run_id=run_id,
                                step_index=step_idx,
                                prompt_preview=str(last_user)[:120],
                                expected_tool=expected_tool,
                                expected_args=expected_args,
                                predicted_tool=None,
                                raw_arguments="",
                                json_valid=False,
                                tool_match=False,
                                schema_valid=False,
                                exact_args_match=False,
                                error=f"request failed ({type(exc).__name__}): {exc}",
                            )
                        )
                        continue
                    choice = resp.choices[0].message if resp.choices else None
                    if choice is not None and choice.tool_calls:
                        tc = choice.tool_calls[0]
                        pred_tool = tc.function.name
                        raw_args = tc.function.arguments or "{}"
                    else:
                        pred_tool = None
                        raw_args = ""
                results.append(
                    score_prediction(
                        run_id=run_id,
                        step_index=step_idx,
                        prefix=prefix,
                        expected_tool=expected_tool,
                        expected_args=expected_args,
                        predicted_tool=pred_tool,
                        raw_arguments=raw_args,
                        tool_schemas=schemas,
                    )
                )
    finally:
        if client is not None:
            await client.close()
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python training/eval_harness.py",
        description=(
            "Evaluate Argos tool-calling accuracy against an OpenAI-compatible "
            "endpoint and log to W&B."
        ),
    )
    parser.add_argument(
        "--eval-file",
        type=Path,
        default=Path("training/data/eval.jsonl"),
        help="Path to eval.jsonl",
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:11434/v1",
        help="OpenAI-compatible base URL (e.g. Ollama or vLLM)",
    )
    parser.add_argument("--api-key", default="ollama", help="API key for the endpoint")
    parser.add_argument("--model", default="qwen2.5:7b", help="Model identifier to evaluate")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_REQUEST_TIMEOUT,
        help="Per-request timeout in seconds",
    )
    parser.add_argument("--limit", type=int, default=None, help="Max trajectories to evaluate")
    parser.add_argument("--out-json", type=Path, default=None, help="Optional JSON report path")
    parser.add_argument("--wandb-project", default="argos-sft", help="Weights & Biases project")
    parser.add_argument("--wandb-run-name", default=None, help="W&B run name")
    parser.add_argument("--no-wandb", action="store_true", help="Disable W&B logging")
    parser.add_argument(
        "--log-report",
        type=Path,
        default=None,
        help="log an existing --out-json report to W&B and exit (no model calls)",
    )
    parser.add_argument(
        "--score-answers",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="also score answer turns (no_tool_accuracy: answering without a tool call)",
    )
    parser.add_argument(
        "--offline-replay",
        action="store_true",
        help="Score ground-truth tool calls without calling a live server (smoke test)",
    )
    return parser


ARTIFACT_PREFIX = "wandb-artifact:///"
STEP_COLUMNS = [
    "run_id",
    "step_index",
    "prompt_preview",
    "expected_tool",
    "predicted_tool",
    "json_valid",
    "tool_match",
    "schema_valid",
    "exact_args_match",
    "expects_tool",
    "raw_arguments",
    "error",
]


def _safe_name(model: str) -> str:
    return model.removeprefix(ARTIFACT_PREFIX).replace("/", "-")


def _model_config(model: str) -> dict[str, Any]:
    """W&B treats a config value starting with `wandb-artifact://` as an artifact it
    must resolve (and fails on serverless checkpoint ids), so those go in as a path."""
    if model.startswith(ARTIFACT_PREFIX):
        return {"model_artifact": model.removeprefix(ARTIFACT_PREFIX)}
    return {"model": model}


def log_to_wandb(
    *,
    project: str,
    run_name: str,
    config: dict[str, Any],
    summary: dict[str, Any],
    steps: list[dict[str, Any]],
) -> None:
    import wandb  # type: ignore[import-not-found]

    run = wandb.init(project=project, name=run_name, job_type="eval", config=config)
    try:
        table = wandb.Table(columns=STEP_COLUMNS)
        for row in steps:
            table.add_data(*[row.get(c) for c in STEP_COLUMNS])
        run.log({"eval/predictions": table, **{f"eval/{k}": v for k, v in summary.items()}})
    finally:
        run.finish()


def log_saved_report(path: Path, project: str, run_name: str | None) -> None:
    """Logs a report written by --out-json to W&B without calling the model again."""
    report = json.loads(path.read_text(encoding="utf-8"))
    model = str(report.get("model") or "")
    log_to_wandb(
        project=project,
        run_name=run_name or f"eval-{_safe_name(model)}",
        config={
            **_model_config(model),
            "base_url": report.get("base_url"),
            "eval_file": report.get("eval_file"),
            "from_report": str(path),
        },
        summary=report["summary"],
        steps=report.get("steps") or [],
    )
    print(json.dumps({"model": model, **report["summary"]}, ensure_ascii=False))


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.log_report is not None:
        log_saved_report(args.log_report, args.wandb_project, args.wandb_run_name)
        return
    if not args.eval_file.is_file():
        raise SystemExit(f"Eval file not found: {args.eval_file}")

    records = [
        json.loads(line)
        for line in args.eval_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if args.limit:
        records = records[: args.limit]

    results = asyncio.run(
        evaluate_dataset(
            records,
            base_url=args.base_url,
            api_key=args.api_key,
            model=args.model,
            temperature=args.temperature,
            request_timeout=args.timeout,
            offline_replay=args.offline_replay,
            include_no_tool=args.score_answers,
        )
    )
    summary = summarize_results(results)
    report = {
        "model": args.model,
        "base_url": args.base_url,
        "eval_file": str(args.eval_file),
        "summary": summary,
        "steps": [asdict(r) for r in results],
    }

    if args.out_json:
        args.out_json.parent.mkdir(parents=True, exist_ok=True)
        args.out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if not args.no_wandb:
        log_to_wandb(
            project=args.wandb_project,
            run_name=args.wandb_run_name or f"eval-{_safe_name(args.model)}",
            config={
                **_model_config(args.model),
                "base_url": args.base_url,
                "eval_file": str(args.eval_file),
                "temperature": args.temperature,
            },
            summary=summary,
            steps=[asdict(r) for r in results],
        )

    print(json.dumps({"model": args.model, **summary}, ensure_ascii=False))


if __name__ == "__main__":
    main()
