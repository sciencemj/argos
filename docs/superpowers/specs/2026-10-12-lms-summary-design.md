# LMS 알림 AI 요약과 알림 카드 (Phase 15a 설계)

2026-10-12 사용자 요청. 아침 요약(Phase 15b)은 별도 spec.

## 목표

LMS에서 온 알림(과제 외 항목)이 지금은 `제목\n\n원문\n\nURL` 평문 메시지라 읽기 어렵다.
알림을 구조화해 저장하고 피드에서 전용 카드로 보여 주며, 긴 알림은 AI가 2~4줄로 요약한다.
원문은 카드의 "원문 보기" 토글로 언제든 본다.

성공 기준:

- 피드에서 LMS 알림이 카드(종류 배지·제목·요약·원문 보기·LMS에서 열기)로 보인다.
- 긴 알림은 가져온 뒤 잠시 후 요약으로 바뀌고(실시간), 짧은 알림은 원문이 그대로 보인다.
- 분류기가 설정되지 않았거나 요약이 실패해도 지금처럼 원문이 보인다.
- 학교별 문구(Canvas 푸터 등)를 코드에 박지 않는다. 걸러내기는 요약 프롬프트의 몫이고 원문 보기에는 그대로 남는다.

범위 밖: 과제(Task) 요약, 기존 알림 소급 요약, 요약에서 할 일·일정 자동 추출.

## 데이터

새 테이블 `lms_notice` (Record 상속, Alembic 마이그레이션):

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `message_id` | FK message.id, unique, CASCADE | 알림 메시지 |
| `kind` | str(20), null | `announcement`/`activity`/`conversation`/`attendance`/`material`, 백필 행은 null |
| `title` | str(500) | 원래 제목(escape 전) |
| `text` | Text | 원문(escape 전) |
| `url` | str(2000) | LMS 주소 |
| `summary` | Text, null | `- ` 불릿 줄들 |
| `summary_state` | str(10) | `pending`/`done`/`skipped`/`failed` |
| `summary_hash` | str(64), null | 요약한 원문의 sha256 |

`Message.body`는 계속 평문을 둔다: 요약이 있으면 `제목\n\n요약\n\nURL`, 없으면 지금 형식(`services.lms_body`).
검색·에이전트 컨텍스트·MCP는 body만 읽으면 되므로 바뀌지 않는다.

기존 알림 백필(서버 시작 때 `services.backfill_lms_notices`, 멱등): `source_link`가 `learningx`이고
`object_type == "message"`인데 notice가 없는 메시지의 body를 `제목\n\n원문\n\nURL`로 나누고 Markdown escape를
되돌려 `lms_notice` 행을 만든다. `lms_key`가 해시라 종류를 알 수 없어 kind는 null(카드 배지 "LMS"). 상태는
`skipped`. 형식이 맞지 않는 body는 건너뛴다(카드 없이 지금처럼 표시).

## 흐름

1. `services.import_lms`가 과제 외 항목을 만들거나 내용이 바뀌어 갱신할 때, 같은 트랜잭션에서 `lms_notice`를
   만들거나 갱신한다(`_log`·WS 이벤트 포함).
2. 상태 결정: 요약기가 있고(`build_summarizer`가 None 아님) `lms_summary_min_chars > 0`이며 원문 길이가 그 이상이면
   `pending`, 아니면 `skipped`. 원문이 바뀌면 summary를 비우고 다시 결정한다.
3. `import_lms`는 결과에 새로 `pending`이 된 notice id 목록을 담아 돌려주고, API 핸들러(`lms.import_data`)가
   `app.state.lms_summaries` 워커 큐에 넣는다.
4. 워커(`summarizer.LmsSummaries`)가 하나씩 꺼내 `Summarizer.summarize`를 부른다. 그사이 원문이 또 바뀌었으면
   (`services.save_summary`가 원문을 비교) 버린다. 성공하면 `services`의 `_update`로 notice의 `summary`·`summary_state=done`·
   `summary_hash`와 메시지 body를 갱신한다(actor `system:lms`). 실패하면 `failed`.
5. `failed`는 다음 `import_lms`에서 그 항목이 다시 오면(내용이 같아도) `pending`으로 되돌려 재시도한다.
6. 서버 시작 시 워커 `start()`가 남은 `pending`을 큐에 넣는다. 종료 시 `stop()`이 태스크를 취소한다.

## 요약기 (`backend/src/argos/summarizer.py`)

- `build_summarizer(settings) -> Summarizer | None`: 분류기와 같은 provider·`base_url`·키·타임아웃으로
  `AsyncOpenAI`를 만든다. 모델은 `classifier_title_model`(채팅 모델)이 있으면 그것, 없으면 `classifier_model`.
  둘 다 없으면 None. 결정 전용 모델이라 답이 깨지면 실패로 처리된다.
- `Summarizer` Protocol: `async summarize(title: str, text: str, language: str) -> str`.
- 프롬프트(언어 설정 `language`에 따라 ko/en): 학교 LMS 알림임을 알리고, 학생이 알아야 할 것만 2~4개 `- ` 불릿으로,
  날짜·마감·장소·준비물·변경 사항은 빠뜨리지 말 것, 수신 안내·구독 해지 문구·URL·과목 코드는 뺄 것,
  원문에 없는 내용은 쓰지 말 것. 원문은 앞 4,000자만 보낸다.
- 출력 파서 `parse_bullets(content) -> str`: `- `/`* `/`• `로 시작하는 줄만 `- `로 맞춰 남기고 최대 4줄.
  하나도 없으면 `SummaryError`.
- 테스트용 `FakeSummarizer`(`tests/fakes.py`).

## API·프런트

- `MessageOut.ref`에 `lms_notice: LmsNoticeOut | None`(`kind, title, text, url, summary, summary_state`).
  메시지 목록 조회에서 함께 읽는다. `make api-types` 실행.
- `api.ts`의 `invalidateFor`에 `lms_notice` → 메시지 쿼리 무효화.
- `lmsNotice.tsx`에 `LmsNoticeCard`(훅 없는 단독 컴포넌트라 테스트하기 쉽게). `feed.tsx`의 `MessageItem`은 `ref.lms_notice`가 있으면 body 대신 카드를 그린다.
  - 헤더: 종류 배지(과제 외 5종의 한국어 이름, `tr()`로) + 제목. 과목 이름은 채널과 겹치므로 넣지 않는다.
  - 본문: `done`이면 요약 불릿, `pending`이면 원문 앞 3줄 + "요약 중…", `skipped`·`failed`면 원문 전체.
    모두 텍스트로 렌더링(Markdown 아님), 원문의 URL만 링크로.
  - 하단: 요약이 있을 때만 `원문 보기` 토글(펼침 상태는 저장하지 않음), `LMS에서 열기 ↗` 링크.
  - 색은 `index.css` 토큰만. `docs/design/LmsNotice.dc.html`·`LmsNoticeDark.dc.html` 캔버스 추가.

## 설정

- `Settings.lms_summary_min_chars: int = 300`(env 기본값) 위에 `app_setting.lms_summary_min_chars`. "과목 이름 정리"와
  같은 방식으로 LMS 설정의 "알림 AI 요약" 토글(켜면 300, 끄면 0), `PUT /api/v1/lms/summary`. `LmsStatus`에
  `summary_min_chars`, `summary_available`(분류기 설정 여부).

## 오류 처리

- 요약기 없음·짧은 원문: `skipped`, 원문 표시.
- 호출 실패·타임아웃·파싱 실패: `failed`, 원문 표시, 로그 경고. 다음 동기화에 재시도.
- 워커 예외가 루프를 멈추지 않는다(항목 단위 try).

## 테스트

- backend: import가 notice를 만들고 길이로 `pending`/`skipped`가 갈림 / 원문 변경 시 다시 `pending`·summary 비움 /
  워커 성공 시 summary·body 갱신과 WS 이벤트 / 실패 시 `failed`와 다음 import에서 재시도 / 시작 시 `pending` 재처리 /
  처리 중 원문이 바뀐 결과는 버림 / `parse_bullets` / 마이그레이션 백필 파서(escape 되돌리기 포함) /
  `lms_summary_min_chars=0`이면 모두 `skipped`.
- frontend(vitest): 카드 상태별 렌더링(`done`/`pending`/`skipped`), 원문 보기 토글, 링크.
