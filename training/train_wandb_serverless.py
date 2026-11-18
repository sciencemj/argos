"""Serverless SFT on W&B Training (CoreWeave) for Argos tool-calling agents.

No local GPU: OpenPipe ART's `ServerlessBackend` uploads the trajectories and trains a
LoRA adapter on W&B's managed cluster, and every checkpoint is served right away through
an OpenAI-compatible endpoint. Point `training/eval_harness.py` or an Argos model
provider ("OpenAI 호환") at the printed `base_url` + model id to use it.

Data: `training/data/train.jsonl` / `eval.jsonl` in the OpenAI `messages` + `tools`
format written by `argos.sft_export` and `training/generate_seed_data.py` (the extra
`metadata` key is ignored by ART). ART has no eval loss for serverless SFT, so the eval
file is validated here and scored afterwards with `eval_harness.py`.

`--dry-run` validates the files and the configuration and prints what would run; it
imports nothing from ART and spends no credits.

Package API used (openpipe-art 0.5.x): `art.TrainableModel`,
`art.serverless.backend.ServerlessBackend`, `art.utils.sft.train_sft_from_file`,
`TrainableModel.get_step()` / `get_inference_name(step)`.

Usage:
    export WANDB_API_KEY=...
    python training/train_wandb_serverless.py --dry-run
    python training/train_wandb_serverless.py --project argos-sft --model-name argos-harness
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Base models W&B Serverless Training lists for SFT/RL (CoreWeave docs, "Available
# models"). LoRA rank and alpha are fixed per base model by the service.
SUPPORTED_BASE_MODELS = (
    "Qwen/Qwen3.8-27B",
    "Qwen/Qwen3.6-35B-A3B",
    "meta-llama/Llama-3.1-8B-Instruct",
    "google/gemma-4-26B-A4B-it",
    "nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B",
)
DEFAULT_BASE_MODEL = "Qwen/Qwen3.8-27B"
# The documented training/inference endpoint. ART's own default may be an older host;
# pass --training-base-url "" to use whatever the installed ART version defaults to.
DEFAULT_TRAINING_BASE_URL = "https://forge.coreweave.com/api/training/v1"
ROLES = {"system", "user", "assistant", "tool"}


# --- dataset validation (no ART needed) ----------------------------------------------


@dataclass
class FileReport:
    path: str
    examples: int = 0
    errors: list[str] = field(default_factory=list[str])
    tool_calls: Counter[str] = field(default_factory=Counter[str])
    with_tools: int = 0
    approx_tokens: int = 0  # characters / 4, a rough budget figure
    longest_messages: int = 0

    def ok(self) -> bool:
        return self.examples > 0 and not self.errors

    def summary(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "examples": self.examples,
            "with_tool_schemas": self.with_tools,
            "tool_calls": dict(self.tool_calls.most_common()),
            "approx_tokens": self.approx_tokens,
            "longest_conversation_messages": self.longest_messages,
            "errors": self.errors[:20],
            "error_count": len(self.errors),
        }


def _check_example(data: Any, where: str) -> list[str]:
    """Problems that would break training or teach the model broken tool calls."""
    if not isinstance(data, dict):
        return [f"{where}: not a JSON object"]
    messages = data.get("messages")
    if not isinstance(messages, list) or not messages:
        return [f"{where}: 'messages' must be a non-empty list"]
    problems: list[str] = []
    tools = data.get("tools")
    known: set[str] | None = None
    if tools is not None:
        if not isinstance(tools, list):
            problems.append(f"{where}: 'tools' must be a list")
        else:
            known = set()
            for i, tool in enumerate(tools):
                fn = tool.get("function") if isinstance(tool, dict) else None
                if not isinstance(fn, dict) or not fn.get("name"):
                    problems.append(f"{where}: tools[{i}] is not an OpenAI function tool")
                else:
                    known.add(str(fn["name"]))
    open_calls: set[str] = set()
    assistant_turns = 0
    for i, message in enumerate(messages):
        at = f"{where} messages[{i}]"
        if not isinstance(message, dict) or message.get("role") not in ROLES:
            problems.append(f"{at}: role must be one of {sorted(ROLES)}")
            continue
        role = message["role"]
        if role == "assistant":
            assistant_turns += 1
            for call in message.get("tool_calls") or []:
                fn = call.get("function") if isinstance(call, dict) else None
                if not isinstance(fn, dict) or not fn.get("name"):
                    problems.append(f"{at}: tool call without a function name")
                    continue
                if known is not None and fn["name"] not in known:
                    problems.append(f"{at}: calls '{fn['name']}', not in this example's tools")
                try:
                    if not isinstance(json.loads(fn.get("arguments") or "{}"), dict):
                        problems.append(f"{at}: '{fn['name']}' arguments are not a JSON object")
                except (TypeError, ValueError):
                    problems.append(f"{at}: '{fn['name']}' arguments are not valid JSON")
                if call.get("id"):
                    open_calls.add(str(call["id"]))
        elif role == "tool":
            call_id = message.get("tool_call_id")
            if call_id not in open_calls:
                problems.append(f"{at}: tool result for unknown call id {call_id!r}")
            open_calls.discard(str(call_id))
    if assistant_turns == 0:
        problems.append(f"{where}: no assistant turn to learn from")
    last = messages[-1]
    if not isinstance(last, dict) or last.get("role") != "assistant":
        problems.append(f"{where}: the last message should be the assistant's answer")
    return problems


def validate_file(path: Path, max_examples: int | None = None) -> FileReport:
    report = FileReport(str(path))
    if not path.is_file():
        report.errors.append(f"{path}: file not found")
        return report
    with path.open(encoding="utf-8") as lines:
        for number, line in enumerate(lines, 1):
            if not line.strip():
                continue
            if max_examples is not None and report.examples >= max_examples:
                break
            where = f"{path.name}:{number}"
            try:
                data = json.loads(line)
            except ValueError as exc:
                report.errors.append(f"{where}: invalid JSON ({exc})")
                continue
            report.errors += _check_example(data, where)
            report.examples += 1
            report.approx_tokens += len(line) // 4
            messages = data.get("messages") if isinstance(data, dict) else None
            if isinstance(messages, list):
                report.longest_messages = max(report.longest_messages, len(messages))
                for message in messages:
                    if isinstance(message, dict):
                        for call in message.get("tool_calls") or []:
                            fn = call.get("function") if isinstance(call, dict) else None
                            if isinstance(fn, dict) and fn.get("name"):
                                report.tool_calls[str(fn["name"])] += 1
            if isinstance(data, dict) and data.get("tools"):
                report.with_tools += 1
    return report


# --- the API key ---------------------------------------------------------------------------


def resolve_api_key() -> tuple[str | None, str]:
    """(key, where it came from): WANDB_API_KEY, else the key `wandb login` saved in
    ~/.netrc (api.wandb.ai). The key itself is never printed."""
    key = os.environ.get("WANDB_API_KEY")
    if key:
        return key, "env:WANDB_API_KEY"
    try:
        import netrc

        entry = netrc.netrc().authenticators("api.wandb.ai")
    except (OSError, netrc.NetrcParseError):
        entry = None
    if entry and entry[2]:
        return entry[2], "~/.netrc (wandb login)"
    return None, "none"


# --- training ----------------------------------------------------------------------------


def inference_settings(base_url: str, model_id: str) -> dict[str, Any]:
    """What to paste into eval_harness.py and an Argos model provider."""
    return {
        "base_url": base_url,
        "model": model_id,
        "api_key_env": "WANDB_API_KEY",
        "eval_harness": (
            "python training/eval_harness.py --eval-file training/data/eval.jsonl "
            f'--base-url {base_url} --api-key "$WANDB_API_KEY" --model "{model_id}"'
        ),
        "argos_provider": {
            "kind": "openai",
            "base_url": base_url,
            "api_key": "<your WANDB_API_KEY, stored in the macOS Keychain by Argos>",
            "agent_backend": "native",
            "agent_model": model_id,
        },
    }


async def train(args: argparse.Namespace, api_key: str) -> dict[str, Any]:
    import art  # openpipe-art; imported only for a real run
    from art.serverless.backend import ServerlessBackend
    from art.utils.sft import train_sft_from_file

    backend = ServerlessBackend(api_key=api_key, base_url=args.training_base_url or None)
    model = art.TrainableModel(
        name=args.model_name,
        project=args.project,
        entity=args.entity,
        run_name=args.run_name or args.model_name,
        base_model=args.base_model,
    )
    try:
        await model.register(backend)
        start = await model.get_step()
        print(
            f"registered {args.entity or model.entity}/{args.project}/{args.model_name} "
            f"(base {args.base_model}, starting at step {start})",
            flush=True,
        )
        await train_sft_from_file(
            model=model,
            file_path=str(args.train_file),
            epochs=args.epochs,
            batch_size=args.batch_size,
            peak_lr=args.lr,
            schedule_type=args.schedule,
            warmup_ratio=args.warmup_ratio,
            assistant_turns=args.assistant_turns,
            verbose=True,
        )
        step = await model.get_step()
        model_id = model.get_inference_name(step)
        base_url = str(getattr(backend, "_base_url", "") or args.training_base_url)
    finally:
        await backend.close()
    entity = model.entity or args.entity or "<entity>"
    return {
        "step": step,
        "model_id": model_id,
        "artifact": f"wandb-artifact:///{entity}/{args.project}/{args.model_name}:step{step}",
        "project_url": f"https://wandb.ai/{entity}/{args.project}",
        "artifacts_url": f"https://wandb.ai/{entity}/{args.project}/artifacts",
        "inference": inference_settings(base_url.rstrip("/"), model_id),
    }


# --- CLI ------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python training/train_wandb_serverless.py",
        description=(
            "Supervised fine-tuning of an Argos tool-calling agent on W&B Serverless "
            "Training (OpenPipe ART ServerlessBackend). No local GPU needed; the trained "
            "LoRA checkpoint is served through an OpenAI-compatible endpoint."
        ),
    )
    data = parser.add_argument_group("data")
    data.add_argument("--train-file", type=Path, default=Path("training/data/train.jsonl"))
    data.add_argument("--eval-file", type=Path, default=Path("training/data/eval.jsonl"))
    data.add_argument(
        "--max-examples", type=int, default=None, help="validate only the first N per file"
    )
    wb = parser.add_argument_group("W&B")
    wb.add_argument("--project", default="argos-sft", help="W&B project (metrics + adapter)")
    wb.add_argument("--entity", default=None, help="W&B entity/team (default: your default)")
    wb.add_argument("--model-name", default="argos-harness", help="name of the trained model")
    wb.add_argument("--run-name", default=None, help="W&B run name (default: model name)")
    wb.add_argument(
        "--training-base-url",
        default=DEFAULT_TRAINING_BASE_URL,
        help=f'Serverless Training API (default {DEFAULT_TRAINING_BASE_URL}; "" = ART default)',
    )
    model = parser.add_argument_group("model")
    model.add_argument(
        "--base-model",
        default=DEFAULT_BASE_MODEL,
        help=f"one of: {', '.join(SUPPORTED_BASE_MODELS)}. LoRA rank/alpha are fixed by "
        "the service for each base model, so they are not options here.",
    )
    model.add_argument(
        "--allow-unlisted-model", action="store_true", help="skip the supported-model check"
    )
    hp = parser.add_argument_group("training")
    hp.add_argument("--epochs", type=int, default=3)
    hp.add_argument("--batch-size", type=int, default=2)
    hp.add_argument("--lr", type=float, default=2e-4, help="peak learning rate")
    hp.add_argument("--schedule", choices=("linear", "cosine", "constant"), default="linear")
    hp.add_argument("--warmup-ratio", type=float, default=0.1)
    hp.add_argument(
        "--assistant-turns",
        choices=("all", "last"),
        default="all",
        help="which assistant turns carry loss (all: tool calls and the final answer)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate data and settings only: no ART import, no upload, no credits",
    )
    parser.add_argument("--out-json", type=Path, default=None, help="write the result here")
    return parser


def check_config(args: argparse.Namespace) -> list[str]:
    problems: list[str] = []
    if args.base_model not in SUPPORTED_BASE_MODELS and not args.allow_unlisted_model:
        problems.append(
            f"base model {args.base_model!r} is not in the supported list "
            "(use --allow-unlisted-model if W&B added it)"
        )
    if args.epochs < 1:
        problems.append("--epochs must be at least 1")
    if args.batch_size < 1:
        problems.append("--batch-size must be at least 1")
    if not 0 < args.lr < 1:
        problems.append("--lr must be between 0 and 1")
    if not 0 <= args.warmup_ratio < 1:
        problems.append("--warmup-ratio must be in [0, 1)")
    return problems


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    train_report = validate_file(args.train_file, args.max_examples)
    eval_report = validate_file(args.eval_file, args.max_examples)
    problems = check_config(args) + train_report.errors
    api_key, key_source = resolve_api_key()
    key_set = api_key is not None
    batches = -(-train_report.examples * args.epochs // args.batch_size)
    plan: dict[str, Any] = {
        "dry_run": args.dry_run,
        "wandb_api_key_set": key_set,
        "wandb_api_key_source": key_source,
        "model": {
            "base_model": args.base_model,
            "project": args.project,
            "entity": args.entity,
            "name": args.model_name,
            "training_base_url": args.training_base_url or "(ART default)",
        },
        "training": {
            "epochs": args.epochs,
            "batch_size": args.batch_size,
            "peak_lr": args.lr,
            "schedule": args.schedule,
            "warmup_ratio": args.warmup_ratio,
            "assistant_turns": args.assistant_turns,
            "optimizer_steps": batches,
        },
        "train": train_report.summary(),
        "eval": eval_report.summary(),
        # Not an error: eval.jsonl is only scored after training (eval_harness.py).
        "eval_ok": eval_report.ok(),
        "problems": problems,
    }
    if args.dry_run:
        if not key_set:
            plan["note"] = (
                "No W&B API key (WANDB_API_KEY or `wandb login`): a real run would stop here."
            )
        plan["expected_inference"] = inference_settings(
            args.training_base_url or "<ART default base_url>",
            f"wandb-artifact:///{args.entity or '<entity>'}/{args.project}/"
            f"{args.model_name}:step<N>",
        )
        output = json.dumps(plan, ensure_ascii=False, indent=2)
        print(output)
        if args.out_json:
            args.out_json.parent.mkdir(parents=True, exist_ok=True)
            args.out_json.write_text(output + "\n", encoding="utf-8")
        return 1 if problems or not train_report.ok() else 0
    if problems or not train_report.ok():
        print(json.dumps(plan, ensure_ascii=False, indent=2), file=sys.stderr)
        print("fix the problems above before spending credits", file=sys.stderr)
        return 1
    if api_key is None:
        print(
            "No W&B API key: export WANDB_API_KEY or run `wandb login` "
            "(https://wandb.ai/authorize)",
            file=sys.stderr,
        )
        return 2
    print(f"W&B API key from {key_source}", file=sys.stderr)
    result = asyncio.run(train(args, api_key))
    output = json.dumps({"plan": plan, "result": result}, ensure_ascii=False, indent=2)
    print(output)
    if args.out_json:
        args.out_json.parent.mkdir(parents=True, exist_ok=True)
        args.out_json.write_text(output + "\n", encoding="utf-8")
    print(
        "\nServe it from Argos or eval_harness.py:\n"
        f"  base_url: {result['inference']['base_url']}\n"
        f"  model:    {result['model_id']}\n"
        "  api key:  WANDB_API_KEY",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
