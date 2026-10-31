# 모델 연결(프로바이더) 여러 개 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 이름 붙은 모델 연결(Ollama / OpenAI 호환)을 여러 개 등록하고, 커스텀 에이전트·분류기가 연결을 골라 쓰게 한다.

**Architecture:** 새 테이블 `model_provider`와 `agent.provider_id`/`agent.send_images`. 서버는 시작할 때와 연결이 바뀔 때
연결 목록(키체인의 API 키 포함)을 `Settings.model_providers` 스냅샷으로 넣는다. 그래서 `build_adapter`·`build_classifier`처럼
`Settings`만 받는 동기 코드가 시그니처 변경 없이 연결을 찾는다. 새 모듈 `providers.py`가 위치 계산, 주소 정규화,
종류별 모델 목록·이미지 지원, 키체인, DB ↔ 스냅샷 변환을 맡는다.

**Tech Stack:** FastAPI, async SQLAlchemy + Alembic(SQLite, `render_as_batch`), pydantic-settings, openai SDK, httpx2, keyring;
React 19 + TanStack Query + openapi-fetch, vitest.

**Spec:** `docs/superpowers/specs/2026-10-31-model-providers-design.md`

## Global Constraints

- 모든 쓰기는 `services.py`의 `_create`/`_update`/`_delete`로(activity_log + WS `object.*`, object_type = `model_provider`).
- 마이그레이션은 스키마만. 앱 코드·`.env`·키체인을 읽지 않는다(`alembic/env.py` 규칙). down_revision `4b2f70c92b79`.
- API 키는 키체인에만(`keyring`, 서비스 `"Argos model provider API key"`, 계정 = 연결 id). DB엔 `has_api_key`만. 응답·로그에 키 금지.
- 키체인 호출은 `asyncio.to_thread`로. 키체인 객체는 `app.state.provider_keychain`(테스트에서 `FakeKeychain`으로 교체).
- `classifier_provider = "hermes"`면 분류기·요약·회고 임베딩은 지금처럼 `classifier_base_url`·`classifier_api_key`를 매번 읽는다.
- 외부 접속: 사용자가 넣은 주소만, `follow_redirects=False`, 목록·시험 타임아웃 5초, 채팅은 `agent_timeout`.
- UI 문구는 한국어로 `tr()`/`tt`, 영어는 `frontend/src/en.ts`/`en-templates.ts`. 코드·식별자·커밋은 영어.
- 색은 `index.css` 토큰만: 클라우드 경고 `bg-danger-bg text-danger`, 위치 배지 `bg-inset text-text-2`.
- 개인 환경 값(mlbox 주소 등)을 코드·문구·테스트 이름에 박지 않는다. 테스트 주소는 `*.test` 도메인과 문서용 IP.
- 기능 단위로 테스트가 통과하면 바로 커밋. 푸시·main 합치기는 하지 않는다.
- API 모델을 바꾼 태스크는 끝에 `make api-types` 후 `frontend/src/api-types.ts`도 커밋.
- 모델 변경을 개발 서버에 반영하기 전에 `cd backend && uv run alembic upgrade head`.

## Review Focus

1. 연결 주소를 `/v1` 없이(`http://host:11434`) 또는 끝에 `/`를 붙여 넣는다 → Ollama는 `/v1`을 붙여 저장, OpenAI 호환은 끝 `/`만 떼고 그대로. (Task 1 `test_normalize_base_url`)
2. 연결 주소를 바꾼 직후 에이전트 모델 목록을 연다 → 예전 주소의 캐시가 아니라 새 주소 목록. (Task 6 `test_models_cache_follows_provider_edit`)
3. 연결 이름을 바꾸거나 기본 연결 주소를 고친다 → 그 연결을 쓰던 에이전트는 id로 이어져 그대로 동작. (Task 4 `test_agent_follows_provider_after_rename`)
4. 키체인 저장이 실패한다(잠김·거부) → 422와 한국어 오류, 연결 행은 남지 않음(새로 만들 때) / 키 상태 안 바뀜(수정 때). (Task 6 `test_keychain_failure_leaves_no_provider`)
5. 서버 시작 때 분류기 연결 서버가 꺼져 있다 → 시작은 네트워크 호출 없이 성공, 분류만 실패. (Task 3 `test_startup_does_not_touch_network`)

---

### Task 1: `providers.py` 핵심(위치·주소·모델 목록·이미지·키체인)

**Files:**
- Create: `backend/src/argos/providers.py`
- Test: `backend/tests/test_providers.py`

**Interfaces:**
- Produces:
  - `ProviderKind = Literal["ollama", "openai"]`, `Locality = Literal["device", "network", "cloud"]`
  - `@dataclass(frozen=True) class ProviderConfig: id: str; name: str; kind: ProviderKind; base_url: str; api_key: str | None; locality: Locality; is_default: bool`
    (`__repr__`이 api_key를 드러내지 않게 `field(repr=False)`)
  - `@dataclass(frozen=True) class ProviderModel: name: str; remote: bool; parameter_size: str | None; decision: bool = False`
  - `class ProviderError(Exception)` — 접속 실패·응답 형식 오류
  - `def normalize_base_url(kind: ProviderKind, url: str) -> str` — ValueError(한국어 메시지)
  - `def locality_of(base_url: str) -> Locality`
  - `def effective_locality(computed: Locality, strict: Locality | None) -> Locality` — 더 엄격한 쪽(device < network < cloud)
  - `async def list_models(p: ProviderConfig, *, decision: bool = False) -> list[ProviderModel]`
  - `async def vision(p: ProviderConfig, model: str) -> bool`
  - `def chat_client(p: ProviderConfig, *, timeout: float, max_retries: int) -> AsyncOpenAI`
  - `def unreachable_text(p: ProviderConfig) -> str` → `f"{p.name}에 연결할 수 없어요 ({p.base_url})"`
  - `KEYCHAIN_SERVICE = "Argos model provider API key"`, `class Keychain` with `get(provider_id) -> str | None`, `set(provider_id, key)`, `delete(provider_id)` (caldav_sync.Keychain 패턴 그대로)

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize(("url", "expected"), [
    ("http://127.0.0.1:11434/v1", "device"), ("http://localhost:1234/v1", "device"),
    ("http://[::1]:11434/v1", "device"), ("http://192.168.0.5:11434/v1", "network"),
    ("http://10.1.2.3/v1", "network"), ("http://172.20.0.1/v1", "network"),
    ("http://100.100.1.1:11434/v1", "network"), ("https://box.tail1234.ts.net/v1", "network"),
    ("http://gpu.local:11434/v1", "network"), ("https://openrouter.ai/api/v1", "cloud"),
    ("http://203.0.113.9/v1", "cloud"), ("http://172.32.0.1/v1", "cloud"),
])
def test_locality_of(url, expected): assert providers.locality_of(url) == expected

def test_effective_locality_only_gets_stricter():
    assert providers.effective_locality("network", "cloud") == "cloud"
    assert providers.effective_locality("cloud", "device") == "cloud"
    assert providers.effective_locality("device", None) == "device"

@pytest.mark.parametrize(("kind", "url", "expected"), [
    ("ollama", "http://gpu.test:11434", "http://gpu.test:11434/v1"),
    ("ollama", "http://gpu.test:11434/v1/", "http://gpu.test:11434/v1"),
    ("openai", "https://api.example.test/api/v1/", "https://api.example.test/api/v1"),
    ("openai", " http://lm.test:1234/v1 ", "http://lm.test:1234/v1"),
])
def test_normalize_base_url(kind, url, expected): assert providers.normalize_base_url(kind, url) == expected

@pytest.mark.parametrize("url", ["ftp://x.test/v1", "gpu.test:11434", "", "http://"])
def test_normalize_base_url_rejects(url):
    with pytest.raises(ValueError): providers.normalize_base_url("ollama", url)
```

Plus MockTransport tests (monkeypatch `providers.httpx2.AsyncClient` like `tests/test_settings.py:108-146`):
- `test_list_models_ollama`: `GET http://gpu.test:11434/api/tags` → completion 모델 1개 + embedding 전용 1개 + decision 1개 + `remote_host` 1개. `decision=False`면 embedding·decision 빠짐, remote는 `remote=True`, 정렬 `(remote, name)`.
- `test_list_models_openai`: `GET https://api.example.test/v1/models`에 `Authorization: Bearer sk-test` 헤더, `{"data":[{"id":"b"},{"id":"a"}]}` → 이름 `["a","b"]`, `remote=False`.
- `test_list_models_unreachable_raises_provider_error` (ConnectError) 와 `test_list_models_does_not_follow_redirects` (302 → ProviderError).
- `test_vision_ollama_reads_capabilities`: `/api/show` capabilities에 `"vision"` → True, 없으면 False, 실패하면 True(지금 `ollama_vision`과 같음).
- `test_vision_openai_is_false`.
- `test_provider_config_repr_hides_key`: `"sk-test" not in repr(cfg)`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_providers.py -q`
Expected: FAIL (ModuleNotFoundError: argos.providers)

- [ ] **Step 3: Implement `providers.py`**

`locality_of`: `urllib.parse.urlsplit(...).hostname`, `ipaddress.ip_address`로 루프백(`is_loopback`) → device, `is_private` 또는
`100.64.0.0/10` → network, IP가 아니면 `localhost` → device, `.ts.net`/`.local`로 끝나면 network, 나머지 cloud.
(`is_private`는 문서용 대역 `203.0.113.0/24`도 True라서 사설 3대역과 CGNAT를 `ip_network`로 명시해 판정.)
`vision`의 캐시는 `agents._VISION`을 이 모듈로 옮겨 `(base_url, model)` 키 유지. `list_models`의 Ollama 분기는
`classifier.list_ollama_models` 본문을 옮겨 온 것(필터 규칙 동일). 모든 httpx 호출 `follow_redirects=False`, timeout 5.
`chat_client`는 `api_key=p.api_key or "none"`(SDK가 빈 키를 거부).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_providers.py -q && uv run pyright src/argos/providers.py`
Expected: all PASS, 0 errors

- [ ] **Step 5: Commit**

```bash
git add backend/src/argos/providers.py backend/tests/test_providers.py
git commit -m "feat: add model provider core (locality, url, models, keychain)"
```

---

### Task 2: 데이터 모델·마이그레이션·services CRUD

**Files:**
- Modify: `backend/src/argos/models.py` (Agent `:381-391` 근처에 새 클래스, Agent에 열 2개)
- Create: `backend/alembic/versions/<rev>_model_provider.py`
- Modify: `backend/src/argos/services.py` (provider CRUD, `_check_agent_fields :2317`)
- Test: `backend/tests/test_providers_services.py`

**Interfaces:**
- Consumes: `providers.normalize_base_url`, `ProviderKind`, `Locality` (Task 1)
- Produces:
  - `class ModelProvider(Record)`: `__tablename__ = "model_provider"`; `name: String(60) unique`; `kind: String(20)`; `base_url: String(500)`;
    `has_api_key: Boolean default False server_default "0"`; `strict_locality: String(20) | None`; `is_default: Boolean default False server_default "0"`
  - `Agent.provider_id: Mapped[str | None] = mapped_column(ForeignKey("model_provider.id", ondelete="SET NULL"))`; `Agent.send_images: Boolean default False server_default "0"`
  - `async def create_provider(session, fields: dict[str, Any], actor: str) -> ModelProvider`
  - `async def update_provider(session, provider_id: str, changes: dict[str, Any], actor: str) -> ModelProvider`
  - `async def delete_provider(session, provider_id: str, actor: str) -> None`
  - `async def provider_usage(session, provider_id: str) -> list[str]` — 쓰는 곳 표시 문자열(`"@tutor"`, `"인박스 분류"`)
  - fields 키: `name, kind, base_url, has_api_key, strict_locality` (`is_default`는 services 밖에서 못 바꿈)

- [ ] **Step 1: Write the failing tests** (`session` 픽스처)

- `test_create_provider_normalizes_and_logs`: `create_provider(session, {"name":" GPU ","kind":"ollama","base_url":"http://gpu.test:11434"}, "user")` → name `"GPU"`, base_url `"http://gpu.test:11434/v1"`, activity_log 1행.
- `test_provider_name_unique` → 두 번째 같은 이름 `ConflictError`.
- `test_provider_rejects_bad_kind_url_locality` → `kind="anthropic"`, `base_url="gpu"`, `strict_locality="device"`(엄격화만 허용: `network`/`cloud`만) 각각 `InvalidError`.
- `test_delete_default_provider_conflict` → `is_default=True` 행 삭제 `ConflictError`.
- `test_delete_provider_in_use_lists_users`: 에이전트 `tutor`(provider_id=p.id)와 app_setting `classifier_provider_id=p.id` → `ConflictError`, 메시지에 `"@tutor"`와 `"인박스 분류"` 포함. 둘 다 없으면 삭제 성공.
- `test_agent_fields_validate_provider`: `create_agent(... {"backend":"ollama","provider_id":"missing"})` → `InvalidError`; 존재하는 id면 저장, `send_images=True` 저장.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_providers_services.py -q`
Expected: FAIL (ImportError / AttributeError)

- [ ] **Step 3: Implement model, services, migration**

`_check_unique_name`은 타입 힌트가 Area/Channel뿐이니 provider 전용 검사를 따로 둔다. 마이그레이션: `op.create_table("model_provider", ...)`
후 `batch_alter_table("agent")`에 `provider_id`(String(36)) + `create_foreign_key("fk_agent_provider_id_model_provider", "model_provider", ["provider_id"], ["id"], ondelete="SET NULL")`
+ `send_images`(Boolean, `server_default="0"`). downgrade는 `29e2c75b6680_coding_mode.py`처럼 raw `ALTER TABLE ... DROP COLUMN`, 그다음 `op.drop_table`.
리비전은 `uv run alembic revision --autogenerate -m "model provider"`로 만들고 손질.

- [ ] **Step 4: Verify**

Run: `cd backend && uv run pytest tests/test_providers_services.py -q && uv run alembic upgrade head && uv run alembic check`
Expected: PASS; `No new upgrade operations detected.`
(launchd 개발 서버가 떠 있으면 자동 리로드 전에 이 `upgrade head`가 먼저 돌아야 한다. CLAUDE.md "모델을 바꾸면" 규칙.)

- [ ] **Step 5: Commit**

```bash
git add backend/src/argos/models.py backend/src/argos/services.py backend/alembic/versions/*_model_provider.py backend/tests/test_providers_services.py
git commit -m "feat: store model providers and agent provider choice"
```

---

### Task 3: 설정 스냅샷과 시작 시 기본 연결

**Files:**
- Modify: `backend/src/argos/config.py:46-60` (필드 2개)
- Modify: `backend/src/argos/classifier.py:651` (`OVERRIDABLE`에 `"classifier_provider_id"`)
- Modify: `backend/src/argos/providers.py` (DB ↔ 스냅샷, 해석 함수)
- Modify: `backend/src/argos/main.py:42-50` (lifespan)
- Test: `backend/tests/test_providers_startup.py`

**Interfaces:**
- Consumes: Task 1 `ProviderConfig`, `Keychain`, `locality_of`, `effective_locality`; Task 2 `ModelProvider`, `create_provider`
- Produces:
  - `Settings.classifier_provider_id: str | None = None`; `Settings.model_providers: tuple[ProviderConfig, ...] = ()` (env에서 읽지 않음: `Field(default=(), exclude=True)`)
  - `async def ensure_default_provider(session, base: Settings, keychain: Keychain) -> None` — 연결이 0개일 때만 "이 Mac" 생성
  - `async def load_providers(session, keychain: Keychain) -> tuple[ProviderConfig, ...]` — `has_api_key`인 것만 키체인 읽기
  - `def resolve(settings: Settings, provider_id: str | None) -> ProviderConfig` — None 또는 없는 id → 기본 연결
  - `def classifier_provider(settings: Settings) -> ProviderConfig` — `classifier_provider_id`, 없으면 기본. 스냅샷이 비어 있으면(옛 테스트, 시작 전) `classifier_base_url` 또는 `http://127.0.0.1:11434/v1`로 만든 임시 기본 연결(id `""`, name `"이 Mac"`)
  - `def with_providers(settings: Settings, loaded: tuple[ProviderConfig, ...]) -> Settings` — `model_copy(update=...)`
  - `app.state.provider_keychain: providers.Keychain`

- [ ] **Step 1: Write the failing tests** (`app_client` 패턴은 `tests/test_settings.py` 재시작 테스트 참고)

- `test_startup_creates_default_from_env`: `Settings(classifier_base_url="http://gpu.test:11434/v1", classifier_api_key="k1")`로 시작 → `GET`(아직 API 없음이니 DB 직접) `model_provider` 1행: name `"이 Mac"`, kind `"ollama"`, base_url `"http://gpu.test:11434/v1"`, `is_default`, `has_api_key`; FakeKeychain에 `{id: "k1"}`.
- `test_startup_hermes_does_not_copy_key`: `classifier_provider="hermes"`, `classifier_base_url="http://hermes.test/v1"`, `classifier_api_key="hk"` → 기본 연결 base_url `"http://127.0.0.1:11434/v1"`, `has_api_key=False`, 키체인 비어 있음.
- `test_second_start_creates_nothing`: 같은 DB로 두 번 시작 → 1행.
- `test_startup_loads_snapshot`: 시작 후 `client.app.state.settings.model_providers[0].locality == "device"`이고 `app.state.base_settings`에도 있음.
- `test_startup_does_not_touch_network`: `monkeypatch.setattr(providers.httpx2, "AsyncClient", boom)`(호출되면 예외) 후 시작 성공.
- `test_classifier_provider_fallback_without_snapshot`: `classifier_provider(Settings(_env_file=None, classifier_base_url="http://x.test/v1")).base_url == "http://x.test/v1"`.

키체인: lifespan이 `app.state.provider_keychain = providers.Keychain()`를 쓰므로 시작 전 교체가 필요하다. `main.create_app(settings, provider_keychain: providers.Keychain | None = None)` 인자를 추가하고 테스트는 `create_app(settings, provider_keychain=FakeKeychain())`. conftest의 `client` 픽스처도 FakeKeychain을 넘긴다(실제 키체인에 닿는 테스트가 없게).

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_providers_startup.py -q`
Expected: FAIL

- [ ] **Step 3: Implement**

lifespan 순서: `seed_defaults` → `ensure_default_provider(session, config, keychain)` → `loaded = await load_providers(session, keychain)` →
`app.state.base_settings = providers.with_providers(config, loaded)` → `app.state.settings = apply_overrides(base_settings, overrides)` → 분류기·요약기·runner(기존 순서).

- [ ] **Step 4: Verify**

Run: `cd backend && uv run pytest -q`
Expected: 전체 PASS(기존 테스트가 실제 키체인에 닿지 않음)

- [ ] **Step 5: Commit**

```bash
git add backend/src/argos/config.py backend/src/argos/classifier.py backend/src/argos/providers.py backend/src/argos/main.py backend/tests/conftest.py backend/tests/test_providers_startup.py
git commit -m "feat: create default provider on startup and load provider snapshot"
```

---

### Task 4: 에이전트가 연결을 쓰게(`build_adapter`, 오류 문구, 이미지)

**Files:**
- Modify: `backend/src/argos/agents.py:189-209` (vision은 providers로), `:220-260` OpenAICompatAdapter, `:294-358` LLMToolAdapter, `:1110-1125` OLLAMA 분기
- Test: `backend/tests/test_agent_providers.py`

**Interfaces:**
- Consumes: `providers.resolve`, `classifier_provider`, `chat_client`, `vision`, `unreachable_text` (Task 1·3)
- Produces:
  - `OpenAICompatAdapter.__init__(..., vision=None, unreachable: str | None = None)` — `openai.APIConnectionError`(timeout 포함)면 `AgentUnavailable(unreachable)`, 스트림 도중 실패도 같은 처리
  - `LLMToolAdapter.__init__(..., vision=None, unreachable: str | None = None)` — 같은 규칙
  - `build_adapter` 시그니처는 그대로. 연결 선택: `agent.is_builtin` → `classifier_provider(settings)`(hermes 분류기면 `resolve(settings, None)`), 아니면 `resolve(settings, agent.provider_id)`
  - kind `ollama`: `extra={"reasoning_effort": "none"}`, `vision=lambda: providers.vision(p, model)`; kind `openai`: `extra=None`, `vision=None if agent.send_images else (async False)`
  - `agents.ollama_vision`은 제거(유일한 호출자였음)

- [ ] **Step 1: Write the failing tests**

`build_adapter`가 만든 client의 `base_url`/`api_key`를 확인(`adapter._client.base_url`) — 실제 요청 없이.
- `test_custom_agent_uses_its_provider`: 스냅샷에 기본(`http://127.0.0.1:11434/v1`)과 `gpu`(`http://gpu.test:11434/v1`), `Agent(backend=OLLAMA, provider_id=gpu.id, model="gemma4:e4b")` → client base_url `http://gpu.test:11434/v1/`.
- `test_agent_without_provider_uses_default` / `test_unknown_provider_id_uses_default`.
- `test_builtin_local_follows_classifier_provider`: `classifier_provider_id=gpu.id`, 내장 `local` → gpu.
- `test_builtin_local_with_hermes_classifier_uses_default`.
- `test_openai_kind_sends_key_and_no_reasoning_effort`: kind openai, api_key `"sk-test"` → client api_key `"sk-test"`, `adapter._extra is None`.
- `test_openai_kind_images_follow_send_images`: send_images False → `await adapter._vision()` False; True → `_vision is None`.
- `test_unreachable_provider_message`: OpenAICompatAdapter에 `create`가 `openai.APIConnectionError(request=httpx.Request("POST","http://gpu.test"))`를 내는 가짜 client → `AgentUnavailable("GPU에 연결할 수 없어요 (http://gpu.test:11434/v1)")`. LLMToolAdapter도 같은 테스트(`connect`는 가짜 MCP).
- `test_agent_follows_provider_after_rename`: 연결 이름·주소를 바꾼 새 스냅샷 → 같은 provider_id 에이전트가 새 주소.

- [ ] **Step 2: Run** `cd backend && uv run pytest tests/test_agent_providers.py -q` → FAIL

- [ ] **Step 3: Implement** (위 Interfaces 대로)

- [ ] **Step 4: Verify** `cd backend && uv run pytest tests/test_agent_providers.py tests/test_agents.py tests/test_custom_agents.py -q && uv run pyright` → PASS, 0 errors

- [ ] **Step 5: Commit**

```bash
git add backend/src/argos/agents.py backend/tests/test_agent_providers.py
git commit -m "feat: run local-model agents on their chosen provider"
```

---

### Task 5: 분류기·요약·임베딩·분류기 설정 API가 분류기 연결을 쓰게

**Files:**
- Modify: `backend/src/argos/classifier.py:614-647` (`build_classifier`), `:683-721` (`OllamaModel`/`list_ollama_models`)
- Modify: `backend/src/argos/summarizer.py:86-104` (`classifier_chat`)
- Modify: `backend/src/argos/notify.py:368-392` (`_ollama_embed`, `_find_embed_model`)
- Modify: `backend/src/argos/api.py:193-221` (`ClassifierSettingsOut/In`), `:1045` (`_classifier_settings`), `:2567-2620` (PUT/GET classifier, models)
- Test: `backend/tests/test_classifier_provider.py`

**Interfaces:**
- Consumes: `providers.classifier_provider`, `list_models`, `chat_client`
- Produces:
  - provider `ollama`일 때 `build_classifier`·`classifier_chat`·`_ollama_embed`·`list_ollama_models`의 주소·키 = `classifier_provider(settings)`. hermes는 기존 코드 경로 그대로.
  - `list_ollama_models(settings, *, decision=False) -> list[OllamaModel]`는 남기고 내부만 `providers.list_models(classifier_provider(settings), decision=...)`로(호출처 `api.py:1240, 2579, 2608` 유지). `ProviderError` → `ClassifierError`.
  - `ClassifierSettingsOut.provider_id: str`(최종 분류기 연결 id, 임시 기본이면 `""`), `ClassifierSettingsIn.provider_id: str | None = None`
  - `PUT /settings/classifier`: `provider_id`가 오면 그 연결이 kind `ollama`인지 확인(아니면 422 `"인박스 분류는 Ollama 연결만 쓸 수 있어요"`), 모델 검증도 그 연결 목록으로, `set_setting("classifier_provider_id", ...)` 후 settings·classifier·summarizer와 **`runner.settings`**도 새로 고침.
  - `GET /settings/classifier/models?provider_id=` (없으면 현재 분류기 연결)

- [ ] **Step 1: Write the failing tests**

- `test_classifier_uses_classifier_provider`: 스냅샷 `gpu`, overrides `classifier_provider_id=gpu.id`, `classifier_model="m"` → `build_classifier(settings)`의 chat client base_url이 gpu, `OllamaClassifier` root `http://gpu.test:11434`.
- `test_summarizer_and_matcher_follow_provider`: `classifier_chat(settings)[0].base_url` == gpu.
- `test_embed_uses_classifier_provider`: MockTransport가 받은 `/api/tags`와 `/v1/embeddings` 주소가 gpu.
- `test_hermes_paths_unchanged`: `classifier_provider="hermes"`, `classifier_base_url="http://hermes.test/v1"` + 스냅샷 gpu → classifier·summarizer·embed 모두 `hermes.test`.
- API: `test_put_classifier_provider_switch` (fake `providers.list_models`), `test_put_classifier_rejects_openai_provider` (422 문구), `test_classifier_models_for_provider` (`?provider_id=` 주소 확인), `test_runner_settings_refreshed_after_classifier_change`.

- [ ] **Step 2: Run** `cd backend && uv run pytest tests/test_classifier_provider.py -q` → FAIL

- [ ] **Step 3: Implement**

- [ ] **Step 4: Verify** `cd backend && uv run pytest tests/test_classifier_provider.py tests/test_settings.py tests/test_classifier.py tests/test_summarizer.py tests/test_notify.py -q` → PASS

- [ ] **Step 5: Commit** (`make api-types` 포함)

```bash
make api-types
git add backend/src/argos/classifier.py backend/src/argos/summarizer.py backend/src/argos/notify.py backend/src/argos/api.py backend/tests/test_classifier_provider.py frontend/src/api-types.ts
git commit -m "feat: let the inbox classifier choose its Ollama provider"
```

---

### Task 6: 연결 API와 다시 읽기

**Files:**
- Modify: `backend/src/argos/api.py` (새 모델·라우트, `agent_models :1216-1250`)
- Test: `backend/tests/test_providers_api.py`

**Interfaces:**
- Consumes: Task 2 services, Task 3 `load_providers`/`with_providers`, Task 1 `list_models`/`normalize_base_url`/`locality_of`
- Produces:
  - `ProviderOut(id, name, kind, base_url, has_api_key, strict_locality, locality, is_default)` — `locality`는 최종값
  - `ProviderIn(name: str | None, kind: ProviderKind | None, base_url: str | None, api_key: str | None, strict_locality: Locality | None)` — `api_key`: 생략 또는 null=그대로(새로 만들 때는 키 없음), `""`=지움, 값=저장
  - `ProviderTestIn(kind, base_url, api_key: str | None, provider_id: str | None)` — 키가 없고 `provider_id`가 있으면 저장된 키로 시험
  - `ProviderModelsOut(reachable: bool, error: str | None, locality: Locality, models: list[OllamaModelOut])` (기존 `OllamaModelOut` 재사용)
  - 라우트: `GET /providers -> list[ProviderOut]`, `POST /providers -> ProviderOut (201)`, `PATCH /providers/{provider_id} -> ProviderOut`,
    `DELETE /providers/{provider_id} -> 204`, `POST /providers/test -> ProviderModelsOut`, `GET /providers/{provider_id}/models -> ProviderModelsOut`
  - `async def _reload_providers(request, session) -> None` — 스냅샷 다시 읽어 `base_settings`·`settings`·`runner.settings`·classifier·summarizer 새로 만들고 `app.state.model_cache` 비움
  - `GET /agents/models?backend=ollama&provider_id=` — 캐시 키 `f"{backend}:{provider_id or ''}"`, 목록은 `providers.list_models(resolve(config, provider_id))`, 설명 문구는 위치별(`"이 컴퓨터에서 실행"` / `"내 네트워크에서 실행"` / `"클라우드 · 입력이 외부로 전송돼요"`, Ollama `remote` 모델은 기존 ollama.com 문구)

- [ ] **Step 1: Write the failing tests** (`client` + FakeKeychain)

- `test_provider_crud_roundtrip`: POST `{"name":"GPU","kind":"ollama","base_url":"http://gpu.test:11434","api_key":"k"}` → 201, `base_url` `/v1`로, `has_api_key` true, 응답 JSON에 `"k"` 없음, FakeKeychain에 저장; PATCH 이름 변경; GET 목록 2개(기본 + GPU); DELETE → 204, 키체인에서도 삭제.
- `test_patch_api_key_empty_clears` / 생략하면 유지.
- `test_delete_in_use_provider_409` (에이전트가 씀) / `test_delete_default_409`.
- `test_provider_test_endpoint`: fake `providers.list_models` → `reachable` true, 모델 이름, `locality` `"network"`(`http://100.100.1.1:11434`); 실패 → `reachable` false와 `error` 한국어.
- `test_cloud_locality_reported`: `https://api.example.test/v1` → `locality` `"cloud"`.
- `test_models_cache_follows_provider_edit`: 모델 목록 한 번 받고 PATCH base_url → 다시 받으면 fake가 새 주소로 호출됨.
- `test_keychain_failure_leaves_no_provider`: FakeKeychain.set이 `keyring.errors.PasswordSetError` → 422 `"키체인에 API 키를 저장하지 못했어요"`, `GET /providers`에 없음.
- `test_runner_sees_new_provider`: 연결 추가 후 `client.app.state.runner.settings.model_providers`에 포함.

- [ ] **Step 2: Run** `cd backend && uv run pytest tests/test_providers_api.py -q` → FAIL

- [ ] **Step 3: Implement**

생성 순서: 키 검증 → `create_provider`(has_api_key) → 키체인 저장, 실패하면 방금 행을 `delete_provider`로 지우고 422. 수정 때는 키체인 먼저, 성공해야 `has_api_key` 갱신.

- [ ] **Step 4: Verify** `cd backend && uv run pytest -q && uv run pyright && uv run ruff check` → PASS

- [ ] **Step 5: Commit**

```bash
make api-types
git add backend/src/argos/api.py backend/tests/test_providers_api.py frontend/src/api-types.ts
git commit -m "feat: add model provider API"
```

---

### Task 7: 에이전트 API의 연결 필드와 YAML 내보내기/가져오기

**Files:**
- Modify: `backend/src/argos/api.py:475-505` (`AgentOut`, `AgentIn`), `:1149` (`_save_agent`), `:1297-1353` (export/import)
- Test: `backend/tests/test_custom_agents.py` (기존 파일에 추가)

**Interfaces:**
- Consumes: Task 2 `_check_agent_fields`가 받는 `provider_id`, `send_images`
- Produces:
  - `AgentOut.provider_id: str | None`, `AgentOut.send_images: bool`; `AgentIn.provider_id: str | None`, `AgentIn.send_images: bool | None`
  - YAML 키: `model` 다음에 `provider: <연결 이름>`(provider_id가 있을 때만), `send_images: true`(true일 때만)
  - `AgentImportOut(AgentOut)` + `notice: str | None` — 이름이 없으면 `"'<이름>' 연결이 없어 기본 연결로 가져왔어요"`
  - `POST /agents/import -> AgentImportOut (201)`

- [ ] **Step 1: Write the failing tests**

- `test_create_agent_with_provider`: POST `{**TUTOR, "provider_id": gpu_id, "send_images": True}` → 응답에 그대로.
- `test_export_writes_provider_name`: export YAML에 `provider: GPU`, `send_images: true`.
- `test_import_matches_provider_by_name`: 같은 이름 연결 있음 → provider_id 그 연결, `notice` None.
- `test_import_unknown_provider_falls_back`: 없는 이름 → provider_id None, `notice` 문구.
- `test_import_old_yaml_without_provider`: 기존 YAML → provider_id None, notice None.

- [ ] **Step 2: Run** `cd backend && uv run pytest tests/test_custom_agents.py -q` → FAIL

- [ ] **Step 3: Implement**

- [ ] **Step 4: Verify** `cd backend && uv run pytest -q` → PASS

- [ ] **Step 5: Commit**

```bash
make api-types
git add backend/src/argos/api.py backend/tests/test_custom_agents.py frontend/src/api-types.ts
git commit -m "feat: carry provider choice through agent API and YAML"
```

---

### Task 8: 프런트 — 모델 연결 섹션

**Files:**
- Modify: `frontend/src/api.ts` (`invalidateFor :119-171`, 새 훅, `errorText :47`)
- Create: `frontend/src/pages/ProviderSettings.tsx`
- Create: `frontend/src/pages/ProviderSettings.test.tsx`
- Modify: `frontend/src/pages/SettingsPage.tsx:176-205` (`section("agents", ...)`에서 `CustomAgentsSection` 앞에 `ProviderSection`)
- Modify: `frontend/src/en.ts`, `frontend/src/en-templates.ts`

**Interfaces:**
- Consumes: Task 6 API 타입(`Schemas["ProviderOut"]` 등)
- Produces:
  - `export type Provider = Schemas["ProviderOut"]`
  - `useProviders()` (key `["providers"]`), `useSaveProvider()` (`{editing?: string, ...ProviderIn}` → POST/PATCH, `useSaveAgent` 패턴), `useDeleteProvider()`,
    `useTestProvider()` (`useMutation`, 무효화 없음), `useProviderModels(id: string | undefined)` (key `["providers", id, "models"]`)
  - `invalidateFor` 키: `model_provider: [["providers"], ["agents"], ["settings", "classifier"], ["settings", "ollama-models"]]`
  - `errorText` 정규식: `/^(.+)에 연결할 수 없어요 \((.+)\)$/` → 영어 `"Can't reach {0} ({1})"`
  - `export function ProviderSection(): JSX.Element`

- [ ] **Step 1: Write the failing tests** (`vi.mock("../api", ...)` 부분 모킹, `LmsSettings.test.tsx` 패턴)

- `lists providers with locality badge`: 연결 2개(device, network) → 이름, 주소, 배지 `"이 기기"`/`"내 네트워크"`, 기본 연결엔 삭제 버튼 없음.
- `adds a provider after a successful test`: "연결 추가" → 이름·종류(Ollama)·주소 입력 → "연결 시험" → `useTestProvider().mutate`가 `{kind:"ollama", base_url:"http://gpu.test:11434", api_key: null}`로 불리고, 결과 `"모델 3개"` 표시 → 저장 → `useSaveProvider().mutate`가 `{name:"GPU", kind:"ollama", base_url:"http://gpu.test:11434", api_key: null, strict_locality: null}`.
- `warns for cloud locality`: 시험 결과 `locality: "cloud"` → `role="alert"`에 `"대화 내용이 이 컴퓨터와 내 네트워크 밖으로 나가요"`.
- `api key field never shows the saved key`: `has_api_key` true인 연결 편집 → 입력칸 비어 있고 `"저장됨"` 표시, 비워 두고 저장하면 payload에 `api_key` 없음, "키 지우기" 누르면 `api_key: ""`.
- `delete error shows users`: `useDeleteProvider` error `"@tutor, 인박스 분류에서 쓰는 연결이에요"` → 그대로 표시.

- [ ] **Step 2: Run** `cd frontend && bunx vitest run src/pages/ProviderSettings.test.tsx` → FAIL

- [ ] **Step 3: Implement**

카드 패턴은 `RemoteSettings.tsx`, 폼은 `ui.tsx`의 `Dialog`·`field`·`label`·`btn`. 위치 배지 문구: device `"이 기기"`, network `"내 네트워크"`, cloud `"클라우드"`(cloud만 `bg-danger-bg text-danger`).
"더 엄격하게 표시" 선택지: `"자동"`, `"내 네트워크로"`(계산값이 device일 때만), `"클라우드로"`.

- [ ] **Step 4: Verify** `cd frontend && bunx vitest run && bunx tsc --noEmit && bunx biome check src` → PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api.ts frontend/src/pages/ProviderSettings.tsx frontend/src/pages/ProviderSettings.test.tsx frontend/src/pages/SettingsPage.tsx frontend/src/en.ts frontend/src/en-templates.ts
git commit -m "feat: add model provider settings section"
```

---

### Task 9: 프런트 — 에이전트 편집과 인박스 분류에서 연결 고르기

**Files:**
- Modify: `frontend/src/api.ts:1157` (`useAgentModels`), `:604-625` (`useOllamaModels`, `useSaveClassifier`)
- Modify: `frontend/src/pages/CustomAgents.tsx:17-29` (BACKENDS 문구), `:145-260` (`AgentDialog`), `:335-400` (`ModelField`), `:91` (목록 행)
- Modify: `frontend/src/pages/SettingsPage.tsx:233-497` (`ClassifierSection`), `:499-560` (`BACKEND_TEXT`, `ModelRow`)
- Create: `frontend/src/pages/CustomAgents.test.tsx`, `frontend/src/pages/ClassifierSection.test.tsx`
- Modify: `frontend/src/en.ts`

**Interfaces:**
- Consumes: Task 7 `AgentIn.provider_id/send_images`, Task 5 `ClassifierSettingsIn/Out.provider_id`, `GET /settings/classifier/models?provider_id=`, Task 8 `useProviders`
- Produces:
  - `useAgentModels(backend: string, providerId?: string | null)` — queryKey `["agents","models",backend, providerId ?? ""]`, query에 `provider_id`
  - `useOllamaModels(providerId?: string)` — queryKey `["settings","ollama-models", providerId ?? ""]`
  - `useSaveClassifier` body에 `provider_id?: string`
  - CustomAgents `BACKENDS`의 ollama: label `"모델 연결"`, hint `"Ollama나 OpenAI 호환 서버의 모델"`

- [ ] **Step 1: Write the failing tests**

CustomAgents.test.tsx:
- `picks provider then model`: 백엔드 "모델 연결" → 연결 select(`id="agent-provider"`)에 기본 + GPU, GPU 선택 시 `useAgentModels`가 `("ollama", gpuId)`로, 모델 선택 후 저장 payload `{..., backend:"ollama", provider_id: gpuId, model:"gemma4:e4b", send_images: false}`.
- `send images toggle only for openai providers`: Ollama 연결이면 토글 없음, OpenAI 호환 연결이면 `"이미지 보내기"` 체크박스.
- `changing provider clears model`.

ClassifierSection.test.tsx:
- `provider select lists only ollama providers`: 연결 3개(ollama 2, openai 1) → select에 2개.
- `saves provider with model`: GPU 선택 + 모델 → `useSaveClassifier().mutate`가 `{model, provider_id: gpuId}`.
- `hermes hides provider select`.

- [ ] **Step 2: Run** `cd frontend && bunx vitest run src/pages/CustomAgents.test.tsx src/pages/ClassifierSection.test.tsx` → FAIL

- [ ] **Step 3: Implement**

`ClassifierSection`의 ollama.com·`.env` 문구(`:312-315`, `:465`)는 선택한 연결 이름 기준으로("<이름>에서 실행" / 클라우드 경고). `ModelRow`의 ollama 대체 문구는 `"인박스 분류 모델과 같게"` 유지.

- [ ] **Step 4: Verify** `cd frontend && bunx vitest run && bunx tsc --noEmit && bunx biome check src` → PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api.ts frontend/src/pages/CustomAgents.tsx frontend/src/pages/CustomAgents.test.tsx frontend/src/pages/SettingsPage.tsx frontend/src/pages/ClassifierSection.test.tsx frontend/src/en.ts
git commit -m "feat: choose model provider for agents and the inbox classifier"
```

---

### Task 10: 문서, 전체 검사, 화면 확인

**Files:**
- Modify: `README.md`, `docs/*.md` 중 `ARGOS_CLASSIFIER_BASE_URL`을 언급하는 곳(`grep -rn CLASSIFIER_BASE_URL README.md docs backend/.env.example`)
- Modify: `backend/.env.example`(있으면)

- [ ] **Step 1: 문서 고치기** — `ARGOS_CLASSIFIER_BASE_URL`: "provider `ollama`면 처음 실행 때 '이 Mac' 연결의 초기값으로만 읽음, 이후는 설정 → 모델 연결. `hermes`면 매번 읽음".

- [ ] **Step 2: 전체 검사**

Run: `make lint && make test && cd backend && uv run alembic check`
Expected: 모두 통과, `No new upgrade operations detected.`

- [ ] **Step 3: 실제 화면 확인** (CLAUDE.md "화면 확인") — Orca 터미널이면 `http://127.0.0.1:5273/argos/` 설정 화면을 라이트/다크로:
연결 추가(사용자의 tailnet Ollama 주소로 시험 → 모델 목록), 에이전트 만들기(그 연결의 모델), 그 에이전트에 DM으로 질문해 답이 오는지, 이미지 첨부 1장.
Orca가 아니면 보고에 "화면 확인 못 함". 개발 DB에서 만든 연결·에이전트는 확인 후 사용자에게 남길지 묻는다.

- [ ] **Step 4: Commit**

```bash
git add README.md docs backend/.env.example
git commit -m "docs: explain model providers and classifier base url"
```
