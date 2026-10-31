# 모델 연결(프로바이더) 여러 개 (Phase 20 설계)

2026-10-31 사용자 요청. 음성 비서는 미룸(아래 "범위 밖"). 다음 작업인 비공개 대화는 별도 spec이고,
이 설계는 그 작업이 쓸 "로컬/클라우드 구분"만 미리 둔다.

## 목표

지금은 로컬 모델 주소가 `.env`의 `ARGOS_CLASSIFIER_BASE_URL` 하나다. 인박스 분류기·제목·LMS 요약·주간
회고 임베딩·로컬 모델 에이전트가 모두 그 주소를 같이 쓰고, 앱에서 바꿀 수 없다. 사용자는 작은 분류 모델은
이 Mac의 Ollama에 두고, 큰 모델(예: `gemma4:e4b`)은 Tailscale로 연결된 GPU 서버(mlbox, Ollama
`http://100.126.152.1:11434`)에서 돌려 에이전트로 쓰고 싶다. 나중에는 LM Studio·vLLM·OpenRouter 같은
OpenAI 호환 서버도 같은 방식으로 붙이고 싶다.

성공 기준:

- 설정 화면에서 이름 붙은 연결을 추가·수정·삭제할 수 있다. 추가할 때 접속을 시험하고 모델 목록을 보여 준다.
- 커스텀 에이전트(로컬 모델 백엔드)를 만들 때 연결을 고르고, 그 연결의 모델 목록에서 모델을 고른다.
  mlbox의 `gemma4:e4b`로 `@gemma`를 만들어 대화·도구 호출·이미지 첨부가 된다.
- 인박스 분류기는 지금처럼 이 Mac의 Ollama를 쓰고, 아무것도 다시 설정하지 않아도 업그레이드 전과 똑같이 동작한다.
- 연결이 꺼져 있으면 다른 연결로 넘어가지 않고, 답 카드에 "<연결 이름>에 연결할 수 없어요"가 뜬다.
- 연결 주소가 이 Mac·내 네트워크 밖이면 "대화 내용이 외부로 나가요" 경고가 보인다.
- 연결마다 로컬/클라우드 구분이 계산되어 API로 나간다(다음 작업 비공개 대화가 씀).

## 범위 밖

- 음성 비서(STT/TTS). 조사 결과는 대화 기록에만 있고, 나중에 따로 brainstorm한다.
- 비공개 대화(다음 작업, 별도 spec).
- Anthropic·Gemini 등 업체 고유 API. OpenAI 호환 API만.
- 연결별 사용량·비용 집계.
- 연결이 꺼졌을 때 다른 연결로 자동 전환.
- Hermes 경로(`classifier_provider = "hermes"`, Hermes 에이전트)는 그대로 둔다.

## 용어

- **연결(provider)**: 이름, 종류, 주소, (선택) API 키를 가진 모델 서버 하나.
- **종류(kind)**: `ollama` 또는 `openai`(일반 OpenAI 호환).
- **위치(locality)**: `device`(이 기기), `network`(내 네트워크: 사설 대역·Tailscale), `cloud`(그 밖). 주소에서 계산.

## 결정 사항

### 사용자가 정한 것

- 연결은 여러 개, 이름을 붙여 등록하고 골라 쓴다(주소 하나만 바꾸는 방식이 아님).
- 작은 분류 모델은 계속 이 Mac에서 돌린다.
- 일반 OpenAI 호환 서버도 붙일 수 있어야 한다.
- 다음 작업인 비공개 대화는 로컬 모델에서만 켤 수 있어야 한다 → 이 설계에서 위치 구분을 미리 둔다.

### 내 판단 (사용자 확인 전)

- **기본 연결 자동 생성**: 업그레이드 후 첫 시작 때 서버 시작 코드가(Alembic 마이그레이션이 아니라: 마이그레이션은
  앱 코드·`.env`·키체인을 건드리지 않는다는 규칙) 연결이 하나도 없으면 "이 Mac" 연결을 만든다. 종류 `ollama`, 주소는
  `classifier_provider = "ollama"`이고 `classifier_base_url`이 있으면 그것, 아니면 `http://127.0.0.1:11434/v1`.
  `classifier_api_key`는 provider가 `ollama`일 때만 이 연결의 키로 옮긴다(hermes일 때는 Hermes 키라 옮기지 않음).
  이 연결이 기본 연결이며 지울 수 없다(수정은 가능). 마이그레이션은 테이블과 열만 만든다.
- **Hermes 분류기는 그대로**: `classifier_provider = "hermes"`면 분류기·LMS 요약·회고 임베딩은 지금처럼 매 시작
  `classifier_base_url`·`classifier_api_key`를 읽어 Hermes로 간다. 분류기 연결 선택은 provider가 `ollama`일 때만 보이고 쓰인다.
- **내장 로컬 모델 에이전트**(`local`): 분류기 연결을 따라간다. 모델도 지금처럼 `classifier_title_model` 또는
  `classifier_model`. 그래야 분류기 연결을 바꿔도 모델 이름과 연결이 어긋나지 않는다. hermes 분류기일 때는 기본 연결.
- **에이전트 내보내기/가져오기**(YAML, `/agents/{name}/export`, `/agents/import`): 연결 id 대신 연결 **이름**과
  `send_images`를 적는다. 가져올 때 같은 이름의 연결이 있으면 그것, 없으면 기본 연결로 두고 응답에 그렇게 했다고 알린다.
  예전 YAML(연결 항목 없음)은 기본 연결.
- **클라우드 연결 + 도구**: 따로 경고하지 않는다(사용자 결정 2026-10-31). 연결 추가 때의 클라우드 경고로
  충분하고, Claude·Codex 에이전트도 경고 없이 도구를 쓴다.
- **API 키는 macOS 키체인**: `keyring`(서비스 이름 상수, 계정 = 연결 id), CalDAV 비밀번호와 같은 방식. DB에는
  "키 있음" 여부만. API 응답에 키를 절대 넣지 않는다.
- **위치 계산 규칙**: 호스트가 루프백(`127.0.0.0/8`, `::1`, `localhost`) → `device`. 사설 대역
  (`10/8`, `172.16/12`, `192.168/16`), CGNAT·Tailscale(`100.64.0.0/10`), `*.ts.net`, `*.local` → `network`.
  나머지(공인 IP, 일반 도메인) → `cloud`. 사용자는 더 엄격하게(예: `network`를 `cloud`로) 표시할 수만 있고 반대는
  안 된다. Ollama 모델이 `*-cloud`(목록의 `remote_host`)면 연결이 로컬이어도 그 모델은 클라우드로 본다(지금 경고와 같음).
- **어디서 연결을 고르나**:
  - 커스텀 에이전트(로컬 모델 백엔드): 연결 + 모델. 종류 `ollama`, `openai` 모두.
  - 내장 로컬 모델 에이전트: 분류기 연결을 따라간다(위 항목).
  - 인박스 분류기·제목 모델·LMS 요약: 연결을 고를 수 있게 하되 종류 `ollama`만(결정 모델 `/v1/systemone`과
    `/api/tags`가 Ollama 전용이라). 기본값은 기본 연결. 사용자의 지금 설정은 그대로 이어진다.
  - 주간 회고 임베딩(`notify._ollama_embed`): 분류기와 같은 연결을 쓴다(따로 고르지 않음).
- **종류별 동작**:
  - `ollama`: 모델 목록 `GET <root>/api/tags`(지금 `list_ollama_models`), 이미지 지원 `POST /api/show`
    (지금 `ollama_vision`), 채팅 `<base>/v1` OpenAI 호환, `reasoning_effort: "none"` 기본.
  - `openai`: 모델 목록 `GET <base>/models`(OpenAI 형식 `data[].id`), 이미지 지원은 알 수 없으니 에이전트 편집에서
    "이미지 보내기" 켜고 끄기(기본 끔), `reasoning_effort`는 보내지 않는다(서버마다 거부할 수 있음).
- **접속 실패 표시**: 어댑터가 연결 실패를 `AgentUnavailable("<연결 이름>에 연결할 수 없어요 …")`로 바꾼다.
  자동 전환 없음.
- **연결 삭제**: 그 연결을 쓰는 에이전트나 분류기 설정이 있으면 409와 함께 사용처를 알려 준다(조용히 깨지지 않게).
- **`.env`의 `classifier_base_url`·`classifier_api_key`**: provider `ollama`에서는 기본 연결을 만들 때 한 번만 읽는다
  (이후 바꾸려면 앱의 연결 설정). provider `hermes`에서는 지금처럼 매번 읽는다. 문서에 이 구분을 적는다.

## 데이터

새 테이블 `model_provider` (`Record` 기반, `services` 헬퍼로만 씀 → activity_log·WS 이벤트):

| 열 | 형 | 메모 |
|---|---|---|
| `id` | str | |
| `name` | str(60), unique | 표시 이름("이 Mac", "mlbox") |
| `kind` | `ollama` \| `openai` | |
| `base_url` | str(500) | `/v1`까지. 저장 전 정규화(끝 `/` 제거, http/https만) |
| `has_api_key` | bool | 실제 키는 키체인 |
| `strict_locality` | str \| null | 사용자가 더 엄격히 표시한 위치(`network`/`cloud`), 없으면 계산값 |
| `is_default` | bool | 하나만 true, 삭제 불가 |

`agent`에 `provider_id`(FK, null = 기본 연결)와 `send_images`(bool, `openai` 종류에서만 의미) 추가.
분류기 연결은 `app_setting`의 새 키 `classifier_provider_id`(`OVERRIDABLE`에 추가).
Alembic 마이그레이션(`render_as_batch`)은 테이블과 열만 만든다. 기본 연결 행과 키 이전은 서버 시작 코드
(위 "기본 연결 자동 생성", 데스크톱 앱도 같은 시작 경로).

## 백엔드

- 새 모듈 `providers.py`: `Provider` 값 객체, `locality(base_url) -> Locality`, `list_models(provider)`(종류별),
  `vision(provider, model)`, `client(provider, timeout) -> AsyncOpenAI`, 키체인 읽기/쓰기(테스트용 메모리 대체 주입).
- `agents.build_adapter`의 `AgentBackend.OLLAMA` 분기: `settings.classifier_base_url` 대신 에이전트의 연결을
  쓴다. 백엔드 이름 `ollama`는 그대로 두고 UI 표시만 "로컬 모델"→"모델 연결"로(enum 값 변경은 마이그레이션 위험 대비 이득 없음).
- `build_adapter`는 동기 함수이고 DB 세션이 없으니, 연결은 `app.state`의 연결 캐시에서 찾아 넘긴다. 호출하는 곳
  (runner, debate, notify, `_agent_status`)이 모두 이 캐시를 받는다.
- `GET /agents/models?backend=ollama`는 연결 id를 받고, 캐시 키에 연결 id를 넣는다.
- `classifier.build_classifier`, `summarizer.classifier_chat`, `notify._ollama_embed`, `api` 모델 목록 엔드포인트가
  provider `ollama`일 때 `classifier_base_url` 대신 분류기 연결을 쓴다(hermes는 그대로). 연결 조회는 시작 시와 설정 변경 시 `app.state`에 캐시
  (요청마다 DB를 읽지 않게), 연결이 바뀌면 분류기·요약기를 다시 만든다(지금 `put_classifier_settings` 방식).
- API(`/api/v1/providers`): 목록·생성·수정·삭제, `POST /providers/test`(저장 전 주소·키로 접속 시험 + 모델 목록),
  `GET /providers/{id}/models`. 응답에 `locality`(최종값)와 `has_api_key`. `make api-types` 갱신.
- 외부 접속 규칙: 주소는 사용자가 넣은 것만, 리다이렉트 따르지 않음, 타임아웃 5초(목록)·`agent_timeout`(채팅).
- MCP 서버는 바꾸지 않는다.

## 프런트엔드

- 설정에 "모델 연결" 섹션(목록 카드: 이름, 종류, 주소, 위치 배지, 연결 상태). 추가/수정 폼: 이름, 종류,
  주소, API 키(입력만, 저장 후 "저장됨"으로 표시), "더 엄격하게 표시" 선택, "연결 시험" 버튼 → 모델 수와 오류.
  클라우드 위치면 경고 문구.
- 커스텀 에이전트 편집(`CustomAgents.tsx`): 로컬 모델 백엔드일 때 연결 선택 → 그 연결의 모델 목록.
  `openai` 종류면 "이미지 보내기" 토글.
- 인박스 분류 설정: 모델 선택 위에 연결 선택(`ollama` 종류만 나열).
- `invalidateFor`에 `model_provider` 추가.
- 새 문구는 모두 `tr()`(한국어/영어)로.
- 디자인 캔버스(`docs/design/*.dc.html`)의 설정 섹션 패턴과 색 토큰을 따른다. 라이트/다크 화면 확인.

## 테스트

- `tests/conftest.py`의 `settings` 픽스처 사용, 실제 Ollama 없음.
- httpx `MockTransport`로 가짜 Ollama(`/api/tags`, `/api/show`)와 가짜 OpenAI 서버(`/v1/models`).
- 키체인은 메모리 대체 구현 주입.
- 위치 계산 표 테스트(루프백, 사설, `100.x`, `*.ts.net`, 공인 IP, 일반 도메인, IPv6).
- 마이그레이션: 기존 `classifier_base_url`이 기본 연결로 옮겨지고, 분류기 동작이 바뀌지 않음.
- 첫 시작 때 기본 연결 생성(ollama/hermes 각각), 두 번째 시작에 중복 생성 없음.
- hermes 분류기 설정에서 분류기·요약·임베딩 주소가 바뀌지 않음.
- 내보내기/가져오기: 연결 이름 일치, 없는 이름 → 기본 연결, 예전 YAML.
- 에이전트가 고른 연결로 요청이 가는지(`fake` 서버가 받은 주소 확인), 꺼진 연결 오류 문구, 사용 중 연결 삭제 409.
- vitest: 연결 폼(시험 결과·경고), 에이전트 편집의 연결 → 모델 목록 연동.

## 문서

- `docs/PLAN.md`에 "Phase 20 모델 연결 (추가 범위, 사용자 요청 2026-10-31)" 추가.
- `docs/decisions.md`에 위 "내 판단" 항목들.
- `README`/`docs/mcp-setup.md` 등 `ARGOS_CLASSIFIER_BASE_URL` 언급을 "첫 실행 초기값"으로 고침.
- `CHANGELOG.md`는 릴리스 때.

## 빌더에게 남긴 것

- 섹션·폼의 정확한 배치와 문구(디자인 캔버스 패턴 안에서).
- `providers.py` 내부 구조와 캐시 무효화 방식.
- 모델 목록 캐시 시간(지금 에이전트 모델 목록 캐시와 맞추면 됨).
