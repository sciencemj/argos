# 아침 요약 (Phase 15b 설계)

2026-10-12 사용자 요청. LMS 알림 요약(15a)에 이어서.

## 목표

매일 아침 Argos의 여러 정보를 모아 사용자가 고른 에이전트가 짧은 브리핑을 쓰고, #today 채널과 메신저로 보낸다.

성공 기준:

- 설정에서 켜면 매일 정해진 시각(기본 7시) 이후 첫 확인 때 #today에 "아침 요약" 스레드가 생기고, 고른 에이전트의
  답이 그 스레드에 실시간으로 쓰인다. 메신저 대상이 설정돼 있으면 같은 글이 메신저로 간다.
- 에이전트를 쓸 수 없거나 실패해도 그날 아침에는 규칙 기반 목록이 대신 온다.
- 하루 한 번만. 스레드에 답하면 같은 에이전트와 이어서 대화한다.
- 개인 환경 값(시각, 에이전트, 메신저)은 모두 설정. 기본은 꺼짐(에이전트 사용량을 쓰므로 사용자가 직접 켠다).

범위 밖: 날씨·외부 뉴스, 폰 푸시 알림, 저녁 회고, 요일별 다른 시각.

## 데이터 (`notify.morning_digest_data(session, now, settings) -> dict`)

`briefing()`을 확장한다. 모두 사용자 시간대 기준.

- `date`, `weekday`
- `events`: 오늘 일정(제목, 채널, 종일 여부, 시작·끝, 장소). 지금 `briefing()`과 같다.
- `due_tasks`: 지난 마감 + 오늘부터 `due_soon_days` 안의 마감(제목, 채널, 마감, `overdue`). 최대 15개.
- `routines`: 오늘 예정된 루틴(제목, 완료 여부).
- `open_inbox`: 열린 인박스 수.
- `lms`: 지난 24시간 안에 만들어진 `lms_notice`(채널 이름, 종류, 제목, 요약이 있으면 요약 아니면 원문 앞 200자)와
  LMS에서 온 새 과제(`source_link.source == "learningx"`, `object_type == "task"`, 24시간 안 생성: 제목, 채널, 마감).
  합쳐 최대 10개, 최신 순.
- `attention`:
  - `overlaps`: 오늘 시간이 겹치는 일정 쌍(종일 일정 제외) 제목 목록
  - `overdue`: 지난 마감 할 일 수
  - `undated`: 날짜 없는 열린 할 일 수(`roundup_candidates`와 같은 기준: `undated_after_days`보다 오래된 것)
  - `stale_inbox`: `inbox_stale_hours`보다 오래 정리 안 된 인박스 수

## 생성과 전달 (`notify.MorningDigest`)

1. 시작 조건: `morning_digest_enabled`이고, 현지 시각이 `morning_digest_hour` 이상 12시 미만이며, 오늘
   `Notification.key == "digest:YYYY-MM-DD"`가 없을 때. `Notifier.run()`이 매 확인마다 검사하고, 조건이 맞으면 알림 행을
   먼저 기록한 뒤(중복 방지) 생성을 별도 asyncio 태스크로 띄운다(Notifier 잠금 안에서 기다리지 않음).
2. #today에 시스템 메시지 "☀️ M월 D일(요일) 아침 요약"을 만든다(스레드 루트). 알림 행의 `object_type="message"`,
   `object_id`는 이 루트.
3. 에이전트: `morning_digest_agent`(이름, 비우면 `default_agent`). 찾을 수 없으면 6번(대체)으로.
   `services.start_run(kind="digest", reply_thread_root_id=루트)` 후 `runner.run_turn(..., no_tools=True,
   session_key="morning-YYYY-MM-DD")`. 루트 메시지의 `sticky_agent_id`를 그 에이전트로 두어 스레드 답글이 같은
   에이전트로 간다.
4. 프롬프트(언어 설정 ko/en): "아래 데이터만으로 오늘 아침 브리핑을 써라. 첫 줄은 오늘 가장 중요한 것 한 문장. 그다음
   `## 오늘`, `## 새 LMS 소식`, `## 챙길 것` 순서. 비어 있는 섹션은 빼라. 데이터에 없는 일정이나 마감은 지어내지 마라.
   전체 20줄 이내." 뒤에 데이터 JSON.
5. run이 `done`이면 답 본문을 메신저로 보낸다(`notify_hermes_target`이 있을 때, `hermes_send`, 제목 줄 `[Argos]`).
   전송 결과는 알림 행의 `sent_at`/`send_error`에.
6. 대체: 에이전트 없음, run `error`, 시간 초과(`agent_timeout`)면 `digest_markdown(data)`(규칙 기반 목록,
   `briefing_markdown` 확장)를 첫 줄 "AI 요약을 만들지 못했어요(이유)"와 함께 같은 스레드에 시스템 메시지로 쓰고,
   그 글을 메신저로 보낸다.
7. 알림 목록 제목: "아침 요약이 도착했어요"(en: "Your morning digest is ready").

"지금 보내기"(`POST /api/v1/notify/digest`)는 꺼져 있거나 이미 보낸 날이어도 바로 한 번 만든다. 알림 키는
`digest:YYYY-MM-DD:manual:<짧은 id>`라 그날 자동 요약을 막지 않는다.

## 설정

`app_setting`(`classifier.OVERRIDABLE`에 추가) 위의 `Settings` 필드:

- `morning_digest_enabled: bool = False`
- `morning_digest_hour: int = 7` (0~23)
- `morning_digest_agent: str = ""` (에이전트 이름, 빈 값이면 기본 에이전트)

`/settings/notify`의 In/Out 모델에 세 필드를 더한다. 설정 → 알림 섹션: "아침 요약" 토글, 시각 선택, 에이전트
선택(등록된 에이전트, 첫 항목 "기본 에이전트"), "지금 보내기" 버튼. 메신저 대상은 기존 설정을 같이 쓴다.

## 오류 처리

- 생성 태스크의 예외는 로그로 남기고, 루트 메시지가 있으면 대체 목록을 쓴다(아침에 항상 뭔가 온다).
- 메신저 실패는 알림 행에 기록(기존 `_deliver`와 같은 방식). 자동 재전송은 하지 않는다.
- 서버가 생성 중에 꺼지면 run은 다음 시작 때 error 처리(기존 `abandon_running_runs`). 그날은 다시 만들지 않는다.

## 테스트

- backend: FakeAgent로 #today 루트·에이전트 답 스레드·메신저 본문(가짜 send) / 에이전트 없음·실패 시 대체 목록이 같은
  스레드와 메신저로 / 하루 한 번, 시각 전·정오 이후·꺼짐이면 안 함 / 데이터: 24시간 안 LMS만·요약 우선·새 LMS 과제·
  겹치는 일정 쌍·지난 마감·날짜 없음·방치된 인박스·상한 / 프롬프트에 데이터 JSON과 언어 / 메신저 대상 없으면 안 보냄 /
  `sticky_agent_id` 설정 / 설정 API 저장·검증 / "지금 보내기"가 자동 요약을 막지 않음.
- frontend(vitest): 알림 설정의 토글·시각·에이전트 선택·지금 보내기.
