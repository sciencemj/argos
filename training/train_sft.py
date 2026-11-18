"""Unsloth + TRL SFTTrainer + Weights & Biases training script for Argos tool-calling agents.

Reads OpenAI `messages + tools` JSONL trajectories produced by `argos.sft_export` or
`training/generate_seed_data.py`, formats them with the model's native tool-calling chat
template, fine-tunes a LoRA adapter with Unsloth `FastLanguageModel` and TRL `SFTTrainer`,
logs training/eval loss and dataset/model artifacts to Weights & Biases, and optionally
exports merged 16-bit weights, GGUF quantizations (for Ollama / llama.cpp), or pushes the
trained LoRA / merged model directly to HuggingFace Hub (`--push-to-hub`).

Usage:
    python training/train_sft.py \
        --train-file training/data/train.jsonl \
        --eval-file training/data/eval.jsonl \
        --output-dir training/outputs/qwen2.5-7b-argos \
        --wandb-project argos-sft \
        --push-to-hub username/argos-qwen2.5-7b-sft
"""

from __future__ import annotations

import argparse
import copy
import inspect
import json
import math
import os
from pathlib import Path
from typing import Any

DEFAULT_MODEL = "unsloth/Qwen2.5-7B-Instruct"
LORA_TARGET_MODULES = [
    "q_proj",
    "k_proj",
    "v_proj",
    "o_proj",
    "gate_proj",
    "up_proj",
    "down_proj",
]


def load_jsonl_records(path: Path) -> list[dict[str, Any]]:
    """Loads JSONL records and strips the extra `metadata` key before passing to SFTTrainer."""
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        item = json.loads(line)
        records.append(
            {
                "messages": item["messages"],
                "tools": item.get("tools") or [],
            }
        )
    return records


def normalize_tool_call_arguments(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Returns a copy of `messages` whose `tool_calls[].function.arguments` are dicts.

    The OpenAI format (what `sft_export` writes) stores arguments as a JSON string, but chat
    templates such as Qwen2.5/3/3.5 only render the parameters when `arguments` is a
    mapping; a string is dropped or dumped verbatim, so the model would learn tool calls
    without (or with doubly-encoded) arguments. Strings that are not a JSON object are
    left as they are.
    """
    out = copy.deepcopy(messages)
    for msg in out:
        for call in msg.get("tool_calls") or []:
            fn = call.get("function") if isinstance(call, dict) else None
            if not isinstance(fn, dict):
                continue
            args = fn.get("arguments")
            if isinstance(args, str):
                try:
                    parsed = json.loads(args) if args.strip() else {}
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict):
                    fn["arguments"] = parsed
    return out


# (instruction_part, response_part) markers for Unsloth's `train_on_responses_only`, by
# chat-template family. Everything from a response marker up to the next instruction
# marker is trained on; the rest (system, tool schemas, user turns) is masked out.
RESPONSE_MARKERS: dict[str, tuple[str, str]] = {
    # Qwen2.5 / Qwen3 / Qwen3.5 (ChatML). Tool results are rendered as user turns
    # (`<|im_start|>user\n<tool_response>`), so they are masked too.
    "chatml": ("<|im_start|>user\n", "<|im_start|>assistant\n"),
    # Llama 3.x
    "llama3": (
        "<|start_header_id|>user<|end_header_id|>\n\n",
        "<|start_header_id|>assistant<|end_header_id|>\n\n",
    ),
}


def detect_response_markers(chat_template: str | None) -> tuple[str, str] | None:
    """Picks the `train_on_responses_only` markers matching a tokenizer's chat template."""
    template = chat_template or ""
    if "<|im_start|>" in template:
        return RESPONSE_MARKERS["chatml"]
    if "<|start_header_id|>" in template:
        return RESPONSE_MARKERS["llama3"]
    return None


def compute_warmup_steps(
    *, num_examples: int, batch_size: int, grad_accum: int, epochs: int, ratio: float
) -> int:
    """Warmup steps from a ratio of the total optimizer steps (single device).

    transformers v5 removed `TrainingArguments.warmup_ratio`; `warmup_steps` works on
    both v4 and v5.
    """
    steps_per_epoch = math.ceil(num_examples / max(1, batch_size * grad_accum))
    total = steps_per_epoch * max(1, epochs)
    return max(0, math.ceil(total * ratio))


def format_for_chat_template(
    examples: list[dict[str, Any]],
    tokenizer: Any,
) -> list[dict[str, str]]:
    """Applies the tokenizer's chat template (including `tools`) to produce `text` rows."""
    formatted: list[dict[str, str]] = []
    for ex in examples:
        text = tokenizer.apply_chat_template(
            normalize_tool_call_arguments(ex["messages"]),
            tools=ex.get("tools") or None,
            tokenize=False,
            add_generation_prompt=False,
        )
        formatted.append({"text": text})
    return formatted


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python training/train_sft.py",
        description="Fine-tune an Argos tool-calling model with Unsloth + TRL SFTTrainer + W&B.",
    )
    parser.add_argument(
        "--model", default=DEFAULT_MODEL, help=f"Base model (default: {DEFAULT_MODEL})"
    )
    parser.add_argument(
        "--train-file",
        type=Path,
        default=Path("training/data/train.jsonl"),
        help="Path to train.jsonl",
    )
    parser.add_argument(
        "--eval-file",
        type=Path,
        default=Path("training/data/eval.jsonl"),
        help="Path to eval.jsonl (optional)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("training/outputs/argos-sft"),
        help="Directory to save LoRA adapter and checkpoints",
    )
    parser.add_argument(
        "--max-seq-length",
        type=int,
        default=8192,
        help="Max tokens per example (default 8192). The system prompt plus the ~29 Argos "
        "tool schemas alone take ~4k tokens, so 4096 truncates almost every example",
    )
    parser.add_argument("--load-in-4bit", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--lora-r", type=int, default=16)
    parser.add_argument("--lora-alpha", type=int, default=32)
    parser.add_argument("--lora-dropout", type=float, default=0.0)
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--grad-accum", type=int, default=4)
    parser.add_argument("--lr", type=float, default=2e-4)
    parser.add_argument(
        "--warmup-ratio",
        type=float,
        default=0.05,
        help="Warmup as a fraction of total steps; converted to warmup_steps",
    )
    parser.add_argument(
        "--warmup-steps",
        type=int,
        default=None,
        help="Explicit warmup steps (overrides --warmup-ratio)",
    )
    parser.add_argument(
        "--train-on-responses-only",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Compute loss only on assistant turns (Unsloth train_on_responses_only)",
    )
    parser.add_argument(
        "--instruction-part",
        default=None,
        help="Override the user-turn marker for --train-on-responses-only",
    )
    parser.add_argument(
        "--response-part",
        default=None,
        help="Override the assistant-turn marker for --train-on-responses-only",
    )
    parser.add_argument("--eval-steps", type=int, default=20)
    parser.add_argument("--logging-steps", type=int, default=5)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--wandb-project", default="argos-sft", help="Weights & Biases project name"
    )
    parser.add_argument("--wandb-run-name", default=None, help="W&B run name")
    parser.add_argument(
        "--no-wandb",
        action="store_true",
        help="Disable Weights & Biases logging",
    )
    parser.add_argument(
        "--export-gguf",
        default=None,
        help="Optional GGUF quantization method to export after training (e.g. q4_k_m, q8_0)",
    )
    parser.add_argument(
        "--export-merged",
        action="store_true",
        help="Also save merged 16-bit weights in <output-dir>/merged_16bit",
    )
    parser.add_argument(
        "--push-to-hub",
        default=None,
        metavar="HF_REPO_ID",
        help="Optional HuggingFace Hub repo ID (e.g. org/argos-qwen2.5-7b-sft) to upload weights",
    )
    parser.add_argument(
        "--hub-private",
        action="store_true",
        help="Create/upload the HuggingFace Hub repository as private",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate dataset loading without importing GPU libraries or running training",
    )
    return parser


def push_artifacts_to_hub(
    *,
    repo_id: str,
    private: bool,
    model: Any,
    tokenizer: Any,
    lora_dir: Path,
    merged_dir: Path | None = None,
    gguf_dir: Path | None = None,
) -> str:
    """Uploads merged 16-bit weights (if exported) and/or LoRA adapter to HuggingFace Hub."""
    from huggingface_hub import HfApi  # type: ignore[import-not-found]

    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    api = HfApi(token=token)
    repo_url = api.create_repo(
        repo_id=repo_id,
        repo_type="model",
        private=private,
        exist_ok=True,
    )
    if merged_dir is not None and merged_dir.is_dir():
        api.upload_folder(
            repo_id=repo_id,
            repo_type="model",
            folder_path=str(merged_dir),
            commit_message="Upload merged 16-bit Argos SFT model",
        )
        api.upload_folder(
            repo_id=repo_id,
            repo_type="model",
            folder_path=str(lora_dir),
            path_in_repo="lora_adapter",
            commit_message="Upload LoRA adapter weights",
        )
    elif hasattr(model, "push_to_hub") and hasattr(tokenizer, "push_to_hub"):
        model.push_to_hub(repo_id, private=private, token=token)
        tokenizer.push_to_hub(repo_id, private=private, token=token)
    else:
        api.upload_folder(
            repo_id=repo_id,
            repo_type="model",
            folder_path=str(lora_dir),
            commit_message="Upload LoRA adapter weights",
        )

    if gguf_dir is not None and gguf_dir.is_dir():
        api.upload_folder(
            repo_id=repo_id,
            repo_type="model",
            folder_path=str(gguf_dir),
            path_in_repo="gguf",
            commit_message="Upload GGUF quantized weights",
        )
    return str(repo_url)


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if not args.train_file.is_file():
        raise SystemExit(f"Train file not found: {args.train_file}")

    train_records = load_jsonl_records(args.train_file)
    eval_records = (
        load_jsonl_records(args.eval_file) if args.eval_file and args.eval_file.is_file() else []
    )
    if args.dry_run:
        print(
            json.dumps(
                {
                    "status": "dry_run_ok",
                    "model": args.model,
                    "train_examples": len(train_records),
                    "eval_examples": len(eval_records),
                    "max_seq_length": args.max_seq_length,
                    "train_on_responses_only": args.train_on_responses_only,
                    "push_to_hub": args.push_to_hub,
                    "hub_private": args.hub_private,
                }
            )
        )
        return

    # GPU training libraries (imported lazily so --dry-run works without CUDA)
    import wandb  # type: ignore[import-not-found]
    from datasets import Dataset  # type: ignore[import-not-found]
    from trl import SFTConfig, SFTTrainer  # type: ignore[import-not-found]
    from unsloth import FastLanguageModel  # type: ignore[import-not-found]

    use_wandb = not args.no_wandb
    run = None
    if use_wandb:
        run = wandb.init(
            project=args.wandb_project,
            name=args.wandb_run_name,
            config={
                "model": args.model,
                "max_seq_length": args.max_seq_length,
                "load_in_4bit": args.load_in_4bit,
                "lora_r": args.lora_r,
                "lora_alpha": args.lora_alpha,
                "lora_dropout": args.lora_dropout,
                "epochs": args.epochs,
                "batch_size": args.batch_size,
                "grad_accum": args.grad_accum,
                "lr": args.lr,
                "train_on_responses_only": args.train_on_responses_only,
                "train_examples": len(train_records),
                "eval_examples": len(eval_records),
                "push_to_hub": args.push_to_hub,
                "hub_private": args.hub_private,
            },
        )
    try:
        if run is not None:
            data_artifact = wandb.Artifact("argos-sft-dataset", type="dataset")
            data_artifact.add_file(str(args.train_file), name="train.jsonl")
            if eval_records and args.eval_file:
                data_artifact.add_file(str(args.eval_file), name="eval.jsonl")
            run.log_artifact(data_artifact)

        model, tokenizer = FastLanguageModel.from_pretrained(
            model_name=args.model,
            max_seq_length=args.max_seq_length,
            dtype=None,
            load_in_4bit=args.load_in_4bit,
        )
        model = FastLanguageModel.get_peft_model(
            model,
            r=args.lora_r,
            target_modules=LORA_TARGET_MODULES,
            lora_alpha=args.lora_alpha,
            lora_dropout=args.lora_dropout,
            bias="none",
            use_gradient_checkpointing="unsloth",
            random_state=args.seed,
        )

        train_ds = Dataset.from_list(format_for_chat_template(train_records, tokenizer))
        eval_ds = (
            Dataset.from_list(format_for_chat_template(eval_records, tokenizer))
            if eval_records
            else None
        )
        too_long = sum(
            1
            for row in train_ds
            if len(tokenizer(row["text"], add_special_tokens=False)["input_ids"])
            > args.max_seq_length
        )
        if too_long:
            print(
                f"WARNING: {too_long}/{len(train_ds)} train examples exceed "
                f"--max-seq-length {args.max_seq_length} and will be truncated "
                "(their final assistant turns may be cut off). Raise --max-seq-length."
            )

        warmup_steps = (
            args.warmup_steps
            if args.warmup_steps is not None
            else compute_warmup_steps(
                num_examples=len(train_ds),
                batch_size=args.batch_size,
                grad_accum=args.grad_accum,
                epochs=args.epochs,
                ratio=args.warmup_ratio,
            )
        )

        args.output_dir.mkdir(parents=True, exist_ok=True)
        sft_params = inspect.signature(SFTConfig.__init__).parameters
        seq_key = (
            "max_length"
            if "max_length" in sft_params and "max_seq_length" not in sft_params
            else "max_seq_length"
        )
        sft_kwargs: dict[str, Any] = {
            "output_dir": str(args.output_dir),
            "dataset_text_field": "text",
            seq_key: args.max_seq_length,
            "per_device_train_batch_size": args.batch_size,
            "gradient_accumulation_steps": args.grad_accum,
            "num_train_epochs": args.epochs,
            "learning_rate": args.lr,
            "warmup_steps": warmup_steps,
            "logging_steps": args.logging_steps,
            "eval_strategy": "steps" if eval_ds is not None else "no",
            "eval_steps": args.eval_steps if eval_ds is not None else None,
            "save_strategy": "epoch",
            "seed": args.seed,
            "report_to": "wandb" if use_wandb else "none",
        }
        sft_config = SFTConfig(**sft_kwargs)

        trainer = SFTTrainer(
            model=model,
            processing_class=tokenizer,
            train_dataset=train_ds,
            eval_dataset=eval_ds,
            args=sft_config,
        )
        if args.train_on_responses_only:
            from unsloth.chat_templates import (  # type: ignore[import-not-found]
                train_on_responses_only,
            )

            markers = detect_response_markers(getattr(tokenizer, "chat_template", None))
            instruction_part = args.instruction_part or (markers[0] if markers else None)
            response_part = args.response_part or (markers[1] if markers else None)
            if not instruction_part or not response_part:
                raise SystemExit(
                    "Unknown chat template for --train-on-responses-only: pass "
                    "--instruction-part and --response-part, or --no-train-on-responses-only"
                )
            trainer = train_on_responses_only(
                trainer,
                instruction_part=instruction_part,
                response_part=response_part,
            )
        train_result = trainer.train()
        metrics: dict[str, Any] = dict(train_result.metrics)
        if eval_ds is not None:
            eval_metrics = trainer.evaluate()
            metrics.update(eval_metrics)

        lora_dir = args.output_dir / "lora_adapter"
        model.save_pretrained(str(lora_dir))
        tokenizer.save_pretrained(str(lora_dir))

        merged_dir: Path | None = None
        if args.export_merged:
            merged_dir = args.output_dir / "merged_16bit"
            model.save_pretrained_merged(str(merged_dir), tokenizer, save_method="merged_16bit")

        gguf_dir: Path | None = None
        if args.export_gguf:
            gguf_dir = args.output_dir / "gguf"
            model.save_pretrained_gguf(
                str(gguf_dir),
                tokenizer,
                quantization_method=args.export_gguf,
            )

        if args.push_to_hub:
            hub_url = push_artifacts_to_hub(
                repo_id=args.push_to_hub,
                private=args.hub_private,
                model=model,
                tokenizer=tokenizer,
                lora_dir=lora_dir,
                merged_dir=merged_dir,
                gguf_dir=gguf_dir,
            )
            metrics["hub_repo_id"] = args.push_to_hub
            metrics["hub_url"] = hub_url

        if run is not None:
            run.log(metrics)
            model_artifact = wandb.Artifact("argos-sft-lora", type="model", metadata=metrics)
            model_artifact.add_dir(str(lora_dir))
            run.log_artifact(model_artifact)
    finally:
        if run is not None:
            run.finish()

    print(json.dumps({"output_dir": str(args.output_dir), **metrics}, default=str))


if __name__ == "__main__":
    main()
