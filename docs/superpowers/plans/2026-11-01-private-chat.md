# 비공개 대화 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 로컬 모델 에이전트 DM에서 유령 버튼으로 전환하는, Argos가 어디에도 기록하지 않는 비공개 대화.

**Architecture:** 서버는 기록 없는 두 엔드포인트를 둔다: 파일을 메모리에서 처리해 조각으로 돌려주는 `/private/files`와,
화면이 들고 있는 대화 전체를 받아 모델 답을 NDJSON으로 흘려 주는 `/private`. 화면은 모듈 수준 store(앱 수명)에 대화를
두고, DM 위에 `clip-path` 원으로 퍼지는 비공개 레이어를 그린다. 데스크톱 `open_main`은 페이지를 다시 불러오지 않게 바꾼다.

**Tech Stack:** FastAPI(StreamingResponse), openai SDK, Pillow/pillow_heif/pypdf(BytesIO); React 19, useSyncExternalStore,
fetch + ReadableStream, Tailwind v4 토큰; Tauri v2(Rust).

**Spec:** `docs/superpowers/specs/2026-11-01-private-chat-design.md`

## Global Constraints

- 비공개 경로는 `services` 쓰기 헬퍼·DB 세션·`hub`·`runner`·`attachments.save_upload`를 쓰지 않는다(decisions.md 2026-11-01).
- 디스크에 쓰지 않는다: `data_dir`, 시스템 임시 폴더(`tempfile.gettempdir()`) 모두. multipart 금지, 파일은 JSON base64.
- 로그: 비공개 코드는 `log.exception`·`str(exc)`·요청 본문·모델 출력·파일 이름을 로그나 오류 문구에 넣지 않는다. 예외 종류 이름만.
- 허용 조건(버튼·서버 공통): 백엔드 `ollama`, 연결 위치 `device`/`network`, 실제 모델 이름이 `-cloud`로 끝나거나 `:cloud` 태그가 아님.
  서버는 추가로 Ollama 연결이면 `/api/show`의 `remote_host`가 없을 때만 허용(확인 실패도 거부).
- 파일 하나 ≤ `attachment_max_mb`, 한 번에 ≤ 10개(`attachments.MAX_FILES`), 대화 전체(화면 계산) ≤ `attachment_max_mb`×4.
- 대화는 `localStorage`·`sessionStorage`·IndexedDB·React Query 캐시에 넣지 않는다.
- UI 문구 한국어 `tr()`/`tt` + `en.ts`; 색은 `index.css` 토큰만. 상단 띠 "비공개 대화 · 이 기기와 내 네트워크의 모델만 쓰고, 앱을 끄면 사라져요",
  빈 화면 "여기서 나눈 대화는 어디에도 저장되지 않아요". 한계(원격 메모리·스왑) 안내는 넣지 않음.
- 비공개 팔레트: 라이트 테마일 때 = 지금 다크 토큰 값, 다크 테마일 때 = 더 깊은 검정(page `#07070b`, card `#0e0e14`, inset `#0b0b10`,
  line `#26262f`, line-soft `#191920`, text `#e6e6ee`, text-2 `#b4b4c2`, meta `#85859a`).
- 전환: 누른 지점에서 `clip-path: circle()` 0 → 150%, 0.65s `cubic-bezier(.3,.7,.2,1)`, 같은 지점에서 링이 퍼짐; 돌아올 때 반대.
  `prefers-reduced-motion`이면 즉시.
- 기능 단위로 테스트 통과 시 커밋. API 모델 변경 태스크는 `make api-types` 결과도 커밋. 푸시·main 합치기는 하지 않는다.

## Review Focus

1. 비공개 대화를 보낸 뒤에도 DB의 모든 테이블 행 수, `data_dir`·임시 폴더 파일, 로그가 그대로여야 한다(오류가 난 요청 포함). (Task 3 `test_private_leaves_no_trace`, `test_private_error_leaves_no_trace`)
2. 이름이 로컬처럼 보이지만 실제로는 Ollama 클라우드 모델(`remote_host`)이면 서버가 거부. (Task 3 `test_private_refuses_remote_host_model`)
3. 일반 DM ↔ 비공개를 여러 번 오가거나 다른 채널에 갔다 와도 비공개 대화가 남는다. (Task 6 `keeps private turns across toggles and remounts`)
4. 답이 나오는 중에 중단·전환·지우기를 누르면 스트림이 끊기고 화면 상태가 꼬이지 않는다. (Task 5 `abort stops the stream and keeps partial text`)
5. HEIC 사진, 큰 이미지, 글 없는 PDF, 알 수 없는 파일이 와도 대화가 계속된다(읽을 수 없는 파일은 안내 조각). (Task 1 테스트들)

---

### Task 1: 메모리에서 첨부 처리 (`attachments.py`)

**Files:** Modify `backend/src/argos/attachments.py`; Test `backend/tests/test_private_attachments.py`

**Interfaces:**
- Produces:
  - `ImagePart.path: Path | None = None` (기존 생성자 호환)
  - `def part_from_bytes(name: str, data: bytes, *, pdf_native: bool = False) -> AgentPart` — `sniff(data[:_HEAD], name)`로 종류 판별 후:
    image → `ImagePart(name, mime, data', None)`(축소·회전 규칙은 `_image`와 같음), heic → JPEG로 변환 후 image 규칙,
    pdf → `PdfPart` (pdf_native) 또는 `_fenced(name, pdf 글)`, text → `_fenced`, 그 밖 → `TextPart("[첨부: {name}, {size} — 내용 읽기 불가]")`,
    처리 중 예외 → `TextPart("[첨부: {name} — 읽지 못함]")`
  - 기존 `_image(path, mime)`, `_pdf_text(path)`는 bytes 버전(`_image_bytes(data, mime)`, `_pdf_text_bytes(data)`)을 부르도록 바꿔 중복 없앰.

- [ ] **Step 1: tests** (`tests/files.py`의 `png`, `text_pdf` 사용)
  - `test_png_stays_png_when_small`: `part_from_bytes("a.png", png(10,10))` → ImagePart, mime `image/png`, data 동일.
  - `test_big_image_is_shrunk_to_jpeg`: 3000×100 png → mime `image/jpeg`, `max(Image.open(BytesIO(data)).size) == 2000`.
  - `test_heic_becomes_jpeg`: pillow_heif로 만든 HEIC bytes → ImagePart mime `image/jpeg`.
  - `test_pdf_text_extracted`: `text_pdf("비밀 계약")` → TextPart, 글에 "비밀 계약"과 "[첨부: a.pdf]".
  - `test_pdf_native`: `pdf_native=True` → PdfPart.
  - `test_textless_pdf_message`, `test_text_file`, `test_unknown_file_unreadable_note`, `test_broken_image_reads_as_failed`(png 헤더+쓰레기).
  - `test_no_disk_writes`: 위 처리 전후로 `tempfile.gettempdir()`와 tmp_path 파일 목록 동일.
- [ ] **Step 2:** `cd backend && uv run pytest tests/test_private_attachments.py -q` → FAIL
- [ ] **Step 3:** 구현(Pillow·pypdf는 `io.BytesIO`; HEIC는 `Image.open(BytesIO)` → `convert("RGB")` → JPEG q90 bytes)
- [ ] **Step 4:** `uv run pytest tests/test_private_attachments.py tests/test_attachments.py -q && uv run pyright` → PASS, 0 errors
- [ ] **Step 5:** commit `feat: process attachments from memory`

---

### Task 2: 허용 판정 (`agents.local_target`, `providers.remote_host`, `AgentOut.private_ok`)

**Files:** Modify `backend/src/argos/agents.py:1104-1146`, `backend/src/argos/providers.py`, `backend/src/argos/api.py` (AgentOut `:482`, `_agent_status` `:1113`); Test `backend/tests/test_private_ok.py`

**Interfaces:**
- Produces:
  - `def local_target(agent: Agent, settings: Settings) -> tuple[ProviderConfig, str]` — build_adapter OLLAMA 분기의 연결·모델 선택을 그대로 옮긴 것(모델 없으면 `AgentUnavailable`). build_adapter도 이걸 쓴다.
  - `def cloud_model_name(model: str) -> bool` — `-cloud`로 끝나거나 `:cloud` 태그(`"x:cloud"`, `"x:120b-cloud"`) 여부.
  - `def private_allowed(agent: Agent, settings: Settings) -> bool` — 백엔드 OLLAMA이고 `local_target` 성공, 위치 ≠ cloud, `not cloud_model_name(model)`.
  - `async def remote_host(p: ProviderConfig, model: str) -> str | None` (providers) — `POST {root}/api/show`, `remote_host` 값 또는 None; 실패는 `ProviderError`; 캐시 없음.
  - `AgentOut.private_ok: bool = False` — `_agent_status`에서 `private_allowed`.
- [ ] **Step 1: tests**
  - `private_allowed`: 로컬 연결 커스텀 에이전트 True; network 연결 True; cloud 위치 연결 False; 모델 `gpt-oss:120b-cloud`·`glm-4.6:cloud` False;
    Claude 에이전트 False; 내장 `local`이 분류기 연결(network) 따라 True; 모델 없음 False.
  - `cloud_model_name` 표.
  - `remote_host`: mock_http로 `{"remote_host":"https://ollama.com:443"}` → 그 값, `{}` → None, 500 → ProviderError.
  - API: `GET /api/v1/agents` 응답의 `private_ok`가 위 조건대로.
  - `build_adapter` 기존 테스트(`tests/test_agent_providers.py`) 그대로 통과.
- [ ] **Step 2:** FAIL 확인 → **Step 3:** 구현 → **Step 4:** `uv run pytest -q && uv run pyright` PASS
- [ ] **Step 5:** `make api-types`; commit `feat: decide which agents allow private chat`

---

### Task 3: 비공개 엔드포인트 (`private.py`)

**Files:** Create `backend/src/argos/private.py` (라우터 `private_router`, prefix `/api/v1`), Modify `backend/src/argos/main.py`(라우터 등록), `backend/src/argos/runner.py`(시각·언어 줄 헬퍼 추출); Test `backend/tests/test_private_chat.py`

**Interfaces:**
- Consumes: Task 1 `part_from_bytes`, `data_url`; Task 2 `local_target`, `private_allowed`, `remote_host`; `providers.chat_client`, `unreachable_text`.
- Produces:
  - `runner.time_line(settings, now) -> str`, `runner.language_line(settings) -> str` (build_context가 이 둘을 쓰도록 바꿈; 문구 동일).
  - `POST /api/v1/agents/{name}/private/files`: body `PrivateFilesIn{files: list[{name: str ≤255, data: str(base64)}] ≤10}` → `list[PrivatePart]`,
    `PrivatePart{kind: "text"|"image"|"pdf", name: str, text: str | None, data: str | None (base64), mime: str | None}`.
    파일 > `attachment_max_mb` → 413 `"{N}MB까지 올릴 수 있어요"`.
  - `POST /api/v1/agents/{name}/private`: body `PrivateChatIn{turns: list[{role: "user"|"assistant", text: str, parts: list[PrivatePart]}] (1..200)}`
    → `StreamingResponse(media_type="application/x-ndjson")`, 줄마다 `{"token": "..."}`, 끝 `{"done": true}`, 오류 `{"error": "<한국어>"}`.
  - 허용 검사(두 엔드포인트 공통): `find_agent` 없으면 404, `private_allowed` False면 422 `"비공개 대화는 로컬 모델에서만 쓸 수 있어요"`,
    Ollama 연결이면 `remote_host`가 값이 있거나 `ProviderError`면 422 같은 문구(스트림 시작 전).
  - 메시지: system = `time_line` + `language_line` + 에이전트 `system_prompt`(있으면); assistant 턴은 assistant, user 턴은 글 + text 조각을 이어 붙이고,
    image 조각은 `image_url`(vision: Ollama면 `providers.vision`, openai면 `agent.send_images`), 못 보내면 `NO_VISION` 안내. pdf 조각은 글로만(pdf_native 쓰지 않음).
  - 모델 호출: `providers.chat_client(p, timeout=settings.agent_timeout, max_retries=0).chat.completions.create(..., stream=True, extra_body={"reasoning_effort":"none"} if ollama else None)`.
    연결 실패 → `unreachable_text(p)`, 그 밖 → `"답을 받지 못했어요"`. 클라이언트가 끊으면 응답 스트림 `close()`.
  - 검증 오류: 이 라우터의 본문은 `Request`로 직접 읽어 pydantic `model_validate_json`; 실패 시 422 `"요청 형식이 맞지 않아요"`(입력값 미포함).
- [ ] **Step 1: tests** (가짜 모델: `monkeypatch.setattr(providers, "chat_client", ...)`로 `tests/test_agents.py:694-720`의 MockTransport AsyncOpenAI, `/api/show`는 `mock_http`)
  - `test_private_streams_tokens`: 로컬 에이전트에 "안녕" → NDJSON 줄 `{"token": ...}`… `{"done": true}`; 모델이 받은 messages[0]은 system(시각·언어 줄), 마지막은 user "안녕".
  - `test_private_sends_history_and_image`: 이전 assistant 턴과 image 조각 → 모델 요청에 assistant 메시지와 `image_url` data URL.
  - `test_private_files_roundtrip`: png·pdf·txt base64 → 조각 3개(kind image/text/text).
  - `test_private_leaves_no_trace`: 대화 + 파일 처리 전후로 `Base.metadata.sorted_tables` 모든 행 수 동일, `settings.data_dir`·`tempfile.gettempdir()` 파일 목록 동일,
    `caplog`(DEBUG, 루트)에 대화 글 "비밀 계약 123"·파일 이름 "contract.pdf"·모델 답 없음.
  - `test_private_error_leaves_no_trace`: 모델이 `APIConnectionError` → `{"error": "GPU에 연결할 수 없어요 (...)"}`, 위와 같은 무흔적 확인.
  - `test_private_refuses_cloud_agent` (Claude 에이전트 422), `test_private_refuses_cloud_provider`, `test_private_refuses_remote_host_model`(이름은 로컬, `/api/show`에 remote_host),
    `test_private_refuses_when_show_fails`, `test_private_bad_body_hides_input`(잘못된 JSON에 "비밀" 포함 → 422 메시지에 "비밀" 없음), `test_private_file_too_large`.
  - `test_build_context_unchanged`: 기존 build_context 테스트들 통과(헬퍼 추출 회귀).
- [ ] **Step 2:** FAIL → **Step 3:** 구현 → **Step 4:** `uv run pytest -q && uv run pyright && uv run ruff check src tests` PASS
- [ ] **Step 5:** `make api-types`; commit `feat: add private chat endpoints that keep no record`

---

### Task 4: 데스크톱 `open_main`이 페이지를 다시 불러오지 않게

**Files:** Modify `desktop/src-tauri/src/lib.rs:244-257,331-342`, `frontend/src/desktop.ts`, `frontend/src/main.tsx:29-60`; Test `frontend/src/desktop.test.ts`, Rust 단위 테스트(`lib.rs` tests 모듈)

**Interfaces:**
- Produces:
  - Rust `fn on_app(url: &str, base: &str) -> bool` (url이 `base` + `/`로 시작) — 테스트 가능 순수 함수.
  - `show_main(app, path)`: `Some(path)`이고 메인 창 URL이 `on_app`이면 `window.emit("argos:navigate", path)`(`use tauri::Emitter`), 아니면 지금처럼 `navigate`.
  - `desktop.ts`: `Tauri` 타입에 `event?.listen`, `export function onMainNavigate(cb: (path: string) => void): void` (앱 밖이면 아무것도 안 함).
  - `main.tsx`: 모듈 수준에서 `onMainNavigate((path) => void router.navigate(path))`.
- [ ] **Step 1: tests** — Rust: `on_app("http://127.0.0.1:8000/c/x", "http://127.0.0.1:8000") == true`, `"tauri://localhost/index.html"` false.
  vitest: `window.__TAURI__.event.listen` 스텁이 `"argos:navigate"`로 불리고, 콜백에 payload 경로가 전달됨; 앱 밖이면 listen 안 부름.
- [ ] **Step 2:** FAIL → **Step 3:** 구현
- [ ] **Step 4:** `cd desktop/src-tauri && cargo fmt --check && cargo clippy --locked -- -D warnings && cargo test --locked`; `cd frontend && bunx vitest run src/desktop.test.ts && bunx tsc --noEmit` PASS
- [ ] **Step 5:** commit `fix: open main window paths without reloading the page`

---

### Task 5: 비공개 대화 store와 스트림 호출 (프런트)

**Files:** Create `frontend/src/privateChat.ts`; Modify `frontend/src/api.ts`(raw fetch 헬퍼); Test `frontend/src/privateChat.test.ts`

**Interfaces:**
- Produces:
  - 타입 `PrivatePart` (Task 3과 같은 모양), `PrivateTurn = {role: "user"|"assistant", text: string, parts: PrivatePart[], error?: string}`,
    `PrivateDraft = {key: string, file: File, status: "uploading"|"done"|"error", parts?: PrivatePart[], error?: string, preview?: string}`.
  - `api.ts`: `privateFiles(agent: string, files: File[]): Promise<PrivatePart[]>` (base64 JSON POST, 오류는 `ApiError`), 
    `streamPrivate(agent: string, turns: PrivateTurn[], onToken: (t: string) => void, signal: AbortSignal): Promise<void>` (NDJSON 읽기; `{"error"}` 줄이면 `ApiError(422,…)` throw).
  - `privateChat.ts` store(모듈 수준 Map + listeners + `useSyncExternalStore`, `agentStream.ts` 패턴):
    `usePrivateChat(agentName: string): {turns, drafts, running, error, send(text), addFiles(files), removeDraft(key), stop(), clear(), size}`.
    `send`: 진행 중이거나 업로드 중 draft 있으면 무시; user 턴(글 + done draft들의 parts) 추가 → 빈 assistant 턴 추가 → 토큰마다 이어 붙임 → 끝나면 running false.
    `stop`: AbortController.abort, 지금까지 받은 글 유지. `clear`: 진행 중이면 stop 후 전부 삭제(미리보기 URL revoke).
    크기(글 길이 + base64 길이 합) > `max_mb*4` MB면 `send`가 오류 "대화가 너무 길어요 · 지우고 새로 시작해 주세요"를 세움(전송 안 함). `max_mb`는 인자(기본 25).
- [ ] **Step 1: tests** (`vi.stubGlobal("fetch", …)`로 `new Response(new ReadableStream(...))`)
  - `streams tokens into an assistant turn`, `sends prior turns and parts`, `abort stops the stream and keeps partial text`,
    `error line becomes the turn error`, `clear empties the store`, `state survives remount (same module)`, `files become drafts then parts`,
    `too long conversation refuses to send`, `never writes storage` (`localStorage.setItem`/`sessionStorage.setItem` 스파이 0회).
- [ ] **Step 2:** FAIL → **Step 3:** 구현 → **Step 4:** `cd frontend && bunx vitest run src/privateChat.test.ts && bunx tsc --noEmit && bunx biome check src` PASS
- [ ] **Step 5:** commit `feat: keep private chats in app memory and stream answers`

---

### Task 6: 유령 버튼·비공개 레이어·파동 (프런트)

**Files:** Modify `frontend/src/index.css`(토큰·애니메이션), `frontend/src/icons.tsx`(`GhostIcon`), `frontend/src/pages/ChannelPage.tsx:258-326`(DMPage), `frontend/src/en.ts`;
Create `frontend/src/pages/PrivateLayer.tsx`; Test `frontend/src/pages/PrivateLayer.test.tsx`

**Interfaces:**
- Consumes: Task 5 `usePrivateChat`, `Agent.private_ok` (Task 2 api-types).
- Produces:
  - CSS: `:root`와 `:root[data-theme="dark"]`에 `--private-page/card/inset/line/line-soft/text/text-2/meta`; `.private-scope`가 `--page`… 를 `--private-*`로 바꿔
    기존 유틸리티(`bg-card`, `text-text`)가 그대로 비공개 색을 쓰게. `.private-layer`(`clip-path: circle(var(--r) at var(--x) var(--y))` 전환), `.private-ring` 키프레임, reduced-motion 블록에 추가.
  - `GhostIcon({size})`.
  - DMPage: 헤더 오른쪽 "새 대화" 앞에 유령 버튼(`agent?.private_ok`일 때만, `aria-label` "비공개 대화로 전환"/"일반 대화로 돌아가기", `aria-pressed`).
    클릭 좌표를 `<main>` 기준으로 계산해 레이어에 넘김. 열림 상태는 에이전트별로 store에 둠(다른 채널 갔다 와도 열린 채).
  - `PrivateLayer({agent, origin, open, onClose})`: `absolute inset-0 z-…` `.private-scope .private-layer`; 헤더(아바타, 이름, "비공개 · 저장 안 됨", 유령 버튼, "대화 지우기"),
    상단 띠, 빈 화면 문구, 대화(사용자 글 / `Markdown` 답 + 진행 캐럿 + `PawTrail`), 입력창(피드 Composer 모양, `useFileDrop`·`pastedFiles`·`DraftChips`, Enter 전송·IME 무시, 중단 버튼).
- [ ] **Step 1: tests** (`vi.mock("../api")`, `vi.mock("../privateChat")` 없이 실제 store + fetch 스텁)
  - `ghost button only when private_ok`, `toggling opens and closes the layer with origin`, `keeps private turns across toggles and remounts`,
    `sends with Enter but not while composing`, `clear button empties`, `reduced motion skips animation class`, 문구(상단 띠·빈 화면) 존재.
- [ ] **Step 2:** FAIL → **Step 3:** 구현 → **Step 4:** `cd frontend && bunx vitest run && bunx tsc --noEmit && bunx biome check src` PASS
- [ ] **Step 5:** commit `feat: switch a DM to private chat with a ghost button`

---

### Task 7: 문서·전체 검사·화면 확인

- [ ] `README.md`/`README.ko.md` 기능 목록에 "비공개 대화" 한 줄. `docs/desktop.md`에 `open_main` 동작 변화가 언급돼 있으면 고침.
- [ ] `make lint && make test`, `cd backend && uv run alembic check`, `cd desktop/src-tauri && cargo clippy --locked -- -D warnings && cargo test --locked`, `cd frontend && bun run build` 모두 통과.
- [ ] 화면 확인(Orca, 라이트·다크): `@gemma` DM에서 유령 버튼 → 파동 전환 → 글 + 이미지·PDF 첨부 질문 → 답 스트리밍 → 일반 DM 왕복 후 유지 → 지우기.
  Claude DM에 버튼 없음. 확인 후 개발 DB `agent_run`/`message` 행 수가 늘지 않았는지 sqlite로 비교.
- [ ] commit `docs: describe private chat`
