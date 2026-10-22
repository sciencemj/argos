# 칸반 마감을 캘린더에, 영역별 캘린더 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 칸반 할 일의 마감을 CalDAV로 iCloud에 `마감 · 제목` 일정으로 올리고, Argos가 쓰는 일정·마감을 영역별 캘린더(`Argos · 학업` 등)로 나눈다.

**Architecture:** 기존 `caldav_sync.sync`에 (1) 채널 → 쓰기 캘린더를 정하는 `Targets`(영역별 캘린더 생성·기억·이름 변경), (2) 앱이 원본인 단방향 `_push_tasks`를 더한다. 링크는 기존 `source_link`(`source="icloud"`, `object_type="task"`)를 쓴다. 읽기 루프는 마감 링크를 일정으로 가져오지 않는다.

**Tech Stack:** FastAPI, async SQLAlchemy, icalendar, httpx2, pytest(가짜 `FakeCalDAV`), React + TanStack Query, vitest.

**Spec:** `docs/superpowers/specs/2026-10-22-calendar-deadlines-design.md`

## Global Constraints

- 캘린더 이름: 영역 캘린더 `{caldav_write_calendar} · {area.name}`(가운뎃점 앞뒤 공백), 영역 없는 채널·나누기 끔 → `{caldav_write_calendar}`. 이름을 코드에 박지 않는다.
- 마감 VEVENT: UID `task-<id>@argos`, SUMMARY `마감 · {title}`, DTSTART 마감 30분 전, DTEND 마감, TRANSP TRANSPARENT. ICS 피드와 같은 함수로 만든다.
- 동기화 범위: 90일 전 ~ 1년 후(`PAST`, `FUTURE`).
- 마감은 앱 → 캘린더 단방향. 원격 수정·삭제는 다음 동기화에서 앱 내용으로 덮어쓰거나 다시 만든다. 충돌 화면 없음.
- 이미 링크된 일정(기존 `Argos` 캘린더 포함)은 옮기지 않는다.
- 설정 키(앱 설정): `calendar_push_tasks`(기본 true), `calendar_split_areas`(기본 true), `calendar_areas`(`{area_id: url}`, 조용히 저장).
- 새 쓰기는 `services` 헬퍼로. 동기화 actor는 `sync:icloud`.
- UI 문구는 `t(한국어, English)`/`tr`. 색은 토큰 클래스만.
- 명령은 worktree 루트 `/Users/sciencemj/dev/argos/.claude/worktrees/calendar-deadlines-changelog`에서. git은 `/usr/bin/git`으로(rtk 훅이 `git`을 가로채 worktree 격리에 막힘).
- API 모델을 바꾼 커밋은 `make api-types` 결과(`frontend/src/api-types.ts`)를 함께 커밋.

## Review Focus

- 링크를 잃은 마감 자원(UID `task-…@argos`, 링크 없음)이 영역 캘린더에 남아 있으면: 일정으로 가져오지 않고 지운 뒤 다시 올려야 한다(If-None-Match 412로 영영 실패하면 안 됨) → Task 3 `test_orphan_deadline_is_replaced_not_imported`.
- 인증 오류(`AuthError`)는 마감 하나의 실패로 삼키지 말고 동기화 전체 오류로 보여야 한다 → Task 3 `test_auth_error_while_pushing_fails_the_sync`.
- 사용자가 폰에서 영역 캘린더를 통째로 지우면: 다음 동기화가 같은 이름으로 다시 만들고 마감을 다시 올린다 → Task 3 `test_deleted_area_calendar_is_made_again`.
- 영역 이름 변경 PROPPATCH가 실패해도 동기화는 성공하고 기존 캘린더를 계속 쓴다(새 캘린더 안 만듦) → Task 2 `test_rename_failure_keeps_using_the_calendar`.
- 동기화 결과 `failed` 키가 생겨도 기존 "두 번 동기화해도 변화 없음" 기준이 유지된다 → Task 3에서 `test_second_sync_changes_nothing` 기대값 갱신 + 마감이 있는 상태로 두 번 동기화 테스트.

---

## File Structure

- Modify `backend/src/argos/ics.py`: `deadline_event()` 공용 함수로 분리, `build_feed(..., deadlines: bool = True)`.
- Modify `backend/src/argos/caldav_sync.py`: `render_deadline`, `deadline_digest`, `Options`/`options()`, `Targets`, `CalendarServer.rename` + `CalDAVClient.rename`, `sync` 읽기 루프 변경, `_push_new`가 `Targets` 사용, `_push_tasks`, `_settle_unseen` 필터, `SyncResult.failed/owned`, 목록 저장에 `writable`.
- Modify `backend/src/argos/api.py`: `ICloudOut.push_tasks/split_areas`, `CalendarOptionsIn`, `PUT /settings/icloud/options`, `_icloud_out` writable, `calendar_feed` 마감 제외 조건.
- Modify `backend/src/argos/main.py`: `on_change`가 task/channel/area에도 캘린더 nudge.
- Modify `backend/tests/test_caldav.py`: `FakeCalDAV.rename`, 기존 기대 갱신, 새 테스트.
- Modify `frontend/src/api.ts`: `useCalendarOptions`.
- Modify `frontend/src/pages/SettingsPage.tsx`: `ICloudSection` export, 토글 두 개, 설명 문구, `RESULT_TEXT`에 `failed`.
- Create `frontend/src/pages/ICloudSection.test.tsx`.
- Modify `frontend/src/api-types.ts` (생성).
- Modify `docs/PLAN.md`, `docs/decisions.md`, `docs/backlog.md`, `CLAUDE.md`.

---

### Task 1: 마감 VEVENT를 피드와 CalDAV가 함께 쓰기

**Files:**
- Modify: `backend/src/argos/ics.py:84-93`
- Modify: `backend/src/argos/caldav_sync.py` (render 아래)
- Test: `backend/tests/test_caldav.py` (파일 끝)

**Interfaces:**
- Produces: `ics.deadline_event(task: Task, channel: str | None, now: datetime) -> VEvent`, `caldav_sync.render_deadline(task: Task, channel: str | None, now: datetime) -> bytes`, `caldav_sync.deadline_digest(task: Task, channel: str | None) -> str`, `caldav_sync.DEADLINE_UID: re.Pattern[str]`.

- [ ] **Step 1: Write the failing test** (`test_caldav.py` 끝, import에 `from icalendar import Calendar`와 `from argos.models import Task` 추가)

```python
def test_deadline_resource_matches_the_feed() -> None:
    task = Task(
        id="t1",
        channel_id="c1",
        title="보고서",
        position=1.0,
        due_at=datetime(2026, 10, 23, 14, 59, tzinfo=UTC),
    )
    task.updated_at = datetime(2026, 10, 1, tzinfo=UTC)
    body = caldav_sync.render_deadline(task, "운영체제", datetime(2026, 10, 22, tzinfo=UTC))
    [item] = Calendar.from_ical(body).events
    assert str(item["UID"]) == "task-t1@argos"
    assert caldav_sync.DEADLINE_UID.fullmatch(str(item["UID"]))
    assert str(item["SUMMARY"]) == "마감 · 보고서"
    assert item.DTSTART == datetime(2026, 10, 23, 14, 29, tzinfo=UTC)
    assert item.end == datetime(2026, 10, 23, 14, 59, tzinfo=UTC)
    assert str(item["TRANSP"]) == "TRANSPARENT"
    same = caldav_sync.deadline_digest(task, "운영체제")
    assert same == caldav_sync.deadline_digest(task, "운영체제")
    assert same != caldav_sync.deadline_digest(task, "인공지능")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_caldav.py::test_deadline_resource_matches_the_feed -q`
Expected: FAIL `AttributeError: module 'argos.caldav_sync' has no attribute 'render_deadline'`

- [ ] **Step 3: Implement**

`ics.py`: 마감 루프를 함수로.

```python
    for task in tasks:
        cal.add_component(deadline_event(task, channels.get(task.channel_id), now))

    return cal.to_ical()


def deadline_event(task: Task, channel: str | None, now: datetime) -> VEvent:
    """A task's deadline as the half hour before it, shown as free time. The feed and
    the CalDAV sync (Phase 18) both use it, so a deadline looks the same either way."""
    assert task.due_at is not None
    item = _base(f"task-{task.id}", f"마감 · {task.title}", task.updated_at, now)
    item.add("dtstart", task.due_at - DEADLINE_LENGTH)
    item.add("dtend", task.due_at)
    item.add("transp", "TRANSPARENT")  # a deadline does not make the user busy
    if task.description:
        item.add("description", task.description)
    _channel(item, channel)
    return item
```

`caldav_sync.py`: import에 `import re`, `from argos import ics, services`, `from argos.models import Area, Channel, Event, SourceLink, Task, TaskStatus` (Area·Channel·TaskStatus는 Task 2·3에서 씀). `render` 아래에:

```python
DEADLINE_UID = re.compile(r"task-.+@argos")


def render_deadline(task: Task, channel: str | None, now: datetime) -> bytes:
    cal = Calendar()
    cal.add("prodid", "-//Argos//Argos//KO")
    cal.add("version", "2.0")
    cal.add_component(ics.deadline_event(task, channel, now))
    return cal.to_ical()


def deadline_digest(task: Task, channel: str | None) -> str:
    """What a pushed deadline shows; a change means the resource must be rewritten."""
    data = {
        "title": task.title,
        "due_at": task.due_at.isoformat() if task.due_at else None,
        "description": task.description,
        "channel": channel,
    }
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
```

- [ ] **Step 4: Run tests**

Run: `cd backend && uv run pytest tests/test_caldav.py tests/test_ics.py -q` (test_ics.py가 없으면 `-k feed`로 피드 테스트 포함)
Expected: PASS

- [ ] **Step 5: Commit**

```bash
/usr/bin/git add backend/src/argos/ics.py backend/src/argos/caldav_sync.py backend/tests/test_caldav.py
/usr/bin/git commit -m "refactor: share the deadline event between feed and CalDAV"
```

---

### Task 2: 영역별 쓰기 캘린더 (새 일정) + 옵션 API

**Files:**
- Modify: `backend/src/argos/caldav_sync.py` (`CalendarServer`, `CalDAVClient`, 새 `Options`/`Targets`, `sync`, `_push_new`, `SyncResult`, `CalendarSync.run`)
- Modify: `backend/src/argos/api.py:1465-1546` (`ICloudOut`, `_icloud_out`, 새 엔드포인트)
- Modify: `frontend/src/api-types.ts` (생성)
- Test: `backend/tests/test_caldav.py`

**Interfaces:**
- Consumes: Task 1 imports.
- Produces:
  - `CalendarServer.rename(self, url: str, name: str) -> None`
  - `caldav_sync.PUSH_TASKS = "calendar_push_tasks"`, `SPLIT_AREAS = "calendar_split_areas"`, `CALENDAR_AREAS = "calendar_areas"`
  - `@dataclass class Options: push_tasks: bool = True; split_areas: bool = True`, `async def options(session) -> Options`
  - `class Targets` with `owned: set[str]` (property), `async def for_channel(channel_id: str) -> RemoteCalendar`
  - `SyncResult.failed: int = 0`, `SyncResult.owned: list[str]`
  - API `PUT /api/v1/settings/icloud/options` body `CalendarOptionsIn{push_tasks: bool, split_areas: bool}` → `ICloudOut` (새 필드 `push_tasks`, `split_areas`)

- [ ] **Step 1: Fake에 rename, 헬퍼 추가** (`FakeCalDAV` 안, `delete` 아래)

```python
    async def rename(self, url: str, name: str) -> None:
        self.requests.append(("PROPPATCH", url, name))
        self.calendars_[url] = RemoteCalendar(url, name)
```

테스트 헬퍼(`channel_named` 아래):

```python
def area_named(client: TestClient, name: str) -> str:
    return next(a["id"] for a in client.get("/api/v1/areas").json() if a["name"] == name)


def set_options(client: TestClient, *, push_tasks: bool = True, split_areas: bool = True) -> None:
    response = client.put(
        "/api/v1/settings/icloud/options",
        json={"push_tasks": push_tasks, "split_areas": split_areas},
    )
    assert response.status_code == 200, response.text
```

(`GET /api/v1/areas` 응답 형식을 먼저 확인: `grep -n '@router.get("/areas' backend/src/argos/api.py`. 목록이 `{"areas": [...]}`이면 `.json()["areas"]`로.)

- [ ] **Step 2: 기존 테스트를 새 동작으로 고치고 새 테스트 작성**

`test_first_sync_reads_every_calendar_and_writes_only_to_argos` → 이름을 `test_first_sync_reads_every_calendar_and_writes_only_to_argos_calendars`로, 기대 변경:

```python
    assert {c["name"] for c in status["calendars"]} == {"개인", "학교", "Argos", "Argos · 학업"}
    assert ("MKCALENDAR", "Argos", None) in server.requests
    assert ("MKCALENDAR", "Argos · 학업", None) in server.requests
    ...
    assert (shown["스터디"]["source"], shown["스터디"]["read_only"]) == ("Argos · 학업", False)

    [pushed] = server.in_calendar("Argos · 학업").values()
    assert f"UID:event-{ours['id']}@argos".encode() in pushed
    assert b"SUMMARY:\xec\x8a\xa4\xed\x84\xb0\xeb\x94\x94" in pushed  # 스터디
    assert server.in_calendar("개인") and all(
        method != "PUT" or href.startswith(f"{HOME}argos") for method, href, _ in server.requests
    )
    writable = {c["name"] for c in status["calendars"] if c["writable"]}
    assert writable == {"Argos", "Argos · 학업"}
```

`test_argos_changes_go_to_the_argos_calendar_only`: `h.startswith(f"{HOME}argos/")` → `h.startswith(f"{HOME}argos · 학업/")`.
`test_argos_changes_sync_within_seconds`: `in_calendar("Argos")` 두 곳 → `in_calendar("Argos · 학업")`. (`wait_until(lambda: server.in_calendar("Argos · 학업") == {})` 전에 캘린더가 없으면 `StopIteration`이므로 첫 `wait_until`이 캘린더 생성을 보장한다.)

새 테스트:

```python
def test_channels_without_an_area_write_to_argos(app: TestClient, server: FakeCalDAV) -> None:
    connect(app)
    app.post(
        "/api/v1/events",
        json={"channel_id": channel_named(app, "일상"), "title": "장보기", "start_date": "2026-10-24"},
    )
    assert sync(app)["pushed"] == 1
    assert len(server.in_calendar("Argos")) == 1
    assert not any(c.name.startswith("Argos ·") for c in server.calendars_.values())


def test_split_off_writes_everything_to_argos(app: TestClient, server: FakeCalDAV) -> None:
    connect(app)
    set_options(app, split_areas=False)
    app.post(
        "/api/v1/events",
        json={"channel_id": channel_named(app, "운영체제"), "title": "퀴즈", "start_date": "2026-10-24"},
    )
    sync(app)
    assert len(server.in_calendar("Argos")) == 1
    assert app.get("/api/v1/settings/icloud").json()["split_areas"] is False


def test_events_already_in_argos_stay_there(app: TestClient, server: FakeCalDAV) -> None:
    """User decision 2026-10-22: existing Argos-calendar events are not moved."""
    connect(app)
    set_options(app, split_areas=False)
    app.post(
        "/api/v1/events",
        json={"channel_id": channel_named(app, "운영체제"), "title": "퀴즈", "start_date": "2026-10-24"},
    )
    sync(app)
    set_options(app, split_areas=True)
    before = len(server.requests)
    assert sync(app)["pushed"] == 0
    assert len(server.in_calendar("Argos")) == 1
    assert not [r for r in server.requests[before:] if r[0] in ("PUT", "DELETE")]


def test_area_rename_renames_its_calendar(app: TestClient, server: FakeCalDAV) -> None:
    connect(app)
    app.post(
        "/api/v1/events",
        json={"channel_id": channel_named(app, "운영체제"), "title": "퀴즈", "start_date": "2026-10-24"},
    )
    sync(app)
    app.patch(f"/api/v1/areas/{area_named(app, '학업')}", json={"name": "공부"})
    app.post(
        "/api/v1/events",
        json={"channel_id": channel_named(app, "운영체제"), "title": "발표", "start_date": "2026-10-25"},
    )
    sync(app)
    assert len(server.in_calendar("Argos · 공부")) == 2
    assert [r for r in server.requests if r[0] == "MKCALENDAR" and "공부" in r[1]] == []
    assert any(r[0] == "PROPPATCH" and r[2] == "Argos · 공부" for r in server.requests)


def test_rename_failure_keeps_using_the_calendar(app: TestClient, server: FakeCalDAV) -> None:
    connect(app)
    app.post(
        "/api/v1/events",
        json={"channel_id": channel_named(app, "운영체제"), "title": "퀴즈", "start_date": "2026-10-24"},
    )
    sync(app)

    async def refuse(url: str, name: str) -> None:
        raise caldav_sync.CalDAVError("iCloud가 요청을 거절했어요 (PROPPATCH 403)")

    server.rename = refuse  # type: ignore[method-assign]
    app.patch(f"/api/v1/areas/{area_named(app, '학업')}", json={"name": "공부"})
    app.post(
        "/api/v1/events",
        json={"channel_id": channel_named(app, "운영체제"), "title": "발표", "start_date": "2026-10-25"},
    )
    sync(app)  # asserts no last_error
    assert len(server.in_calendar("Argos · 학업")) == 2
    assert "Argos · 공부" not in {c.name for c in server.calendars_.values()}
```

CalDAV 클라이언트 테스트(`test_client_writes_only_what_it_saw` 아래). `canned`에 PROPPATCH 응답 추가: `if method == "PROPPATCH": return multistatus((url, "<d:displayname/>"))`.

```python
async def test_client_renames_a_calendar(dav: CalDAVClient, seen: list[httpx2.Request]) -> None:
    await dav.rename(f"{HOME}home/", "Argos · <연구> & 실험")
    assert seen[-1].method == "PROPPATCH"
    assert "<d:displayname>Argos · &lt;연구&gt; &amp; 실험</d:displayname>".encode() in seen[-1].content
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_caldav.py -q`
Expected: 새 테스트와 고친 기존 테스트 FAIL (404 on `/settings/icloud/options`, `Argos · 학업` 없음, `rename` 없음).

- [ ] **Step 4: Implement**

`caldav_sync.py`:

```python
class CalendarServer(Protocol):
    ...
    async def delete(self, href: str, etag: str | None) -> None: ...
    async def rename(self, url: str, name: str) -> None: ...
```

`CalDAVClient` (`create_calendar`의 이스케이프를 함수로 빼서 같이 씀):

```python
def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
```

`create_calendar`의 `escaped = ...` 줄을 `escaped = _escape(name)`로. 그리고:

```python
    async def rename(self, url: str, name: str) -> None:
        body = _xml(
            '<d:propertyupdate xmlns:d="DAV:"><d:set><d:prop>'
            f"<d:displayname>{_escape(name)}</d:displayname>"
            "</d:prop></d:set></d:propertyupdate>"
        )
        await self._send("PROPPATCH", url, body, {"Content-Type": "application/xml; charset=utf-8"})
```

설정 키와 옵션(파일 아래 `CALENDAR_CHANNELS` 옆으로 옮기지 말고 sync 절 위에 둔다, `sync`가 쓰므로):

```python
PUSH_TASKS = "calendar_push_tasks"
SPLIT_AREAS = "calendar_split_areas"
CALENDAR_AREAS = "calendar_areas"  # {area id: url} of the area calendars Argos made


@dataclass
class Options:
    push_tasks: bool = True  # kanban deadlines go to the calendar (Phase 18)
    split_areas: bool = True  # one "<Argos> · <area>" calendar per area


async def options(session: AsyncSession) -> Options:
    push = await services.get_setting(session, PUSH_TASKS)
    split = await services.get_setting(session, SPLIT_AREAS)
    return Options(push_tasks=push is not False, split_areas=split is not False)


class Targets:
    """The calendars Argos writes to: the Argos calendar and, split by area, one
    "<Argos> · <area>" calendar per area, made the first time something goes there.
    An area calendar is remembered by URL, so renaming the area renames it."""

    def __init__(
        self,
        session: AsyncSession,
        server: CalendarServer,
        base: RemoteCalendar,
        calendars: list[RemoteCalendar],
        *,
        split: bool,
        remembered: dict[str, str],
    ) -> None:
        self._session = session
        self._server = server
        self.base = base
        self.calendars = calendars
        self._split = split
        self._remembered = remembered
        self._areas: dict[str, RemoteCalendar] = {}
        self._channel_areas: dict[str, str | None] = {}

    @property
    def owned(self) -> set[str]:
        return {self.base.url, *self._remembered.values()}

    async def for_channel(self, channel_id: str) -> RemoteCalendar:
        if not self._split:
            return self.base
        if channel_id not in self._channel_areas:
            channel = await self._session.get(Channel, channel_id)
            self._channel_areas[channel_id] = channel.area_id if channel else None
        area_id = self._channel_areas[channel_id]
        if area_id is None:
            return self.base
        if area_id not in self._areas:
            area = await self._session.get(Area, area_id)
            if area is None:
                return self.base
            self._areas[area_id] = await self._area_calendar(area)
        return self._areas[area_id]

    async def _area_calendar(self, area: Area) -> RemoteCalendar:
        name = f"{self.base.name} · {area.name}"
        cal = next((c for c in self.calendars if c.url == self._remembered.get(area.id)), None)
        if cal is not None and cal.name != name:
            try:
                await self._server.rename(cal.url, name)
            except AuthError:
                raise
            except CalDAVError:
                log.warning("could not rename calendar to %r; trying again next sync", name)
            else:
                renamed = RemoteCalendar(cal.url, name)
                self.calendars[:] = [renamed if c.url == cal.url else c for c in self.calendars]
                cal = renamed
        if cal is None:
            cal = next((c for c in self.calendars if c.name == name), None)
        if cal is None:
            cal = await self._server.create_calendar(name)
            self.calendars.append(cal)
        if self._remembered.get(area.id) != cal.url:
            self._remembered[area.id] = cal.url
            await services.set_setting_quietly(self._session, CALENDAR_AREAS, dict(self._remembered))
        return cal
```

`SyncResult`:

```python
@dataclass
class SyncResult:
    calendars: list[RemoteCalendar] = field(default_factory=list)
    owned: list[str] = field(default_factory=list)  # URLs of the calendars Argos writes
    created: int = 0  # new in Argos
    updated: int = 0  # changed in Argos from the calendar
    pushed: int = 0  # written to an Argos calendar
    deleted: int = 0  # removed on either side
    conflicts: int = 0
    failed: int = 0  # deadlines that could not be written; tried again next sync
```

`sync` 시작부 (target을 찾은 다음):

```python
    result.calendars = calendars
    opts = await options(session)
    remembered: dict[str, str] = dict(await services.get_setting(session, CALENDAR_AREAS) or {})
    targets = Targets(
        session, server, target, calendars, split=opts.split_areas, remembered=remembered
    )
    owned = targets.owned
    start, end = now - PAST, now + FUTURE
    seen: set[str] = set()

    for cal in list(calendars):
        writable = cal.url in owned
```

끝부분:

```python
    await _settle_unseen(session, server, seen, start, end, result)
    await _push_new(session, server, targets, now, start, end, result)
    result.owned = sorted(targets.owned)
    return result
```

`_push_new` 시그니처 `target: RemoteCalendar` → `targets: Targets`, 루프 안:

```python
        cal = await targets.for_channel(event.channel_id)
        uid = f"event-{event.id}@argos"
        ours = Fields.of(event)
        href, etag = await server.put(cal.url, uid, render(uid, ours, now), None, None)
        session.add(
            SourceLink(
                ...
                container=cal.url,
                container_name=cal.name,
```

독스트링: `"""Events made in Argos (no link, no source calendar) go to their channel's Argos calendar."""`

`CalendarSync.run`:

```python
                    await services.set_setting_quietly(
                        session,
                        ICLOUD_CALENDARS,
                        [
                            {"url": c.url, "name": c.name, "writable": c.url in result.owned}
                            for c in result.calendars
                        ],
                    )
                counts = asdict(result)
                counts.pop("calendars")
                counts.pop("owned")
```

모듈 독스트링 첫 단락을 갱신: "Every calendar is read into Argos events; Argos writes only to its own calendars — "Argos" and, split by area, "Argos · <area>" (Phase 18) — where events made in Argos and kanban deadlines go."

`api.py`:

```python
class ICloudOut(BaseModel):
    ...
    write_calendar: str
    push_tasks: bool  # kanban deadlines go to the calendar
    split_areas: bool  # one calendar per area
    poll_minutes: int
    ...


class CalendarOptionsIn(BaseModel):
    push_tasks: bool
    split_areas: bool
```

`_icloud_out`:

```python
    opts = await caldav_sync.options(session)
    return ICloudOut(
        username=username,
        connected=creds is not None,
        write_calendar=config.caldav_write_calendar,
        push_tasks=opts.push_tasks,
        split_areas=opts.split_areas,
        poll_minutes=config.caldav_poll_minutes,
        calendars=[
            ICloudCalendarOut(
                url=c["url"],
                name=c["name"],
                channel_id=mapping.get(c["url"]),
                writable=c.get("writable", c["name"] == config.caldav_write_calendar),
            )
            for c in listed
        ],
```

(`listed` 타입을 `list[dict[str, Any]]`로.) `set_calendar_channel` 아래:

```python
@router.put("/settings/icloud/options")
async def set_calendar_options(
    request: Request, session: Session, config: Config, body: CalendarOptionsIn
) -> ICloudOut:
    """Deadlines to the calendar, one calendar per area (Phase 18); applied right away."""
    await services.set_setting(session, caldav_sync.PUSH_TASKS, body.push_tasks, USER)
    await services.set_setting(session, caldav_sync.SPLIT_AREAS, body.split_areas, USER)
    _calendar_sync(request).nudge()
    return await _icloud_out(request, session, config)
```

- [ ] **Step 5: Run tests**

Run: `cd backend && uv run pytest tests/test_caldav.py -q`
Expected: PASS. `test_second_sync_changes_nothing`가 `failed` 키 때문에 실패하면 기대 dict에 `"failed": 0`을 넣는다.

- [ ] **Step 6: Lint, types**

Run: `cd backend && uv run ruff check src tests && uv run ruff format src tests && uv run pyright` 그다음 루트에서 `make api-types`
Expected: 오류 없음, `frontend/src/api-types.ts`에 `CalendarOptionsIn`, `push_tasks` 생김.

- [ ] **Step 7: Commit**

```bash
/usr/bin/git add backend/src/argos/caldav_sync.py backend/src/argos/api.py backend/tests/test_caldav.py frontend/src/api-types.ts
/usr/bin/git commit -m "feat: write new events to per-area calendars"
```

---

### Task 3: 칸반 마감 올리기

**Files:**
- Modify: `backend/src/argos/caldav_sync.py` (`sync` 읽기 루프, `_settle_unseen`, 새 `_push_tasks`)
- Test: `backend/tests/test_caldav.py`

**Interfaces:**
- Consumes: `render_deadline`, `deadline_digest`, `DEADLINE_UID` (Task 1), `Targets.for_channel`, `options`, `SyncResult.failed` (Task 2).
- Produces: `async def _push_tasks(session, server, targets: Targets, deadlines: dict[str, str | None], orphans: list[tuple[str, str | None]], *, enabled: bool, now, start, end, result) -> None`

- [ ] **Step 1: Write failing tests**

헬퍼:

```python
def task_on(client: TestClient, channel: str, title: str, due: str, **extra: Any) -> dict[str, Any]:
    response = client.post(
        "/api/v1/tasks",
        json={"channel_id": channel_named(client, channel), "title": title, "due_at": due, **extra},
    )
    assert response.status_code == 201, response.text
    return response.json()


def deadlines_in(server: FakeCalDAV, calendar: str) -> list[bytes]:
    return [ics for ics in server.in_calendar(calendar).values() if b"UID:task-" in ics]
```

테스트:

```python
def test_kanban_deadline_reaches_the_area_calendar_once(app: TestClient, server: FakeCalDAV) -> None:
    connect(app)
    task = task_on(app, "운영체제", "과제 3", "2026-10-30T14:59:00Z")
    assert sync(app)["pushed"] == 1
    [body] = deadlines_in(server, "Argos · 학업")
    assert f"UID:task-{task['id']}@argos".encode() in body
    assert "마감 · 과제 3".encode() in body
    assert sync(app)["pushed"] == 0  # twice: no duplicate
    assert len(deadlines_in(server, "Argos · 학업")) == 1
    assert events(app) == []  # a pushed deadline never comes back as an event


def test_deadline_follows_the_task(app: TestClient, server: FakeCalDAV) -> None:
    connect(app)
    task = task_on(app, "운영체제", "과제 3", "2026-10-30T14:59:00Z")
    sync(app)
    [href] = [h for h, (_, _, ics) in server.resources.items() if b"UID:task-" in ics]

    app.patch(f"/api/v1/tasks/{task['id']}", json={"title": "과제 3 (연장)", "due_at": "2026-11-02T14:59:00Z"})
    assert sync(app)["pushed"] == 1
    assert "과제 3 (연장)".encode() in server.resources[href][2]

    app.post(f"/api/v1/tasks/{task['id']}/move", json={"status": "done"})
    assert sync(app)["deleted"] == 1
    assert href not in server.resources


def test_removing_the_due_date_or_task_removes_the_deadline(app: TestClient, server: FakeCalDAV) -> None:
    connect(app)
    first = task_on(app, "운영체제", "읽기", "2026-10-28T14:59:00Z")
    second = task_on(app, "운영체제", "퀴즈", "2026-10-29T14:59:00Z")
    sync(app)
    app.patch(f"/api/v1/tasks/{first['id']}", json={"due_at": None})
    app.delete(f"/api/v1/tasks/{second['id']}")
    assert sync(app)["deleted"] == 2
    assert deadlines_in(server, "Argos · 학업") == []


def test_phone_edits_to_a_deadline_are_overwritten(app: TestClient, server: FakeCalDAV) -> None:
    connect(app)
    task_on(app, "운영체제", "과제 3", "2026-10-30T14:59:00Z")
    sync(app)
    [href] = [h for h, (_, _, ics) in server.resources.items() if b"UID:task-" in ics]
    cal, _, body = server.resources[href]
    server.resources[href] = (cal, '"phone"', body.replace("과제 3".encode(), "바꿈".encode()))
    assert sync(app)["pushed"] == 1
    assert "마감 · 과제 3".encode() in server.resources[href][2]

    del server.resources[href]  # deleted on the phone
    assert sync(app)["pushed"] == 1
    assert len(deadlines_in(server, "Argos · 학업")) == 1


def test_deleted_area_calendar_is_made_again(app: TestClient, server: FakeCalDAV) -> None:
    connect(app)
    task_on(app, "운영체제", "과제 3", "2026-10-30T14:59:00Z")
    sync(app)
    [url] = [u for u, c in server.calendars_.items() if c.name == "Argos · 학업"]
    del server.calendars_[url]
    for href in [h for h, (c, _, _) in server.resources.items() if c == url]:
        del server.resources[href]
    sync(app)
    assert len(deadlines_in(server, "Argos · 학업")) == 1


def test_moving_a_channel_to_another_area_moves_its_deadlines(app: TestClient, server: FakeCalDAV) -> None:
    connect(app)
    task_on(app, "운영체제", "과제 3", "2026-10-30T14:59:00Z")
    sync(app)
    app.patch(f"/api/v1/channels/{channel_named(app, '운영체제')}", json={"area_id": area_named(app, "프로젝트")})
    sync(app)
    assert deadlines_in(server, "Argos · 학업") == []
    assert len(deadlines_in(server, "Argos · 프로젝트")) == 1


def test_deadlines_outside_the_window_or_switched_off_are_not_pushed(app: TestClient, server: FakeCalDAV) -> None:
    connect(app)
    task_on(app, "운영체제", "먼 일", "2028-01-01T00:00:00Z")
    task_on(app, "운영체제", "가까운 일", "2026-10-30T14:59:00Z")
    sync(app)
    assert len(deadlines_in(server, "Argos · 학업")) == 1
    set_options(app, push_tasks=False)
    sync(app)
    assert deadlines_in(server, "Argos · 학업") == []


def test_orphan_deadline_is_replaced_not_imported(app: TestClient, server: FakeCalDAV) -> None:
    connect(app)
    task = task_on(app, "운영체제", "과제 3", "2026-10-30T14:59:00Z")
    sync(app)
    [href] = [h for h, (_, _, ics) in server.resources.items() if b"UID:task-" in ics]
    # The link is lost (e.g. a restored backup); the resource is still on the phone.
    import asyncio
    from sqlalchemy import delete
    from argos.models import SourceLink

    async def drop() -> None:
        async with app.app.state.sessionmaker() as session:  # type: ignore[attr-defined]
            await session.execute(delete(SourceLink).where(SourceLink.object_id == task["id"]))
            await session.commit()

    asyncio.run(drop())
    assert sync(app)["created"] == 0
    assert events(app) == []
    assert len(deadlines_in(server, "Argos · 학업")) == 1


def test_auth_error_while_pushing_fails_the_sync(app: TestClient, server: FakeCalDAV) -> None:
    connect(app)
    task_on(app, "운영체제", "과제 3", "2026-10-30T14:59:00Z")

    async def refuse(*_: Any) -> tuple[str, str | None]:
        raise AuthError("애플 ID나 앱 전용 암호가 맞지 않아요")

    server.put = refuse  # type: ignore[method-assign]
    status = app.post("/api/v1/calendar/sync").json()["status"]
    assert status["last_error"] == "애플 ID나 앱 전용 암호가 맞지 않아요"


def test_one_failed_deadline_does_not_stop_the_rest(app: TestClient, server: FakeCalDAV) -> None:
    connect(app)
    task_on(app, "운영체제", "실패", "2026-10-30T14:59:00Z")
    task_on(app, "운영체제", "성공", "2026-10-31T14:59:00Z")
    real = server.put

    async def flaky(calendar_url: str, uid: str, ics: bytes, href: str | None, etag: str | None) -> tuple[str, str | None]:
        if "실패".encode() in ics:
            raise caldav_sync.CalDAVError("iCloud가 요청을 거절했어요 (PUT 500)")
        return await real(calendar_url, uid, ics, href, etag)

    server.put = flaky  # type: ignore[method-assign]
    result = sync(app)
    assert (result["pushed"], result["failed"]) == (1, 1)
```

(`asyncio.run`이 TestClient 이벤트 루프와 충돌하면 대신 `app.portal.call(drop)`을 쓴다. `TestClient`는 `portal`을 `with` 블록 안에서 노출한다. sessionmaker 이름은 `grep -n "sessionmaker" backend/src/argos/main.py`로 확인.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_caldav.py -q -k "deadline or kanban or orphan or auth_error_while or failed_deadline"`
Expected: FAIL (마감이 안 올라감).

- [ ] **Step 3: Implement**

`sync` 읽기 루프 (`seen` 옆에 상태 둘 추가):

```python
    seen: set[str] = set()
    deadlines: dict[str, str | None] = {}  # href → ETag of the pushed deadlines listed
    orphans: list[tuple[str, str | None]] = []  # our deadlines whose link is gone

    for cal in list(calendars):
        writable = cal.url in owned
        for item in await server.items(cal.url, start, end):
            seen.add(item.href)
            parsed = parse(item.ics, tz)
            if parsed is None:
                continue
            uid, remote = parsed
            link = await _link_for(session, item.href)
            if link is not None and link.object_type == "task":
                deadlines[item.href] = item.etag
                continue
            if link is None and writable and DEADLINE_UID.fullmatch(uid):
                orphans.append((item.href, item.etag))  # never an event; pushed again below
                continue
            if link is None:
                ...
```

끝부분:

```python
    await _settle_unseen(session, server, seen, start, end, result)
    await _push_new(session, server, targets, now, start, end, result)
    await _push_tasks(
        session, server, targets, deadlines, orphans,
        enabled=opts.push_tasks, now=now, start=start, end=end, result=result,
    )
    result.owned = sorted(targets.owned)
    return result
```

`_settle_unseen`의 쿼리: `select(SourceLink).where(SourceLink.source == SOURCE, SourceLink.object_type == "event")`.

`_push_new` 아래에:

```python
async def _push_tasks(
    session: AsyncSession,
    server: CalendarServer,
    targets: Targets,
    deadlines: dict[str, str | None],
    orphans: list[tuple[str, str | None]],
    *,
    enabled: bool,
    now: datetime,
    start: datetime,
    end: datetime,
    result: SyncResult,
) -> None:
    """Kanban deadlines, one way (Phase 18): the kanban is the source, so a deadline
    changed or deleted on the phone is written again, and one whose task is done,
    undated, deleted or out of the window is removed."""
    for href, etag in orphans:
        await server.delete(href, etag)
    tasks = (
        await session.scalars(
            select(Task).where(
                Task.due_at.is_not(None),
                Task.due_at >= start,
                Task.due_at < end,
                Task.status != TaskStatus.DONE,
            )
        )
        if enabled
        else []
    )
    wanted = {task.id: task for task in tasks}
    links = {
        link.object_id: link
        for link in (
            await session.scalars(
                select(SourceLink).where(
                    SourceLink.source == SOURCE, SourceLink.object_type == "task"
                )
            )
        ).all()
    }
    names = {c.id: c.name for c in (await session.scalars(select(Channel))).all()}

    for task_id, link in links.items():
        if task_id in wanted:
            continue
        try:
            await server.delete(link.external_id, deadlines.get(link.external_id))
        except AuthError:
            raise
        except CalDAVError:
            result.failed += 1
            continue
        await session.delete(link)
        await session.commit()
        result.deleted += 1

    for task in wanted.values():
        try:
            await _push_task(session, server, targets, task, links.get(task.id), deadlines, names, now)
        except AuthError:
            raise
        except CalDAVError:
            await session.rollback()
            result.failed += 1
            continue
        if session.dirty or session.new:
            result.pushed += 1
        await session.commit()


async def _push_task(
    session: AsyncSession,
    server: CalendarServer,
    targets: Targets,
    task: Task,
    link: SourceLink | None,
    deadlines: dict[str, str | None],
    names: dict[str, str],
    now: datetime,
) -> None:
    """Leaves the session dirty only when something was written."""
    cal = await targets.for_channel(task.channel_id)
    channel = names.get(task.channel_id)
    digest = deadline_digest(task, channel)
    uid = f"task-{task.id}@argos"
    body = render_deadline(task, channel, now)
    if link is None:
        href, etag = await server.put(cal.url, uid, body, None, None)
        session.add(
            SourceLink(
                object_type="task",
                object_id=task.id,
                source=SOURCE,
                external_id=href,
                container=cal.url,
                container_name=cal.name,
                uid=uid,
                etag=etag,
                content_hash=digest,
                read_only=False,
                local_updated_at=task.updated_at,
                last_synced_at=now,
            )
        )
        return
    listed = link.external_id in deadlines
    if link.container != cal.url:  # the channel moved to another area
        href, etag = await server.put(cal.url, uid, body, None, None)
        if listed:
            await server.delete(link.external_id, deadlines[link.external_id])
    elif not listed:  # deleted on the phone, or the whole calendar was
        href, etag = await server.put(cal.url, uid, body, None, None)
    elif digest != link.content_hash or deadlines[link.external_id] != link.etag:
        try:
            href, etag = await server.put(
                cal.url, uid, body, link.external_id, deadlines[link.external_id]
            )
        except PreconditionFailed:  # changed again since it was listed: ours wins
            href, etag = await server.put(cal.url, uid, body, link.external_id, None)
    else:
        return
    link.external_id, link.etag = href, etag
    link.container, link.container_name = cal.url, cal.name
    link.content_hash = digest
    link.local_updated_at = task.updated_at
    link.last_synced_at = now
```

주의: `CalDAVClient.put`은 `href`를 주고 `etag=None`이면 조건 헤더 없이 덮어쓴다(기존 동작). 지워진 캘린더 안의 href로 다시 PUT하지 않도록 "not listed"는 `href=None`(새로 만들기)로 보낸다. `FakeCalDAV.put`은 `href or f"{calendar_url}{uid}.ics"`라 같은 경로에 다시 생긴다.

- [ ] **Step 4: Run tests**

Run: `cd backend && uv run pytest tests/test_caldav.py -q`
Expected: PASS (전부). 마감이 있는 상태에서 `test_kanban_deadline_reaches_the_area_calendar_once`의 두 번째 동기화 `pushed == 0`이 "두 번 동기화해도 변화 없음"을 검증한다.

- [ ] **Step 5: Lint**

Run: `cd backend && uv run ruff check src tests && uv run ruff format src tests && uv run pyright`
Expected: 오류 없음.

- [ ] **Step 6: Commit**

```bash
/usr/bin/git add backend/src/argos/caldav_sync.py backend/tests/test_caldav.py
/usr/bin/git commit -m "feat: push kanban deadlines to the calendar"
```

---

### Task 4: 바로 반영과 ICS 피드 중복 제거

**Files:**
- Modify: `backend/src/argos/main.py:87-93`
- Modify: `backend/src/argos/ics.py:23-47`
- Modify: `backend/src/argos/api.py:1446-1463` (`calendar_feed`)
- Test: `backend/tests/test_caldav.py`

**Interfaces:**
- Consumes: `caldav_sync.options`, `CalendarSync.credentials`.
- Produces: `ics.build_feed(session, now, tz_name, *, deadlines: bool = True) -> bytes`

- [ ] **Step 1: Write failing tests**

```python
def test_task_changes_reach_the_calendar_within_seconds(app: TestClient, server: FakeCalDAV) -> None:
    connect(app)
    app.app.state.calendar_sync.debounce = 0.05  # type: ignore[attr-defined]
    task = task_on(app, "운영체제", "곧바로", "2026-10-30T14:59:00Z")
    wait_until(lambda: len(deadlines_in(server, "Argos · 학업")) == 1)
    app.post(f"/api/v1/tasks/{task['id']}/move", json={"status": "done"})
    wait_until(lambda: deadlines_in(server, "Argos · 학업") == [])


def test_feed_leaves_out_deadlines_that_sync(app: TestClient, server: FakeCalDAV) -> None:
    task_on(app, "운영체제", "과제 3", "2026-10-30T14:59:00Z")
    path = app.get("/api/v1/settings/calendar").json()["path"]
    assert "과제 3" in app.get(path).text  # not connected: the feed carries deadlines
    connect(app)
    assert "과제 3" not in app.get(path).text
    set_options(app, push_tasks=False)
    assert "과제 3" in app.get(path).text
```

- [ ] **Step 2: Run to verify fail**

Run: `cd backend && uv run pytest tests/test_caldav.py -q -k "within_seconds or feed"`
Expected: 두 새 테스트 FAIL.

- [ ] **Step 3: Implement**

`main.py`:

```python
        def on_change(_type: str, data: dict[str, Any]) -> None:
            kind = data.get("object_type")
            # Push Argos-side edits right away: events, deadlines, and the channel or
            # area that decides a deadline's calendar (Phase 18).
            if kind in ("event", "task", "channel", "area"):
                app.state.calendar_sync.nudge()
            if kind == "task":  # a linked task may need its checkbox ticked
                app.state.vault_sync.nudge()
```

`ics.py`:

```python
async def build_feed(
    session: AsyncSession, now: datetime, tz_name: str, *, deadlines: bool = True
) -> bytes:
    """`deadlines=False` when the CalDAV sync already puts them in the calendar."""
    ...
    tasks = (
        (
            await session.scalars(
                select(Task).where(
                    Task.due_at.is_not(None), Task.due_at >= since, Task.status != TaskStatus.DONE
                )
            )
        ).all()
        if deadlines
        else []
    )
```

`api.py` `calendar_feed`에 `request: Request` 인자 추가:

```python
    synced = (
        await _calendar_sync(request).credentials(session) is not None
        and (await caldav_sync.options(session)).push_tasks
    )
    body = await ics.build_feed(session, datetime.now(UTC), config.timezone, deadlines=not synced)
```

- [ ] **Step 4: Run all backend tests**

Run: `cd backend && uv run pytest -q`
Expected: PASS. (다른 테스트가 task 변경 후 캘린더 nudge로 영향받으면, 연결이 없을 때 `run`은 바로 돌아오므로 영향 없어야 한다.)

- [ ] **Step 5: Commit**

```bash
/usr/bin/git add backend/src/argos/main.py backend/src/argos/ics.py backend/src/argos/api.py backend/tests/test_caldav.py
/usr/bin/git commit -m "feat: sync deadlines right away and keep them out of the feed"
```

---

### Task 5: 설정 화면 토글

**Files:**
- Modify: `frontend/src/api.ts:931-935`
- Modify: `frontend/src/pages/SettingsPage.tsx:803-860`
- Create: `frontend/src/pages/ICloudSection.test.tsx`

**Interfaces:**
- Consumes: API `PUT /api/v1/settings/icloud/options`, `ICloudOut.push_tasks/split_areas` (Task 2, 타입 생성됨).
- Produces: `useCalendarOptions()` mutation; `export function ICloudSection()`.

- [ ] **Step 1: Write the failing test** (`ICloudSection.test.tsx`)

```tsx
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { ICloudSection } from "./SettingsPage";

const mocks = vi.hoisted(() => ({
  options: { mutate: vi.fn(), isPending: false, error: null },
}));
vi.mock("../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api")>()),
  useICloud: () => ({
    data: {
      username: "me@icloud.com",
      connected: true,
      write_calendar: "Argos",
      push_tasks: true,
      split_areas: true,
      poll_minutes: 10,
      calendars: [],
      conflicts: 0,
      status: { running: false, last_sync_at: null, last_error: null, last_result: null },
    },
  }),
  useConnectICloud: () => ({ mutate: vi.fn(), isPending: false, error: null }),
  useDisconnectICloud: () => ({ mutate: vi.fn(), isPending: false, error: null }),
  useSyncCalendars: () => ({ mutate: vi.fn(), isPending: false, error: null }),
  useCalendarChannel: () => ({ mutate: vi.fn(), isPending: false, error: null }),
  useCalendarOptions: () => mocks.options,
  useChannels: () => ({ data: { channels: [] } }),
  useCalendarConflicts: () => ({ data: [] }),
  useResolveConflict: () => ({ mutate: vi.fn(), isPending: false, error: null }),
}));

afterEach(cleanup);
beforeEach(() => {
  localStorage.setItem("argos-language", "ko");
  mocks.options.mutate.mockReset();
});

test("turning off deadlines keeps the area split", () => {
  render(<ICloudSection />);
  fireEvent.click(screen.getByRole("checkbox", { name: /칸반 마감/ }));
  expect(mocks.options.mutate).toHaveBeenCalledWith({
    push_tasks: false,
    split_areas: true,
  });
});

test("the split toggle names the area calendars", () => {
  render(<ICloudSection />);
  expect(screen.getByRole("checkbox", { name: /영역별 캘린더/ })).toBeChecked();
  expect(screen.getByText(/Argos · 영역 이름/)).toBeInTheDocument();
});
```

(`ConflictList`가 쓰는 훅 이름은 `grep -n "function ConflictList" -A8 frontend/src/pages/SettingsPage.tsx`로 확인해 mock에 넣는다. `toBeChecked`는 `@testing-library/jest-dom`이 설정돼 있어야 한다: `grep -rn "jest-dom" frontend/src/test*`. 없으면 `.checked` 속성으로 확인.)

- [ ] **Step 2: Run to verify fail**

Run: `cd frontend && bunx vitest run src/pages/ICloudSection.test.tsx`
Expected: FAIL (`ICloudSection` export 없음).

- [ ] **Step 3: Implement**

`api.ts` (`useCalendarChannel` 아래):

```ts
export const useCalendarOptions = () =>
  useICloudWrite((body: Schemas["CalendarOptionsIn"]) =>
    call(client.PUT("/api/v1/settings/icloud/options", { body })),
  );
```

`SettingsPage.tsx`: import에 `useCalendarOptions`. `function ICloudSection()` → `export function ICloudSection()`. `RESULT_TEXT`에 `["failed", tr("실패")]` 추가. 컴포넌트 안 `const setChannel = ...` 아래 `const options = useCalendarOptions();`. 설명 문단의 첫 `t(...)`를:

```tsx
        {t(
          `모든 캘린더의 일정을 Argos로 가져오고, Argos에서 만든 일정과 칸반 마감은 "${data?.write_calendar ?? "Argos"}" 캘린더(영역별로 나누면 "${data?.write_calendar ?? "Argos"} · 영역 이름")에만 써요.`,
          `Argos imports events from all calendars and writes its own events and kanban deadlines only to the “${data?.write_calendar ?? "Argos"}” calendar (or “${data?.write_calendar ?? "Argos"} · area name” per area).`,
        )}
```

연결됨 블록의 동기화 상태 줄 바로 아래(`status?.last_error` 앞)에:

```tsx
          <div className="flex flex-col gap-2">
            {(
              [
                [
                  "push_tasks",
                  t("칸반 마감을 캘린더에 올리기", "Put kanban deadlines in the calendar"),
                  t(
                    "마감 30분 전부터 \"마감 · 제목\"으로 보이고, 완료하면 사라져요. 캘린더에서 고쳐도 칸반 내용으로 돌아와요.",
                    "Shown as “Deadline · title” for the half hour before; gone once done. Edits in Calendar are replaced by the kanban.",
                  ),
                ],
                [
                  "split_areas",
                  t("영역별 캘린더로 나누기", "One calendar per area"),
                  t(
                    `새 일정과 마감을 "${data.write_calendar} · 영역 이름" 캘린더에 써요. 이미 있는 일정은 옮기지 않아요.`,
                    `New events and deadlines go to “${data.write_calendar} · area name”. Existing events stay where they are.`,
                  ),
                ],
              ] as const
            ).map(([key, title, hint]) => (
              <label key={key} className="flex items-start gap-2 text-sm">
                <input
                  type="checkbox"
                  className="mt-1 accent-[var(--ink)]"
                  checked={data[key]}
                  disabled={options.isPending}
                  onChange={(e) =>
                    options.mutate({
                      push_tasks: data.push_tasks,
                      split_areas: data.split_areas,
                      [key]: e.target.checked,
                    })
                  }
                />
                <span>
                  {title}
                  <span className="block text-xs text-meta">{hint}</span>
                </span>
              </label>
            ))}
          </div>
```

`ErrorText`의 `error={sync.error ?? setChannel.error ?? disconnect.error}`에 `?? options.error` 추가. `Argos가 씀` 배지는 `c.writable` 그대로(이제 영역 캘린더도 true).

- [ ] **Step 4: Run frontend checks**

Run: `cd frontend && bunx vitest run && bunx tsc -b && bunx biome check src`
Expected: PASS.

- [ ] **Step 5: 화면 확인**

`TERM_PROGRAM=Orca`이고 `orca-cli` 스킬이 있으면 별도 포트 스택(CLAUDE.md "테스트" 절: `ARGOS_PORT=8001 ARGOS_DB_PATH=<임시 db>`, 프런트 `ARGOS_BACKEND=http://127.0.0.1:8001 bunx vite --port 5174`)에서 설정 → iCloud 섹션을 라이트·다크로 본다(연결 안 된 상태라 토글은 안 보이므로, 토글은 vitest로만 확인됐다고 보고). 도구가 없으면 보고에 "화면 확인 못 함".

- [ ] **Step 6: Commit**

```bash
/usr/bin/git add frontend/src/api.ts frontend/src/pages/SettingsPage.tsx frontend/src/pages/ICloudSection.test.tsx
/usr/bin/git commit -m "feat: calendar deadline and area toggles in settings"
```

---

### Task 6: 문서

**Files:**
- Modify: `docs/PLAN.md` (Phase 17 다음에 Phase 18)
- Modify: `docs/decisions.md` (표 끝)
- Modify: `docs/backlog.md` (표 끝)
- Modify: `CLAUDE.md` (아키텍처 백엔드 목록)

- [ ] **Step 1: PLAN Phase 18**

`### Phase 17` 절 다음에:

```markdown
### Phase 18. 칸반 마감을 캘린더에, 영역별 캘린더 (추가 범위, 2026-10-22 사용자 요청)

**목표**: 칸반 마감이 폰 캘린더에 보여 같은 일을 두 군데 적지 않는다. Argos가 쓰는 캘린더를 영역별로 나눈다.

- 마감이 있는 미완료 할 일 → CalDAV로 `마감 · 제목`(마감 30분 전~마감, 바쁨 아님). 앱 → 캘린더 단방향
- 새 일정·마감은 `Argos · 영역 이름` 캘린더(영역 없으면 `Argos`). 이미 있는 일정은 옮기지 않음
- 설정: 마감 올리기, 영역별 나누기(둘 다 기본 켬). iCloud 연결 중이면 ICS 피드에서 마감 제외

**완료 기준**: 칸반에서 마감을 정하면 몇 초 안에 폰 캘린더의 영역 캘린더에 보이고, 완료하면 사라진다 / 두 번 동기화해도 중복 없음 / 올린 마감이 일정으로 다시 들어오지 않음.
```

- [ ] **Step 2: decisions.md 행 추가**

```markdown
| 2026-10-22 | 영역별 쓰기 캘린더 `{caldav_write_calendar} · {영역 이름}`, 영역 없는 채널은 `Argos`. 영역 캘린더 URL을 `app_setting.calendar_areas`에 기억해 영역 이름이 바뀌면 PROPPATCH로 이름만 바꿈 | 사용자 결정(영역마다 새 캘린더). 이름 변경 때 새 캘린더가 생기지 않게 |
| 2026-10-22 | 칸반 마감은 CalDAV로 단방향(앱 원본): 폰에서 고치거나 지우면 다음 동기화에서 덮어쓰거나 다시 만듦, 충돌 화면 없음. `source_link(object_type="task", source="icloud")` | 사용자 문제: 폰에 마감이 안 보여 두 군데 확인. 양방향은 backlog |
| 2026-10-22 | 이미 `Argos` 캘린더에 있는 일정은 영역 캘린더로 옮기지 않음. 앱에서 일정 채널을 바꿔도 올린 캘린더는 그대로 | 사용자 결정 "일단 둬" |
| 2026-10-22 | iCloud가 연결돼 있고 마감 올리기가 켜져 있으면 ICS 피드에서 마감을 뺌 | 둘 다 쓰면 같은 마감이 두 번 보임(2026-09-25 피드 일정 제외와 같은 이유) |
| 2026-10-22 | 링크를 잃은 `task-…@argos` 자원은 일정으로 가져오지 않고 지운 뒤 다시 올림 | 백업 복원 등으로 링크가 사라져도 중복·영구 412가 생기지 않게 |
```

- [ ] **Step 3: backlog.md 행 추가** (기존 열 순서 `날짜 | 항목 | 내용 | 출처 | 상태`를 확인하고 맞춘다)

```markdown
| 2026-10-22 | 폰에서 옮긴 마감을 칸반에 반영 | 지금은 앱 → 캘린더 단방향 | Phase 18 범위 밖 | 대기 |
| 2026-10-22 | 기존 Argos 캘린더 일정을 영역 캘린더로 옮기기 | 사용자 결정으로 보류 | Phase 18 | 대기 |
```

- [ ] **Step 4: CLAUDE.md** — 백엔드 목록에 `caldav_sync.py` 항목이 없으면 `notify.py` 앞에 추가:

```markdown
- **`caldav_sync.py`**: iCloud CalDAV 양방향(Phase 7b) + 칸반 마감 단방향(Phase 18). 쓰기는 Argos 캘린더(`Argos`,
  영역별 `Argos · 영역`, `Targets`)에만. 모든 링크는 `source_link(source="icloud")`, 마감은 `object_type="task"`.
  테스트는 `tests/test_caldav.py`의 `FakeCalDAV`.
```

- [ ] **Step 5: 전체 확인과 커밋**

Run: 루트에서 `make lint && make test`
Expected: PASS.

```bash
/usr/bin/git add docs/PLAN.md docs/decisions.md docs/backlog.md CLAUDE.md
/usr/bin/git commit -m "docs: record calendar deadlines and per-area calendars"
```
