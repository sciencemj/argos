# 칸반 마감을 캘린더에, 영역별 캘린더 (Phase 18 설계)

2026-10-22 사용자 요청. 앱 안 변경 내역(Phase 19)은 별도 spec.

## 목표

지금은 칸반 할 일의 마감이 CalDAV로 올라가지 않고 ICS 구독 피드에만 있어 폰 캘린더에 안 보이거나
늦게 보인다. 그래서 같은 일을 캘린더에도 따로 적게 되고, 결국 두 군데를 확인한다. 또 앱이 쓰는
캘린더가 `Argos` 하나라 과목·프로젝트가 섞인다.

성공 기준:

- 칸반에서 마감을 정한 할 일이 몇 초~몇 분 안에 폰 캘린더에 `마감 · 제목`으로 보인다.
- 완료하거나 마감을 지우거나 할 일을 지우면 캘린더에서도 사라진다.
- 앱에서 새로 만든 일정과 마감은 채널의 영역별 캘린더(`Argos · 학업` 등)에 들어간다.
- 두 번 동기화해도 중복이 없고, 올린 마감이 일정으로 다시 들어오지 않는다.
- 캘린더 이름 접두사·켜고 끄기는 설정이고, 영역 이름은 사용자 데이터에서 온다(코드에 박지 않음).

범위 밖: 폰에서 고친 마감을 칸반에 반영(양방향), 이미 `Argos` 캘린더에 있는 일정 옮기기(사용자 결정:
그대로 둠), 이미 양쪽에 겹쳐 있는 항목 찾아 합치기, iCloud 미리 알림(VTODO, 새 iCloud 미리 알림은 CalDAV로
읽고 쓸 수 없음). 앞의 둘은 `docs/backlog.md`.

## 캘린더 고르기

`write_target(channel)`: 일정·마감을 쓸 캘린더 이름.

- "영역별 캘린더로 나누기"가 켜져 있고 채널에 영역이 있으면 `{caldav_write_calendar} · {area.name}`.
- 아니면 `{caldav_write_calendar}`(기본 `Argos`, 지금과 같음).

동기화 시작 때 원격 캘린더 목록에서 이름으로 찾고 없으면 MKCALENDAR. 필요한 캘린더만 만든다(마감·새
일정이 없는 영역은 캘린더를 만들지 않음). Argos가 쓰는 캘린더 집합 = 위 규칙으로 나오는 이름들 +
`source_link`가 쓰기 가능(`read_only=False`)으로 가리키는 컨테이너. 읽기에서 `writable`은 이 집합으로
판단한다(지금은 `cal.url == target.url`).

영역 이름은 `/`, 줄바꿈 같은 문자가 있어도 캘린더 이름(displayname)일 뿐이라 그대로 쓴다. URL은 서버가
정한다(MKCALENDAR는 지금처럼 uuid 경로).

## 마감 올리기

대상: `due_at`이 있고 `status != done`이고 `_in_window`(90일 전~1년 후) 안인 할 일. 설정 "칸반 마감을
캘린더에 올리기"가 꺼져 있으면 대상 없음(이미 올린 것은 지움).

ICS: VEVENT, UID `task-<id>@argos`, SUMMARY `마감 · {title}`, DTSTART 마감 30분 전, DTEND 마감, TRANSP
TRANSPARENT, DESCRIPTION 채널 이름. ICS 피드의 마감과 같은 모양이라 `ics.py`의 마감 VEVENT 만드는 부분을
함수로 빼서 둘이 같이 쓴다.

`source_link`: `object_type="task"`, `source="caldav"`, `container`=캘린더 URL, `content_hash`=올린 내용
(제목·마감·채널 이름) 해시, `read_only=False`.

`_push_tasks` (sync 끝, `_push_new` 다음):

1. 대상 할 일마다 원하는 캘린더와 내용 해시를 계산.
2. 링크 없음 → PUT(새로), 링크 생성.
3. 링크 있음, 캘린더가 다름(채널의 영역이 바뀜·설정 변경) → 새 캘린더에 PUT, 옛 href DELETE, 링크 갱신.
4. 링크 있음, 해시 다름 → If-Match PUT. 412면 ETag 없이 다시 PUT(앱이 원본, 충돌 화면 없음).
5. 링크 있음, 원격에서 사라짐(목록에 없음 또는 캘린더가 지워짐) → 다시 PUT.
6. 대상이 아닌데 링크가 있음(완료·마감 삭제·할 일 삭제·범위 밖·설정 끔) → DELETE, 링크 삭제.

폰에서 마감 항목을 고치면 다음 동기화 때 원격 ETag가 바뀐 것을 보고 앱 내용으로 다시 쓴다. 지우면 다시
만든다(마감의 원본은 칸반).

읽기 쪽(`sync`의 캘린더 순회)에서 `object_type="task"` 링크가 가리키는 href는 일정으로 가져오지 않고
위 규칙(5번 판정용 "보임")에만 쓴다. `_settle_unseen`은 `object_type="event"` 링크만 다룬다.

## 새 일정

`_push_new`의 대상(링크 없음, `calendar_id` 없음)은 `write_target(event.channel)` 캘린더로 PUT한다.
이미 링크가 있는 일정(지금 `Argos` 캘린더의 것 포함)은 옮기지 않는다. 앱에서 일정의 채널을 다른 영역으로
바꿔도 이미 올린 일정은 원래 캘린더에 둔다(옮기기는 범위 밖, 위 사용자 결정과 일관).

## 영역 이름 바꾸기

영역 이름이 바뀌면 다음 동기화가 새 이름으로 캘린더를 못 찾아 새로 만들 것이다. 막으려고 앱 설정
`calendar_areas`(`{area_id: calendar_url}`)에 영역별로 만든 캘린더 URL을 기억하고, 그 URL이 원격 목록에
있으면 이름이 달라도 그 캘린더를 쓴다. 이름이 다르면 `CalendarServer.rename(url, name)`(PROPPATCH
displayname)로 바꾼다. 실패하면 로그만 남기고 계속(다음 동기화에서 다시 시도). 영역이 지워지면 그 채널들은
영역 없음 → `Argos`로 마감이 옮겨지고, 영역 캘린더는 비워진 채 남는다(지우기는 사용자가 폰에서).

## 즉시 반영

`main.py`의 `on_change`: `object_type`이 `task`, `channel`, `area`일 때도 `calendar_sync.nudge()`
(task는 볼트 nudge와 함께). 동기화 자신이 만든 변경은 지금처럼 실행 중이라 무시된다.

## ICS 피드

iCloud가 연결돼 있고(키체인에 암호) 마감 올리기가 켜져 있으면 `build_feed`가 마감을 넣지 않는다. 둘 다
구독한 사람에게 같은 마감이 두 번 보이지 않게(결정 2026-09-25 "동기화되는 일정은 피드에서 제외"와 같은 이유).

## 설정

`Settings`(env, 기본값)에 두지 않고 앱 설정(`app_setting`)으로: `calendar_push_tasks`(기본 true),
`calendar_split_areas`(기본 true). `ICloudOut`에 두 값을 싣고, 설정 화면 iCloud 섹션에 토글 두 개.
켜고 끄면 바로 nudge. `ICloudCalendarOut`에 영역 캘린더는 `writable=true`로 보인다.

## 오류

- MKCALENDAR·인증 실패: 지금처럼 `CalDAVError` → 동기화 상태 오류(상태바·설정).
- 마감 PUT/DELETE 하나가 실패(`CalDAVError`): 그 할 일만 건너뛰고 계속, 다음 동기화에서 다시. 결과에
  `failed` 개수.
- PROPPATCH 실패: 로그, 계속.

## 테스트 (`tests/test_caldav.py`, 가짜 `FakeServer`)

`FakeServer`에 `rename` 추가. 케이스:

- 마감 있는 할 일 → 영역 캘린더 생성, `마감 · 제목` VEVENT 하나, 두 번 동기화해도 하나.
- 제목·마감 변경 → 같은 href에 PUT. 완료·마감 삭제·할 일 삭제 → DELETE.
- 올린 마감이 다음 동기화에서 일정(Event)으로 생기지 않음.
- 폰에서 마감 항목 수정(ETag 바뀜) → 앱 내용으로 덮어씀. 폰에서 삭제 → 다시 생성.
- 채널의 영역 변경 → 새 캘린더에 PUT, 옛 것 DELETE.
- 영역 이름 변경 → 새 캘린더를 만들지 않고 rename.
- 영역 없는 채널 → `Argos`. 나누기 끔 → 모두 `Argos`. 마감 올리기 끔 → 올린 것 삭제.
- 새 일정 → 영역 캘린더. 기존 `Argos` 링크 일정은 그대로.
- 범위 밖 마감은 올리지 않음.
- ICS 피드: 연결·켬이면 마감 없음, 아니면 지금처럼.
- `on_change` nudge: task 변경 후 동기화가 돈다(기존 nudge 테스트 방식).

프런트: 설정 토글 렌더·뮤테이션 vitest 하나. `make api-types`.

## 문서

PLAN에 Phase 18(추가 범위, 2026-10-22 사용자 요청), `decisions.md`에 영역 캘린더 이름 규칙·마감 단방향·기존
일정 두기·피드 마감 제외 조건, `backlog.md`에 양방향 마감·기존 일정 옮기기.
