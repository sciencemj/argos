# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Argos: 개인용 학업·프로젝트 대시보드. 채널(과목·프로젝트)별 채팅 피드·칸반·캘린더, 인박스 AI 분류,
에이전트(Argos 네이티브 하네스·Hermes·Claude·Codex·로컬 모델) 대화와 코딩 잡, 에이전트용 MCP 서버.
v0.2(`feat/v0.2-agent-harness`)부터 채널 첫 화면은 채팅 우선(가안 A)이고, 툴콜 모델 SFT 파이프라인(`training/`)이 있다.
이 Mac에서만 돌고 외부 접근은 Tailscale serve(tailnet 전용)로만.

## 작업 규칙 (docs/PLAN.md §0 요약)

- `docs/PLAN.md`의 Phase를 순서대로 하나씩. Phase가 끝나면 멈추고 보고한 뒤 승인받는다.
  Phase 범위 밖 기능은 미리 만들지 않는다. 미룬 개선은 `docs/backlog.md`.
- 커밋은 Phase 끝까지 모으지 말고 기능 단위(테스트가 통과하는 한 덩어리)를 구현할 때마다 바로 한다(2026-10-12 사용자 요청).
  푸시·main 합치기는 따로 확인받는다.
- PLAN과 다른 선택, PLAN에 없는 결정은 `docs/decisions.md` 표에 날짜·결정·이유 한 줄로 남긴다.
- UI 문구와 문서는 한국어, 코드·식별자·커밋 메시지는 영어.
- 외부 시스템(에이전트 CLI, 캘린더, 옵시디언 볼트)을 건드리는 코드는 테스트용 fake와 함께.
  CLI·SDK 옵션은 추측하지 말고 설치된 버전의 `--help`나 공식 문서로 확인.
- PLAN §9 "확인 필요 사항"은 임의로 정하지 말고 설정으로 빼거나 사용자에게 묻는다.
- 사용자의 시스템 설정(`~/.hermes`, `~/.claude`, `~/.codex`)은 확인받고 수정. 서버는 `127.0.0.1`에만
  바인딩, funnel 금지. 프로세스는 PID로만 종료(패턴 `pkill`은 다른 프로젝트 서버까지 죽인다).
- **다른 사람도 쓸 수 있게 만든다**(2026-09-25 사용자 요청). 볼트 경로, 폴더 구조, 캘린더 이름, 할 일
  표기, 호스트 주소 등 개인 환경에 따른 값은 설정(기본값 포함)이나 자동 감지(예: 옵시디언 설정을 읽기 전용으로)로
  두고 코드·UI 문구에 박지 않는다.
- **패키지·실행 도구**: Python(백엔드, `training/` 스크립트, 일회성 스크립트)은 **`uv`**만 쓴다:
  `cd backend && uv sync`, `uv run ...`, 저장소 루트에서는 `uv run --project backend python ...`, 별도 환경은
  `uv venv` + `uv pip install -r ...`. bare `python`/`python3`/`pip`는 쓰지 않는다. 프런트엔드·데스크톱 JS는
  **`bun`**만 쓴다: `bun install`, `bun run <script>`, `bunx <tool>`. `npm`/`npx`/`yarn`/`pnpm`은 쓰지 않는다
  (lockfile은 `bun.lock`).
- UI는 `docs/design/*.dc.html`(라이트/다크 쌍) 디자인 캔버스를 따른다. 색은 `frontend/src/index.css`의
  토큰(CSS 변수)만 쓴다.

## 명령

```bash
make install      # uv sync + bun install
make dev          # alembic upgrade 후 backend :8100 + frontend :5273 (Vite가 /api, /ws 프록시)
make app          # 데스크톱 앱: frontend build + PyInstaller 서버 + Tauri → desktop/build/Argos.{app,dmg}
make test         # pytest + vitest
make lint         # ruff check/format --check + pyright(strict) + biome + tsc
make format
make api-types    # 백엔드 OpenAPI → frontend/src/api-types.ts. API 모델을 바꾸면 반드시 실행 후 커밋(CI가 검사)

cd backend && uv run pytest tests/test_jobs.py::test_job_moves_card_and_reports_in_thread -q
cd backend && uv run alembic revision --autogenerate -m "..."   # 모델 변경 시, 그다음 alembic check
cd frontend && bunx vitest run src/dates.test.ts

# 툴콜 SFT (docs/sft-training.md)
uv run --project backend python -m argos.sft_export --out-dir training/data      # 실제 run → train/eval.jsonl
uv run --project backend python training/generate_seed_data.py --out-dir training/data  # 합성 시드 데이터
uv venv training/.venv && uv pip install --python training/.venv -r training/requirements-serverless.txt
```

포트 8000은 데스크톱 앱(`docs/desktop.md`, 데이터는 `~/Library/Application Support/Argos`) 몫이다. 에이전트
MCP 등록이 8000을 가리키므로 개발 서버는 8100/5273을 쓴다. 개발 화면은 `/argos/` 아래(`http://127.0.0.1:5273/argos/`,
`ARGOS_BASE_PATH`로 변경, 빌드는 항상 `/`). Tailscale serve는 개발용으로 그 경로를 연다:
`tailscale serve --bg --set-path /argos http://127.0.0.1:5273/argos` (앱 기능 아님, 모바일 PWA 테스트용). 릴리스: `CHANGELOG.md`에 `## x.y.z · 날짜` 섹션을 먼저 쓰고 `make release VERSION=x.y.z` → 태그 → `.github/workflows/release.yml`(그 섹션이 릴리스 노트·업데이트 알림, 앱의 "새 기능" 모달).

개발 서버가 launchd로 떠 있을 수 있다(설정 → 백업과 자동 실행, `~/Library/LaunchAgents/app.argos.server.plist`가
`make dev`를 실행, 기록은 `backend/data/logs/service.log`). 그때는 `make dev`를 또 띄우지 말고(포트 충돌) 코드
변경은 자동 리로드에 맡긴다. 다시 시작: `launchctl kickstart -k gui/$(id -u)/app.argos.server`.
모델을 바꾸면 **서버가 리로드되기 전에** `uv run alembic upgrade head`를 먼저 적용한다(새 테이블을 읽는 시작 코드가 실패함).

CI(`.github/workflows/ci.yml`): backend(ruff, pyright, pytest, `alembic check`), frontend(biome, tsc,
vitest, build), api-types 최신 여부.

## 아키텍처

### 백엔드 (`backend/src/argos`, FastAPI + async SQLAlchemy + SQLite WAL)

- **`services.py`가 유일한 쓰기 경로.** `_create`/`_update`/`_delete`가 한 트랜잭션에서
  `activity_log`(actor: `user`, `agent:<이름>`, `system`)를 남기고 커밋 후 `hub`로 WS 이벤트
  (`object.created|updated|deleted`)를 보낸다. API·채팅·MCP·러너 모두 여기를 거친다. 새 쓰기 기능도
  이 헬퍼로 만들어야 화면이 실시간 갱신되고 "발자국"이 남는다. 오류는 `NotFoundError`/`InvalidError`/
  `ConflictError` → API에서 404/422/409.
- **`chat.py`**: 채널 메시지 입력의 라우팅. 스레드 답글 → 고정(sticky) 에이전트, `@멘션`/DM → 대화,
  그 외는 `commands.py`로 슬래시 명령(`/task /event /note /ask /job`) 파싱, 명령 없는 일반 메시지는
  인박스에만 저장하고 분류를 백그라운드로(`classifier.py`, Ollama 또는 Hermes). 결과 `Posted`가
  API에 "어느 에이전트를 부를지 / 어떤 잡을 시작할지"를 알려준다.
- **`runner.py`**: 에이전트 실행. `agent_run` 행 + 답변 메시지를 만들고 `agent.token|status|done|error`를
  WS로 스트리밍, 답 본문은 끝날 때 한 번 저장. 잡(`kind=job`)은 세마포어(`job_concurrency`)로
  대기(`queued`), 카드 이동(todo → in_progress → review), 도구 단계 로그를 남긴다. 취소는 asyncio 태스크
  취소. 서버 시작 시 남은 running/queued run은 error 처리.
- **`attachments.py`**: 채팅 첨부(Phase 13). 파일은 `data_dir/attachments/<id>`(이름은 DB에만), 종류는 내용으로
  판별. `Turn.attachments` → `split_attachments`/`with_attachments`(agents.py)가 이미지·PDF 파트와 본문 텍스트로
  나누고 어댑터가 백엔드별 형식으로 보낸다. 미전송 업로드는 서버 시작 때 24시간 지나면 정리.
- **`summarizer.py`**: LMS 알림 요약(Phase 15a). `import_lms`가 메시지마다 `lms_notice`(종류·원문·URL·요약 상태)를
  쓰고 요약할 id를 돌려주면 `LmsSummaries` 워커가 분류기의 채팅 모델(`classifier_title_model` 우선)로 하나씩
  요약해 `services.save_summary`로 저장. 처리 중 원문이 바뀐 요약은 버리고, 실패는 다음 동기화에 재시도(최대 5회).
  피드는 `ref.lms_notice`를 `lmsNotice.tsx` 카드로 그린다.
- **`lms_canvas.py`**: LMS 기본 연결(Phase 17). Canvas 개인 액세스 토큰(키체인)으로 서버가 15분마다 확장의
  `collectLms`와 같은 API를 읽어(`collect`) `services.import_lms`에 넣고 자료 파일도 받음. 토큰은 설정한 주소의
  `/api/v1/`로만, GET만, 리다이렉트는 따르지 않음(저장소는 토큰 없이). `LmsSync`는 `lms.sync_lock`을 확장 수신
  API와 공유. 테스트는 httpx `MockTransport` 가짜 Canvas. 확장 프로그램(`lms_extension.py`, `integrations/
  learningx-extension`)은 설정에서 접힌 대안.
- **`caldav_sync.py`**: iCloud CalDAV 양방향(Phase 7b) + 칸반 마감 단방향(Phase 18). 쓰기는 Argos 캘린더(`Argos`,
  영역별 `Argos · 영역`, `Targets`)에만. 링크는 `source_link(source="icloud")`, 마감은 `object_type="task"`.
  테스트는 `tests/test_caldav.py`의 `FakeCalDAV`.
- **`notify.py` 아침 요약**(Phase 15b): `Notifier.run()`이 잠금 안에서 `MorningDigest.start_if_due`를 불러 설정
  시각~정오 사이 하루 한 번(알림 키 `digest:날짜`) #today에 루트 메시지를 만들고, 별도 태스크에서 고른 에이전트를
  `run_turn(no_tools=True)`로 실행해 그 스레드에 쓰게 한 뒤 같은 글을 `hermes send`로 보낸다. 에이전트가 없거나
  실패·빈 답이면 `digest_markdown` 목록. "지금 보내기"는 `POST /notify/digest`(별도 키라 그날 자동 요약을 막지 않음).
- **`agents.py`**: 어댑터 `stream(transcript, context, session)`가 `Token`/`Status`/`Failure`를 낸다.
  `AgentBackend`: `native`(Argos 하네스, 아래), Hermes(게이트웨이 Responses API + `conversation`), Claude(claude-agent-sdk, uuid5 세션 +
  `get_session_info`로 resume 판단), Codex(`codex app-server` JSON-RPC, 전용 `CODEX_HOME`에 auth.json·skills·AGENTS.md만
  링크, thread id는 `agent_session` 테이블), Ollama. 채팅은 Argos MCP 도구 + 사용자 스킬(Claude는 훅 끔),
  잡은 파일·셸 도구 + 샌드박스 + 작업 디렉터리 허용 목록, 코딩 모드 스레드(`message.coding`)는 채널
  작업 폴더(`channel.workspace_path`, 허용 목록 안)에서 사용자 설정·네트워크까지. `/이름`은 `skills.py`가
  에이전트 스킬로 풀어 준다. `build_adapter`가 에이전트 → 어댑터를 고른다.
- **`mcp_server.py`**: 같은 프로세스의 `/mcp`(Streamable HTTP). `?agent=` 또는 `X-Argos-Agent`로
  호출자 식별. 툴은 `argos_tools.py` 함수의 시그니처·docstring으로 동적 등록. 삭제 도구는 즉시 지우지 않고
  approval을 만든다(사용자 승인 후 실행).
- **`providers.py`**: 모델 연결(Phase 20, `model_provider` 행, 키는 키체인). 종류는 `ollama`·`openai`(OpenAI 호환)·
  `anthropic`. Anthropic `base_url`은 `/v1` 없는 루트(SDK가 붙임), OpenAI SDK를 쓰는 분류기·요약·비공개 대화는
  Anthropic의 OpenAI 호환 엔드포인트(`<base>/v1`)로. 키는 항상 명시(환경 변수 `ANTHROPIC_API_KEY` 안 읽음).
  위치(이 기기/내 네트워크/클라우드)는 주소로 계산, 비공개 대화(`private.py`, services를 안 타는 유일한 기능) 허용 판단에 씀.

#### v0.2 네이티브 에이전트 하네스

- **`harness.py`**: 프로바이더 모델의 스트리밍 다중 턴 툴 루프. `ToolDef`(이름·스키마·`kind` read|write|destructive·
  `source`/`plugin`)·`ToolRegistry`(`core_tools`), `decide()`가 권한 모드(`ARGOS_PERMISSION_MODE` 기본 `auto_edit`,
  채널별 `channel.permission_mode`, 실행 값은 `agent_run.permission_mode`)와 경로로 자동 실행/승인 요청을 일괄 판단.
  `HarnessLLM` 구현은 `OpenAIChatLLM`(chat.completions)·`AnthropicLLM`(Messages API), `repair_json`으로 깨진 툴 인자 복구.
  `NativeHarnessAdapter`가 `Token` + `ToolCall`/결과 단계를 내고 러너가 `agent_run_step`에 저장(화면은 `toolSteps.tsx`).
  `agents.build_adapter`가 `native`(또는 Anthropic 연결을 고른 로컬 모델 에이전트)일 때만 지연 import(순환 방지).
- **승인 대기**: 툴 승인은 기존 `approval` 행(`action="tool"`, `run_id`) 재사용. run은 `approval_waits.py`(프로세스 내
  future + 5초 DB 재확인)에서 멈춰 기다리고 승인되면 직접 실행. 대기 중 run 타임아웃 정지, 잡은 `job_concurrency` 슬롯 반납,
  `ARGOS_APPROVAL_TIMEOUT`(기본 24시간) 지나면 거절. run 종료·서버 재시작 시 남은 툴 승인은 `failed`.
- **`argos_tools.py`**: 도메인 툴(할 일·일정·채널·노트·인박스·LMS·`search_knowledge`)을 `(ToolContext, **args)` 함수로
  한 번만 작성. MCP와 네이티브 하네스가 공유(네이티브는 시그니처로 만든 pydantic 모델로 인자 검증, 삭제는 `destructive`
  인라인 승인 후 바로 삭제).
- **`file_tools.py`**: `read_file`/`list_dir`/`glob_files`/`grep_files`/`write_file`/`edit_file`/`delete_file`/`run_command`.
  채팅 run 작업 폴더는 `argos_workspace/channels/<채널 id>`(코딩·잡은 지정 폴더). `_resolve()`가 허용 루트(작업 폴더,
  Argos 워크스페이스, `job_roots`, 채널 `workspace_path`, `linked_folders`) 밖은 승인해도 거부(심링크·`..` 해석 후).
  덮어쓰는 파일은 `data_dir/file-backups/<run id>/`에 백업.
- **`extra_tools.py`**: `render_artifact`(HTML/SVG/Mermaid/TSX를 툴 호출 인자로 넘기면 화면이 sandbox iframe으로 그림),
  `web_fetch`(http(s)·공개 IP만, 리다이렉트마다 재검사, SSRF 방어), `ask_agent`(같은 스레드에 자식 run `kind="delegate"`,
  `parent_run_id`/`depth`, `ARGOS_MAX_AGENT_DEPTH` 기본 2, 조상 에이전트 재호출 금지).
- **`plugins.py`**: `data_dir/plugins/*.py`의 `register(registry)`를 시작 때 로드(`GET /plugins`). 코어 툴 덮어쓰기 금지,
  실패한 플러그인은 등록한 툴 전부 롤백. 플러그인 툴도 `kind`대로 권한 게이트를 받는다.
- **`knowledge.py`**: 채널 업로드(`argos_workspace/channels/<id>/uploads`)와 LMS 자료 텍스트를 `knowledge_doc` +
  `knowledge_fts`(trigram)에 색인, `search_knowledge`가 읽음. 경로는 `resolve_upload`로만.
- **`sft_export.py`**: 끝난 run(스레드 대화 + `agent_run_step` 툴 단계 + 최종 답)을 OpenAI `{"messages","tools"}` JSONL로
  내보냄(비밀값 `[REDACTED]`, `run_id` 해시로 train/eval 고정 분할, `tool_specs()`가 툴 스키마). CLI `python -m argos.sft_export`.
- **`remote.py`**: 폰에서 열기(Phase 14). Tailscale CLI 래퍼(`status`/`serve --set-path`, 테스트는 fake)와
  `RemoteGate` 미들웨어: serve로 온 요청은 `Tailscale-User-Login`이 `remote_allowed`에 있어야 하고, `/argos` 접두사를
  떼고 처리한다. 빌드된 화면은 그 경로로 열리면 `rewrite_index`가 자산 주소와 `<meta name="argos-base">`를 고친다.
- **데이터 경로**: `data_dir`(기본 `backend/data`, 앱은 Application Support) 기준 상대 경로. 새 경로 설정도
  `_inside_data_dir`에 넣는다.
- **설정**: `config.py`의 `Settings`(env prefix `ARGOS_`, `backend/.env`) 위에 앱에서 바꾼 값
  (`app_setting` 테이블: `default_agent`, `classifier_model`, `job_roots`)을
  `classifier.apply_overrides`로 덮어 `app.state.settings`에 둔다. 요청마다 `Config` 의존성으로 읽는다.
- 시간은 UTC 저장(`UTCDateTime`), 표시는 `Asia/Seoul`. 종일 일정은 날짜로만.
- 첫 실행(빈 DB)에만 `seed.example.toml`(또는 gitignore된 `seed.toml`)로 데모 영역·채널을 만든다.
- 마이그레이션은 Alembic `render_as_batch`(SQLite). `UTCDateTime`은 `sa.DateTime`으로 렌더링된다.

### 데스크톱 앱 (`desktop/`, Tauri v2)

`src-tauri/src/lib.rs`가 PyInstaller sidecar(`argos.desktop` 진입점)를 띄우고 8000이 답하면 창을 그 주소로
옮긴다. 창 닫기 = 숨기기(메뉴 막대 상주), `⌘⇧Space` 빠른 입력 창(`/quick`). 화면 → 앱 호출은
`frontend/src/desktop.ts`(`window.__TAURI__`)로만, 허용 명령은 `build.rs`(권한 생성) + `capabilities/default.json`. 자동 업데이트는 `src/update.rs`.
처음 설정(환영 화면)과 에이전트 MCP·스킬 설치/제거는 `onboarding.py` + `pages/Welcome.tsx`.

### 프런트엔드 (`frontend/src`, React 19 + Vite + Tailwind v4 + TanStack Query)

- `api.ts`: `openapi-fetch` 클라이언트(타입은 생성된 `api-types.ts`), 모든 쿼리·뮤테이션 훅.
  `invalidateFor(objectType)`가 객체 종류 → 무효화할 쿼리 키 매핑의 단일 지점. 새 객체 종류나 화면이
  생기면 여기에 추가.
- `realtime.ts`: `/ws` 하나로 `object.*` 이벤트 → `invalidateFor`, `agent.*` 이벤트 → `agentStream.ts`
  (스트리밍 중인 답 텍스트를 메시지별로 보관). 재연결 시 전체 refetch.
- 라우트(`main.tsx`): `/`는 마지막으로 연 채널(`lastChannel.ts`, 없으면 첫 과목·프로젝트, 그것도 없으면 Today)로
  이동(`HomeRedirect.tsx`), Today는 `/today`. `/c/:channelId`는 과목·프로젝트면 첫 탭이 채팅(`pages/ChatTab.tsx`,
  `?session=<루트 메시지 id>`), 피드는 `/c/:id/feed`, 그 밖에 `/kanban`, `/calendar`, `/notes`, `/materials`.
  시스템·내 공간 채널은 피드 그대로. `/settings`, `/approvals`, `/notifications`, `/review`, `/quick`, `/welcome`.
  우측 패널은 URL 쿼리(`?task=`, `?thread=`, `&wide=1`)로 열린다(`layout/Shell.tsx`). 사이드바 세션 목록은
  `GET /channels/{id}/threads`.
- **Chat-first(가안 A, v0.2)**: `ChatTab.tsx`의 새 대화 첫 메시지는 `agent_session: true`로 보내 인박스 대신 채널 기본
  에이전트와 스레드 시작. `composerBar.tsx`(입력창 하단 막대: 첨부·권한 모드 왼쪽, 에이전트 선택·컨텍스트 링·전송 오른쪽),
  `workspacePanel.tsx`(채널 파일 업로드 `/files`·LMS 자료 색인 상태·연결 폴더, 열림 상태는 기기별 localStorage),
  `toolSteps.tsx`(툴 호출 단계·인라인 승인), `artifact.tsx`(`render_artifact`를 `sandbox` iframe + 네트워크 없는 CSP로,
  tsx는 sucrase + Preact, mermaid는 `securityLevel: strict`, 둘 다 지연 로딩).
- `ui.tsx` 공용 컴포넌트: 네이티브 `<select>`·`window.confirm` 대신 테마 맞춘 `SelectMenu`·`ConfirmDialog`를 쓴다
  (테스트는 `test-select.ts` 헬퍼).
- `feed.tsx`(채팅 피드·입력창·에이전트 답 카드), `cards.tsx`(메시지에 붙는 할 일·일정·제안·승인 카드),
  `jobs.tsx`(잡 상태), `pages/KanbanTab.tsx`(dnd-kit, 낙관적 이동).

### 학습 파이프라인 (`training/`, 앱 코드와 분리)

네이티브 하네스용 툴콜 모델 SFT·평가(`docs/sft-training.md`). 데이터 형식은 모두 OpenAI `messages + tools` JSONL.

- `generate_seed_data.py`: `argos.sft_export.tool_specs()`의 실제 툴 스키마로 합성 다중 턴 궤적 → `training/data/{train,eval}.jsonl`.
- `train_sft.py`: Unsloth + TRL `SFTTrainer` LoRA(로컬 Linux + CUDA, `requirements.txt`), W&B 로깅, merged/GGUF 내보내기·HF Hub 푸시.
- `train_wandb_serverless.py`: OpenPipe ART `ServerlessBackend`로 W&B Training(GPU 없이 Mac 가능, `requirements-serverless.txt`),
  체크포인트가 OpenAI 호환 엔드포인트로 바로 서빙. `--dry-run`은 크레딧을 쓰지 않음.
- `eval_harness.py`: OpenAI 호환 URL(Ollama·vLLM·Forge 등)의 모델로 `eval.jsonl` 채점(JSON 유효율·툴 선택·스키마 준수·인자 일치), W&B 테이블.
- 결과 JSON은 `training/outputs/`. 학습 의존성은 백엔드 `pyproject`에 넣지 않고 `training/requirements*.txt` + 별도 `uv venv`.

## 테스트

- `tests/conftest.py`의 `settings`는 `Settings(_env_file=None, ...)`: 개발자 `.env`를 읽지 않는다.
  새 테스트도 이 픽스처를 쓴다.
- 실제 에이전트·Ollama·Hermes 없이 돌아간다. `tests/fakes.py`의 `FakeAgent`/`fake_agents(...)`로
  `app.state.runner.adapter_factory`를 바꾸고, 파서는 `tests/fixtures/agents/`의 실제 출력 샘플로 검증.
- E2E는 저장소에 없다. 필요하면 별도 포트 스택(백엔드 `ARGOS_PORT=8001 ARGOS_DB_PATH=<임시 db>`,
  프런트 `ARGOS_BACKEND=http://127.0.0.1:8001 bunx vite --port 5174`)을 띄워 사용자의 개발 DB를 건드리지 않는다.

## 화면 확인 (UI 피드백 루프)

UI를 바꾸면 테스트 통과로 끝내지 말고 실제 화면을 보고 확인한다(라이트/다크 둘 다, 디자인 캔버스와 비교).
아래 도구는 저장소에 없는 개인 환경용이다. Orca 터미널(`TERM_PROGRAM=Orca`)이고 해당 스킬이 있을 때만
쓰고, 없으면 건너뛰고 보고에 "화면 확인 못 함"이라고 적는다.

- **웹 화면**: `orca-cli` 스킬의 Orca 내장 브라우저. 개발 서버 `http://127.0.0.1:5273/argos/`(안 떠 있으면
  앱 `http://127.0.0.1:8000/`). 테마는 `orca set media --color-scheme light|dark`, 모바일은 `orca set device`.
  연 탭은 끝나면 `orca tab close --page <id>`로 닫는다.
- **일부분 자세히 보기**: `ui-crop` 스킬. `python3 .claude/skills/ui-crop/crop.py --selector '<CSS>'`가 잘린
  PNG 경로를 출력하면 그 파일을 Read한다. 데스크톱 창 스크린샷은 `crop.py <state.json> --rect x,y,w,h --scale 2`.
- **데스크톱 앱(Tauri 창·빠른 입력 창)**: `computer-use` 스킬(`orca computer ... --app app.argos.desktop`).
  Claude 내장 computer-use보다 이것을 쓴다(다른 앱을 숨기지 않고, 웹뷰 내용이 접근성 트리로 나와 요소 번호로
  조작). 창을 닫으면 숨겨지므로 `window_not_found`면 `open -b app.argos.desktop`. 앱은 사용자의 실제 데이터
  (`~/Library/Application Support/Argos`)를 쓰므로 메시지 전송·삭제·설정 변경 같은 조작은 먼저 묻는다.

## 관련 문서

- `docs/PLAN.md`: 전체 설계, Phase별 범위와 완료 기준, 보안·동기화·볼트 안전 규칙(§8)
- `docs/decisions.md`: PLAN과 달라진 결정 기록
- `docs/hermes-setup.md`, `docs/mcp-setup.md`: Hermes 게이트웨이·MCP 연결 절차
- `docs/sft-training.md`: SFT 데이터 추출·학습·평가 가이드
- `integrations/hermes/skills/argos/SKILL.md`: Hermes가 Argos 도구를 쓰는 규칙(스킬로 등록)
