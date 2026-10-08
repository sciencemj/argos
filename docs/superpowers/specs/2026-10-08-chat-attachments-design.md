# 채팅 첨부 파일 설계

- 날짜: 2026-10-08
- 상태: 설계 승인됨, 구현 계획 전
- PLAN: "Phase 13. 첨부 파일 (추가 범위, 2026-10-08 사용자 요청)"으로 추가 예정

## 1. 목표

채팅에 파일과 이미지를 붙인다. 클립보드 붙여넣기(⌘V), Finder에서 드래그 앤 드롭, 📎 버튼(파일 선택기)
세 가지로 넣을 수 있다. 에이전트는 첨부 내용을 실제로 읽는다.

**사용자가 정한 것**
- 에이전트가 첨부를 읽는다(피드에 기록만 하는 것이 아님).
- 에이전트가 이해하는 종류: 이미지, 텍스트류(txt/md/csv/코드/json), PDF. 그 외 파일은 저장·다운로드만
  되고 에이전트에게는 이름만 간다.
- 첨부 가능한 곳: 채널 입력창, 스레드 답글, DM, 빠른 입력 창(⌘⇧Space).
- 방식: 2단계 업로드(붙이는 즉시 업로드, 보낼 때 id로 연결) + 어댑터별 네이티브 전달.

**가정**
- 이 Mac에서 쓰는 개인 앱이고 파일은 스크린샷, 강의 PDF 정도의 크기다.
- 브라우저(Tailscale 개발 접속, 아이패드)에서도 붙여넣기와 📎 버튼은 동작한다. Finder 드래그는 Mac에서만.

**범위 밖**
- 에이전트가 파일을 만들어 첨부하는 기능, MCP로 첨부 읽기, 첨부 파일 백업, 토론·인박스 분류에 첨부 사용.

## 2. 데이터 모델과 저장

새 테이블 `attachment`(`Attachment(Record)`):

| 열 | 내용 |
|---|---|
| `message_id` | `message.id` FK, `ondelete=CASCADE`, nullable. 비어 있으면 올렸지만 아직 안 보낸 상태 |
| `name` | 정리한 원래 파일 이름(경로 구분자·제어 문자 제거, 길이 제한) |
| `mime` | 저장한 파일의 mime |
| `size` | 바이트 |
| `kind` | `image` \| `text` \| `pdf` \| `file`. 업로드 때 내용(매직 바이트)과 확장자로 판단, 브라우저 mime은 믿지 않음 |
| `width`, `height` | 이미지만. 피드에서 자리를 먼저 잡는 데 사용 |

- 파일은 `attachments_dir/<id>`(기본 `data_dir/attachments`)에 저장. 경로에 사용자 파일명을 쓰지 않는다.
  `attachments_dir`는 `Settings._inside_data_dir`에 등록.
- 설정: `attachment_max_mb`(기본 25), 메시지당 최대 10개.
- 마이그레이션: Alembic autogenerate 후 `alembic check`.

## 3. API

- `POST /api/attachments` (multipart, 파일 하나) → `AttachmentOut{id, name, mime, size, kind, width, height, url}`.
  디스크로 스트리밍하다 한도를 넘으면 중단하고 413. HEIC/HEIF는 업로드 때 JPEG로 변환해 저장
  (어느 브라우저에서나 보이게).
- `GET /api/attachments/{id}/content` → 파일.
  - png/jpg/gif/webp만 `inline`, 나머지(SVG, HTML 포함)는 `Content-Disposition: attachment`.
  - 항상 `X-Content-Type-Options: nosniff`, `Content-Security-Policy: sandbox`. 앱과 같은 출처에서 SVG/HTML
    스크립트가 API 권한으로 실행되는 것을 막는다.
  - 파일이 디스크에 없으면 404.
- `DELETE /api/attachments/{id}` → 보내기 전 첨부만(입력창 칩의 ✕). 이미 메시지에 붙었으면 409.
- `MessageCreate.attachment_ids: list[str]`(최대 10). 첨부가 있으면 `body`는 비어도 된다. 둘 다 비면 422.
  이미 다른 메시지에 붙은 id면 409, 없는 id면 404.
- `MessageOut.attachments: list[AttachmentOut]`.
- 모델이 바뀌므로 `make api-types` 실행 후 커밋.

**쓰기 경로**: 업로드는 `services._create`, 메시지 연결은 `chat.post_message`와 같은 트랜잭션에서
`services`를 거친다. 활동 기록과 WS `object.*` 이벤트가 평소처럼 나간다. 프런트 `invalidateFor`에
`attachment` 추가.

**정리**: 서버 시작 시
- 24시간 넘게 안 보낸 첨부 → 행과 파일 삭제.
- 행이 없는 파일(채널 삭제로 cascade된 것 등) → 삭제.

**백업**: DB 백업은 지금처럼 DB만. 첨부 파일은 백업되지 않는다(`backlog.md`에 기록). 복원 후 파일이 없으면
화면에 "파일 없음"으로 표시.

## 4. 에이전트 전달

### 4.1 공통 변환 (`attachments.py`)

- `Turn`에 `attachments: tuple[AttachmentRef, ...]`(경로, mime, 이름, kind) 추가. 러너가 대화 기록을 만들 때
  각 메시지의 첨부를 넣는다.
- 첨부 하나 → 에이전트용 조각:
  - **이미지**: 긴 변 2000px 초과면 축소, 5MB 아래로 맞춤(Pillow). 원본 파일은 그대로.
  - **텍스트**: 본문에 코드 블록으로 삽입. 파일당 100KB까지, 넘으면 잘랐다고 표시.
  - **PDF**: Claude는 문서 블록으로 원본 전달. 나머지는 pypdf로 뽑은 텍스트(한도 동일).
  - **그 외**: `[첨부: 이름, 크기 — 내용 읽기 불가]` 한 줄.
- 파일이 없거나 변환에 실패하면 `[첨부: 이름 — 읽지 못함]`으로 대체하고 대화는 계속한다.

### 4.2 어댑터별 전달

| 어댑터 | 이미지 | PDF |
|---|---|---|
| `ClaudeSDKAdapter` | `query(prompt=AsyncIterable[dict])`로 user 메시지 content에 image 블록(base64) | document 블록 |
| `CodexAppServerAdapter` | `turn/start` input에 `{"type": "localImage", "path": …}` | 텍스트 |
| `CLIAdapter`(codex exec) | `--image <파일>` | 텍스트 |
| `HermesResponsesAdapter` | `input_image`(data URL) | 텍스트(Hermes는 file 파트를 거부) |
| `OpenAICompatAdapter` / `LLMToolAdapter`(Ollama) | `image_url`(data URL). 비전 모델이 아니면 "이미지를 볼 수 없음" 안내 | 텍스트 |

- Ollama 비전 여부: `/api/show`의 `capabilities`에 `vision`이 있는지, 모델별로 캐시.
- 설치 버전으로 확인함(2026-10-08): claude-agent-sdk 0.2.159 `query`의 prompt가 `AsyncIterable[dict]`를
  받음, codex-cli 0.159 app-server `UserInput`에 `localImage`, `codex exec --image`, Hermes 게이트웨이
  `input_image`(http(s) 또는 `data:image/…`).
- **구현 첫 단계에서 실측 확인**: Claude CLI stream-json 입력이 document 블록을 받는지. 안 되면 PDF도
  텍스트로 보낸다.

### 4.3 대화 기록 범위

- 세션을 이어 가는 경우(Claude resume, Codex thread resume, Hermes conversation): 새 턴의 첨부만.
- 처음부터 보내는 경우(Ollama, 새 세션): 전체 기록의 첨부를 넣되 이미지는 최근 5개까지, 오래된 이미지는 이름만.

### 4.4 적용 범위

- 채팅, DM, 스레드, 코딩 모드, `/job`은 같은 `Turn` 경로라 모두 적용.
- 토론 턴과 인박스 분류는 텍스트만. 본문 없이 첨부만 있는 일반 메시지는 분류를 건너뛰고 피드에만 남긴다.
- 채팅 에이전트 샌드박스(Argos 도구만, decisions 2026-09-25)는 바꾸지 않는다. 내용이 프롬프트에 직접 들어간다.

## 5. 화면

### 5.1 넣기 (`Composer` 공통: 채널, 스레드, DM, 빠른 입력 창)

- **⌘V**: `clipboardData.files`가 있으면 첨부로. 텍스트만 있으면 기존 동작.
- **드래그 앤 드롭**: 입력창 위로 끌면 테두리 강조와 "여기에 놓아 첨부" 안내.
  데스크톱 앱은 메인 창·빠른 입력 창 모두 Tauri `dragDropEnabled: false`(아니면 드롭이 웹뷰에 오지 않음).
- **📎 버튼**: 입력창 도구줄, `<input type="file" multiple>`.

### 5.2 입력창 칩

- 입력창 위 한 줄. 이미지는 썸네일, 나머지는 아이콘·이름·크기.
- 올리는 중 `PawTrail`, 실패 시 빨간 표시와 "다시 시도", ✕로 제거(서버 DELETE).
- 올리는 중에는 보내기 비활성. 첨부가 있으면 본문 없이 보내기 가능.
- 용량·개수 초과는 칩에서 바로 안내("25MB까지 올릴 수 있어요").

### 5.3 피드 표시 (`AttachmentList`)

- **이미지**: 1장이면 크게, 여러 장이면 격자. 저장된 width/height로 자리 확보. 누르면 앱 안 라이트박스
  (Esc 닫기, ←→ 넘기기).
- **파일**: 이름·크기·종류 아이콘 카드. 브라우저에선 다운로드, 데스크톱 앱에선 기본 앱으로 열기
  (`desktop.ts`에 `openAttachment`, Tauri에 `open_attachment` 명령과 권한 추가).
- 파일 없음: 흐리게 "파일 없음".
- 피드, 스레드 패널, 에이전트 답 카드의 원 메시지에 같은 컴포넌트.

### 5.4 디자인·문구

- 색은 `index.css` 토큰만. 디자인 캔버스에 첨부 화면이 없으므로 `cards.tsx` 카드 스타일을 따르고
  라이트/다크 모두 확인.
- 문구는 한국어와 `en.ts` 영어 모두.

## 6. 의존성

- Pillow(이미지 축소·크기), pillow-heif(HEIC 변환), pypdf(PDF 텍스트). PyInstaller 번들 포함 확인.
- `decisions.md`에 도입 이유 기록.

## 7. 테스트

**백엔드 (pytest, 실제 에이전트 없이, `settings` 픽스처 사용)**
- 업로드: 내용 기반 종류 판별, 413, 파일명 정리, HEIC → JPEG, 이미지 크기.
- 제공: 이미지 inline, SVG/HTML attachment, `nosniff`·`CSP: sandbox`, 파일 없으면 404.
- 연결: `attachment_ids` 연결, 첨부만 보내기, 둘 다 비면 422, 이미 붙은 id 409, 보낸 첨부 DELETE 409.
- 정리: 24시간 지난 미전송 첨부와 고아 파일 삭제.
- `attachments.py`: 이미지 축소, 텍스트 100KB 자르기, PDF 텍스트, 읽기 실패 안내.
- 어댑터 요청 모양: 가짜 클라이언트로 Claude content 블록, Codex `localImage`/`--image`, Hermes
  `input_image`, Ollama `image_url`과 비전 아닌 모델 안내. 실제 출력 샘플은 `tests/fixtures/agents/`.
- 러너: 이어 가는 세션은 새 첨부만, 처음부터면 최근 이미지 5개만(`FakeAgent`가 받은 `Turn.attachments`).
- 테스트 이미지·PDF는 테스트 안에서 생성.

**프런트엔드 (vitest)**
- 붙여넣기·드롭 이벤트에서 파일 추출, 칩 상태, 보내기 활성 조건.
- 종류별 렌더링(이미지 격자, 파일 카드, 파일 없음).

**수동 확인 (별도 포트 스택 8001/5174, 개발 DB 건드리지 않음)**
- 데스크톱 앱 Finder 드래그, 스크린샷 ⌘V, 빠른 입력 창 붙여넣기.
- Claude, Codex, Hermes, Ollama 각각에 이미지와 PDF를 주고 실제로 읽는지.

## 8. 문서

- `PLAN.md`: Phase 13 추가(범위, 완료 기준).
- `decisions.md`: 2단계 업로드, 어댑터별 네이티브 전달, 의존성, 백업 범위.
- `backlog.md`: 첨부 파일 백업.
- `CLAUDE.md`: 아키텍처에 `attachments.py`와 저장 경로 한 줄.
