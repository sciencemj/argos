"""Unsloth + TRL SFTTrainer + Weights & Biases training script for Argos tool-calling agents.

Reads OpenAI `messages + tools` JSONL trajectories produced by `argos.sft_export` or
`training/generate_seed_data.py`, formats them with the model's native tool-calling chat
template, fine-tunes a LoRA adapter with Unsloth `FastLanguageModel` and TRL `SFTTrainer`,
logs training/eval loss and dataset/model artifacts to Weights & Biases, and optionally
exports merged 16-bit weights or GGUF quantizations (for Ollama / llama.cpp).

Usage:
    python training/train_sft.py \
        --train-file training/data/train.jsonl \
        --eval-file training/data/eval.jsonl \
        --output-dir training/outputs/qwen2.5-7b-argos \
        --wandb-project argos-sft
"""

from __future__ import annotations

import argparse
import inspect
import json
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


def format_for_chat_template(
    examples: list[dict[str, Any]],
    tokenizer: Any,
) -> list[dict[str, str]]:
    """Applies the tokenizer's chat template (including `tools`) to produce `text` rows."""
    formatted: list[dict[str, str]] = []
    for ex in examples:
        text = tokenizer.apply_chat_template(
            ex["messages"],
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
    parser.add_argument("--max-seq-length", type=int, default=4096)
    parser.add_argument("--load-in-4bit", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--lora-r", type=int, default=16)
    parser.add_argument("--lora-alpha", type=int, default=32)
    parser.add_argument("--lora-dropout", type=float, default=0.0)
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--grad-accum", type=int, default=4)
    parser.add_argument("--lr", type=float, default=2e-4)
    parser.add_argument("--warmup-ratio", type=float, default=0.05)
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
        "--dry-run",
        action="store_true",
        help="Validate dataset loading without importing GPU libraries or running training",
    )
    return parser


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
                }
            )
        )
        return

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
                "train_examples": len(train_records),
                "eval_examples": len(eval_records),
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
            "warmup_ratio": args.warmup_ratio,
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
        train_result = trainer.train()
        metrics: dict[str, Any] = dict(train_result.metrics)
        if eval_ds is not None:
            eval_metrics = trainer.evaluate()
            metrics.update(eval_metrics)

        lora_dir = args.output_dir / "lora_adapter"
        model.save_pretrained(str(lora_dir))
        tokenizer.save_pretrained(str(lora_dir))

        if args.export_merged:
            merged_dir = args.output_dir / "merged_16bit"
            model.save_pretrained_merged(str(merged_dir), tokenizer, save_method="merged_16bit")

        if args.export_gguf:
            gguf_dir = args.output_dir / "gguf"
            model.save_pretrained_gguf(
                str(gguf_dir),
                tokenizer,
                quantization_method=args.export_gguf,
            )

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
