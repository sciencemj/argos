# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Argos: 개인용 학업·프로젝트 대시보드. 채널(과목·프로젝트)별 채팅 피드·칸반·캘린더, 인박스 AI 분류,
에이전트(Hermes·Claude·Codex·로컬 모델) 대화와 코딩 잡, 에이전트용 MCP 서버. 이 Mac에서만 돌고
외부 접근은 Tailscale serve(tailnet 전용)로만.

## 작업 규칙 (docs/PLAN.md §0 요약)

- `docs/PLAN.md`의 Phase를 순서대로 하나씩. Phase가 끝나면 멈추고 보고한 뒤 승인받고 커밋.
  Phase 범위 밖 기능은 미리 만들지 않는다. 미룬 개선은 `docs/backlog.md`.
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
```

포트 8000은 데스크톱 앱(`docs/desktop.md`, 데이터는 `~/Library/Application Support/Argos`) 몫이다. 에이전트
MCP 등록이 8000을 가리키므로 개발 서버는 8100/5273을 쓴다. Tailscale serve는 개발용으로 5273을 가리킨다
(앱 기능 아님). 릴리스: `make release VERSION=x.y.z` → 태그 → `.github/workflows/release.yml`.

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
- **`agents.py`**: 어댑터 `stream(transcript, context, session)`가 `Token`/`Status`/`Failure`를 낸다.
  Hermes(게이트웨이 Responses API + `conversation`), Claude(claude-agent-sdk, uuid5 세션 +
  `get_session_info`로 resume 판단), Codex(`codex app-server` JSON-RPC, 전용 `CODEX_HOME`에 auth.json·skills·AGENTS.md만
  링크, thread id는 `agent_session` 테이블), Ollama. 채팅은 Argos MCP 도구 + 사용자 스킬(Claude는 훅 끔),
  잡은 파일·셸 도구 + 샌드박스 + 작업 디렉터리 허용 목록, 코딩 모드 스레드(`message.coding`)는 채널
  작업 폴더(`channel.workspace_path`, 허용 목록 안)에서 사용자 설정·네트워크까지. `/이름`은 `skills.py`가
  에이전트 스킬로 풀어 준다. `build_adapter`가 에이전트 → 어댑터를 고른다.
- **`mcp_server.py`**: 같은 프로세스의 `/mcp`(Streamable HTTP). `?agent=` 또는 `X-Argos-Agent`로
  호출자 식별. 삭제 도구는 즉시 지우지 않고 approval을 만든다(사용자 승인 후 실행).
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
- 라우트: `/`(Today), `/c/:channelId`(피드) + `/kanban`, `/calendar`, `/settings`, `/approvals`.
  우측 패널은 URL 쿼리(`?task=`, `?thread=`)로 열린다(`layout/Shell.tsx`).
- `feed.tsx`(채팅 피드·입력창·에이전트 답 카드), `cards.tsx`(메시지에 붙는 할 일·일정·제안·승인 카드),
  `jobs.tsx`(잡 상태), `pages/KanbanTab.tsx`(dnd-kit, 낙관적 이동).

## 테스트

- `tests/conftest.py`의 `settings`는 `Settings(_env_file=None, ...)`: 개발자 `.env`를 읽지 않는다.
  새 테스트도 이 픽스처를 쓴다.
- 실제 에이전트·Ollama·Hermes 없이 돌아간다. `tests/fakes.py`의 `FakeAgent`/`fake_agents(...)`로
  `app.state.runner.adapter_factory`를 바꾸고, 파서는 `tests/fixtures/agents/`의 실제 출력 샘플로 검증.
- E2E는 저장소에 없다. 필요하면 별도 포트 스택(백엔드 `ARGOS_PORT=8001 ARGOS_DB_PATH=<임시 db>`,
  프런트 `ARGOS_BACKEND=http://127.0.0.1:8001 bunx vite --port 5174`)을 띄워 사용자의 개발 DB를 건드리지 않는다.

## 관련 문서

- `docs/PLAN.md`: 전체 설계, Phase별 범위와 완료 기준, 보안·동기화·볼트 안전 규칙(§8)
- `docs/decisions.md`: PLAN과 달라진 결정 기록
- `docs/hermes-setup.md`, `docs/mcp-setup.md`: Hermes 게이트웨이·MCP 연결 절차
- `integrations/hermes/skills/argos/SKILL.md`: Hermes가 Argos 도구를 쓰는 규칙(스킬로 등록)
