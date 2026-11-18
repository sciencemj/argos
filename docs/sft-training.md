# SFT 트래젝토리 추출 및 툴콜 모델 학습·평가 가이드

Argos 네이티브 하네스 에이전트 실행 기록을 OpenAI `messages + tools` JSONL 형식으로 추출하고, Unsloth QLoRA + Weights & Biases(W&B)로 파인튜닝한 뒤, OpenAI 호환 엔드포인트(Ollama·vLLM 등)에서 툴콜 정확도를 평가하는 워크플로우입니다.

---

## 1. 실제 실행 로그에서 SFT 데이터 추출 (`sft_export`)

완료된 `AgentRun`과 `agent_run_step` 기록을 읽어 `{"messages": [...], "tools": [...]}` 형태의 JSONL로 내보냅니다. 대화·툴 출력에 포함된 API 키·토큰·Bearer 헤더·개인키 등은 기본적으로 `[REDACTED]`로 마스킹되며, `run_id` 해시 기준으로 `train`/`eval` 분할이 고정됩니다.

### CLI 사용법

`backend/` 디렉터리(또는 `--project backend`)에서 실행합니다.

```bash
# train.jsonl / eval.jsonl 로 분할 저장
uv run --project backend python -m argos.sft_export --out-dir training/data

# 단일 JSONL 파일로 저장
uv run --project backend python -m argos.sft_export --out training/data/all.jsonl
```

주요 옵션:
- `--out-dir <경로>` / `--out <경로>` (둘 중 하나 필수): 출력 디렉터리(`train.jsonl`, `eval.jsonl` 생성) 또는 단일 `.jsonl` 파일 경로
- `--db <경로>`: SQLite DB 경로 지정 (기본값: `ARGOS_DB_PATH` / 앱 설정)
- `--min-tool-steps <정수>`: 최소 툴 호출 단계 수 필터 (기본 `0`)
- `--eval-ratio <실수>`: `eval` 분할 비율 (기본 `0.1`)
- `--include-failed`: 성공한 실행뿐 아니라 실패/중단 실행도 포함
- `--native-only`: Argos 네이티브 하네스 에이전트 실행만 추출
- `--limit <정수>`: 최신 N개 실행만 추출
- `--no-mask`: 비밀 마스킹 비활성화 (권장하지 않음)
- `--no-metadata`: 레코드의 `metadata` 필드 제외

### 로컬 API (`GET /api/v1/sft/export`)

루프백(`127.0.0.1` / `localhost`, Tailscale 원격 헤더 없음)에서만 허용됩니다.

```bash
curl -fsSL "http://127.0.0.1:8000/api/v1/sft/export?split=train&native_only=true" -o train.jsonl
```

쿼리 파라미터: `split` (`all` | `train` | `eval`, 기본 `all`), `min_tool_steps` (기본 `0`), `eval_ratio` (기본 `0.1`), `only_success` (기본 `true`), `native_only` (기본 `false`), `mask` (기본 `true`), `metadata` (기본 `true`), `limit` (`1..10000`).

---

## 2. 학습·평가 환경 설치 (`training/`)

GPU 환경(Linux CUDA 등)에서 `training/requirements.txt`를 설치합니다.

```bash
pip install -r training/requirements.txt
```

포함 패키지: `unsloth>=2024.11.0`, `trl>=0.12.0,<0.17.0`, `transformers>=4.46.0`, `datasets>=3.1.0`, `wandb>=0.18.0`, `openai>=1.54.0`.

---

## 3. 합성 시드 데이터 생성 (`generate_seed_data.py`)

초기 실행 로그가 부족할 때 `argos.sft_export.tool_specs()`의 실제 툴 스키마를 기반으로 다중 턴 툴콜 시드 데이터를 생성합니다.

```bash
uv run --project backend python training/generate_seed_data.py \
  --out-dir training/data \
  --count 60 \
  --eval-ratio 0.2 \
  --seed 42
```

옵션:
- `--out-dir`: 출력 디렉터리 (기본 `training/data`)
- `--count`: 생성할 총 트래젝토리 수 (기본 `60`)
- `--eval-ratio`: 평가 세트 비율 (기본 `0.2`)
- `--seed`: 난수 시드 (기본 `42`)

---

## 4. Unsloth QLoRA SFT + W&B 학습 (`train_sft.py`)

`FastLanguageModel`과 TRL `SFTTrainer`로 LoRA 어댑터를 학습하고, W&B에 손실·평가 지표 및 `argos-sft-lora` 모델 아티팩트를 기록합니다.

```bash
# GPU/라이브러리 없이 데이터 로딩만 사전 검증
python training/train_sft.py --train-file training/data/train.jsonl --eval-file training/data/eval.jsonl --dry-run

# QLoRA 학습 + W&B 로깅 + GGUF 내보내기 (Ollama용)
python training/train_sft.py \
  --model unsloth/Qwen2.5-7B-Instruct \
  --train-file training/data/train.jsonl \
  --eval-file training/data/eval.jsonl \
  --output-dir training/outputs/qwen2.5-7b-argos \
  --wandb-project argos-sft \
  --export-gguf q4_k_m
```

주요 옵션:
- `--model`: 베이스 모델 (기본 `unsloth/Qwen2.5-7B-Instruct`)
- `--train-file` / `--eval-file`: 학습·평가 JSONL 경로 (기본 `training/data/train.jsonl`, `training/data/eval.jsonl`)
- `--output-dir`: LoRA 어댑터 및 체크포인트 저장 경로 (기본 `training/outputs/argos-sft`)
- `--max-seq-length` (`4096`), `--load-in-4bit` / `--no-load-in-4bit` (기본 `True`)
- `--lora-r` (`16`), `--lora-alpha` (`32`), `--lora-dropout` (`0.0`)
- `--epochs` (`3`), `--batch-size` (`2`), `--grad-accum` (`4`), `--lr` (`2e-4`), `--warmup-ratio` (`0.05`), `--eval-steps` (`20`), `--logging-steps` (`5`), `--seed` (`42`)
- `--wandb-project` (`argos-sft`), `--wandb-run-name`, `--no-wandb`
- `--export-merged`: `<output-dir>/merged_16bit`에 병합 16-bit 가중치 저장
- `--export-gguf <방식>`: `<output-dir>/gguf`에 GGUF 양자화 내보내기 (예: `q4_k_m`, `q8_0`)
- `--dry-run`: GPU 라이브러리 임포트 없이 데이터셋 로딩만 검증

---

## 5. 하네스 툴콜 평가 (`eval_harness.py`)

Ollama, vLLM 등 OpenAI 호환 엔드포인트에 `eval.jsonl` 턴을 실행해 베이스 모델과 SFT 모델의 툴콜 품질을 비교합니다.

```bash
# Ollama 로컬 모델 평가 + W&B 테이블 기록
python training/eval_harness.py \
  --eval-file training/data/eval.jsonl \
  --base-url http://127.0.0.1:11434/v1 \
  --model qwen2.5:7b \
  --wandb-project argos-sft \
  --out-json training/outputs/eval-base.json

# 라이브 서버 없이 정답 트래젝토리로 스모크 테스트
python training/eval_harness.py --eval-file training/data/eval.jsonl --offline-replay --no-wandb
```

측정 지표:
- `json_validity_rate`: 툴 호출 인자가 유효한 JSON 객체인 비율
- `tool_selection_accuracy`: 기대한 툴 이름을 정확히 선택한 턴 비율
- `schema_compliance_rate`: 인자가 해당 툴의 JSON Schema(타입·필수 필드)를 만족하는 비율
- `exact_argument_match_rate`: 정답 인자 키/값과 정확히 일치하는 턴 비율

주요 옵션:
- `--eval-file` (기본 `training/data/eval.jsonl`), `--base-url` (기본 `http://127.0.0.1:11434/v1`), `--api-key` (기본 `ollama`), `--model` (기본 `qwen2.5:7b`)
- `--temperature` (`0.0`), `--timeout`, `--limit`, `--out-json`
- `--wandb-project` (`argos-sft`), `--wandb-run-name`, `--no-wandb`, `--offline-replay`

---

## 6. GPU 없이 W&B Serverless SFT (`train_wandb_serverless.py`)

로컬 GPU가 없을 때(예: Mac) W&B Training(CoreWeave Serverless Training)에 학습을 맡기는 경로예요. OpenPipe ART의 `ServerlessBackend`가 `train.jsonl`을 올려 LoRA 어댑터를 학습하고, 끝난 체크포인트는 바로 OpenAI 호환 엔드포인트로 서빙돼요. 학생 플랜의 W&B Training 크레딧이 쓰여요.

### 설치

```bash
python3 -m venv .venv-train && source .venv-train/bin/activate
pip install -r training/requirements-serverless.txt   # openpipe-art, wandb, openai (GPU 패키지 없음)
export WANDB_API_KEY=...                               # https://wandb.ai/authorize
```

### 먼저 dry-run (크레딧 0)

ART를 불러오지 않고 데이터와 설정만 검사해요: JSONL 형식, 역할, 툴콜 인자 JSON, `tools`에 없는 툴 호출, 짝 없는 툴 결과, 마지막 턴이 어시스턴트인지, 지원 베이스 모델인지. 예상 옵티마이저 스텝 수와 학습 후 붙일 주소 형식도 보여 줘요. 문제가 있으면 종료 코드 1이에요.

```bash
python training/train_wandb_serverless.py --dry-run
```

### 학습

```bash
python training/train_wandb_serverless.py \
  --train-file training/data/train.jsonl \
  --eval-file training/data/eval.jsonl \
  --project argos-sft --model-name argos-harness \
  --base-model Qwen/Qwen3.8-27B --epochs 3 --batch-size 2 --lr 2e-4 \
  --out-json training/outputs/serverless.json
```

- 베이스 모델: `Qwen/Qwen3.8-27B`(기본), `Qwen/Qwen3.6-35B-A3B`, `meta-llama/Llama-3.1-8B-Instruct`, `google/gemma-4-26B-A4B-it`, `nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B`. 목록에 없는 모델은 `--allow-unlisted-model`.
- LoRA rank·alpha는 서비스가 베이스 모델마다 고정해요(옵션으로 바꿀 수 없음).
- `--assistant-turns all|last`: 손실을 모든 어시스턴트 턴(툴콜 포함)에 줄지, 마지막 답에만 줄지.
- `--training-base-url`: 기본은 문서의 `https://forge.coreweave.com/api/training/v1`. `""`를 주면 설치된 ART의 기본 주소를 써요.
- serverless SFT에는 eval loss가 없어요. `eval.jsonl`은 검사만 하고, 학습 뒤 `eval_harness.py`로 채점해요.

끝나면 다음을 출력해요(`--out-json`에도 저장):
- `model_id`: `wandb-artifact:///<entity>/<project>/<model-name>:step<N>`
- `inference.base_url`: OpenAI 호환 주소 (API 키는 `WANDB_API_KEY`)
- `artifacts_url`: W&B 프로젝트의 아티팩트 페이지
- `inference.eval_harness`: 바로 붙여 넣을 평가 명령

### 평가

```bash
python training/eval_harness.py --eval-file training/data/eval.jsonl \
  --base-url https://forge.coreweave.com/api/training/v1 --api-key "$WANDB_API_KEY" \
  --model "wandb-artifact:///<entity>/argos-sft/argos-harness:step<N>" \
  --wandb-project argos-sft --out-json training/outputs/eval-serverless.json
```

같은 명령에서 `--model`만 베이스 모델로 바꾸면 SFT 전후를 비교할 수 있어요.

### Argos에 연결

1. 설정 → 모델 연결 → 연결 추가: 종류 "OpenAI 호환", 주소 `inference.base_url`, API 키 = `WANDB_API_KEY`(키체인에 저장돼요). 위치는 "클라우드"로 표시돼요.
2. 설정 → 커스텀 에이전트 → 봇 만들기: 기반 에이전트 "Argos 하네스", 모델 연결은 위 연결, 모델은 `model_id`. 연결의 모델 목록에 학습한 체크포인트가 안 보이면 YAML 가져오기로 `backend: native`, `model: wandb-artifact:///…`를 지정해요.
3. 채널 기본 에이전트로 정하거나 `@이름`으로 불러 써요. 툴 승인 규칙(Auto-Edit / Ask)은 다른 하네스 에이전트와 같아요.
