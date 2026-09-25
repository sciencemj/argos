"""Two-way iCloud calendar sync (PLAN Phase 7b) against a fake CalDAV server, plus the
CalDAV client's requests against canned iCloud-style responses."""

# pyright: reportUnknownMemberType=false

import base64
import itertools
import time
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient

from argos import caldav_sync
from argos.caldav_sync import (
    AuthError,
    CalDAVClient,
    PreconditionFailed,
    RemoteCalendar,
    RemoteItem,
)
from argos.config import Settings
from argos.main import create_app

PASSWORD = "abcd-efgh-ijkl-mnop"
HOME = "https://p01-caldav.icloud.com/123/calendars/"


class FakeCalDAV:
    """In-memory calendars with ETags that change on every write, like a server."""

    def __init__(self) -> None:
        self.calendars_: dict[str, RemoteCalendar] = {}
        self.resources: dict[str, tuple[str, str, bytes]] = {}  # href → (calendar, etag, ics)
        self.requests: list[tuple[str, str, str | None]] = []  # (method, href, if-match)
        self._etags = itertools.count(1)

    def add_calendar(self, name: str) -> RemoteCalendar:
        cal = RemoteCalendar(f"{HOME}{name.lower()}/", name)
        self.calendars_[cal.url] = cal
        return cal

    def store(self, cal: RemoteCalendar, name: str, ics: bytes) -> str:
        href = f"{cal.url}{name}.ics"
        self.resources[href] = (cal.url, f'"{next(self._etags)}"', ics)
        return href

    def in_calendar(self, name: str) -> dict[str, bytes]:
        url = next(c.url for c in self.calendars_.values() if c.name == name)
        return {h: ics for h, (c, _, ics) in self.resources.items() if c == url}

    async def calendars(self) -> list[RemoteCalendar]:
        return list(self.calendars_.values())

    async def create_calendar(self, name: str) -> RemoteCalendar:
        self.requests.append(("MKCALENDAR", name, None))
        return self.add_calendar(name)

    async def items(self, calendar_url: str, start: datetime, end: datetime) -> list[RemoteItem]:
        return [
            RemoteItem(href, etag, ics)
            for href, (cal, etag, ics) in self.resources.items()
            if cal == calendar_url
        ]

    async def put(
        self, calendar_url: str, uid: str, ics: bytes, href: str | None, etag: str | None
    ) -> tuple[str, str | None]:
        target = href or f"{calendar_url}{uid}.ics"
        self.requests.append(("PUT", target, etag))
        if etag is not None and self.resources[target][1] != etag:
            raise PreconditionFailed("changed")
        new = f'"{next(self._etags)}"'
        self.resources[target] = (calendar_url, new, ics)
        return target, new

    async def delete(self, href: str, etag: str | None) -> None:
        self.requests.append(("DELETE", href, etag))
        self.resources.pop(href, None)


class FakeKeychain:
    def __init__(self) -> None:
        self.saved: dict[str, str] = {}

    def get(self, username: str) -> str | None:
        return self.saved.get(username)

    def set(self, username: str, password: str) -> None:
        self.saved[username] = password

    def delete(self, username: str) -> None:
        self.saved.pop(username, None)


def vevent(
    uid: str,
    summary: str,
    start: str,
    end: str | None = None,
    *,
    rrule: str | None = None,
    extra: str = "",
) -> bytes:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Apple Inc.//iPhone//EN",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        "DTSTAMP:20260901T000000Z",
        f"SUMMARY:{summary}",
        f"DTSTART{start}",
    ]
    if end:
        lines.append(f"DTEND{end}")
    if rrule:
        lines.append(f"RRULE:{rrule}")
    if extra:
        lines.append(extra)
    lines += ["END:VEVENT", "END:VCALENDAR", ""]
    return "\r\n".join(lines).encode()


@pytest.fixture
def server() -> FakeCalDAV:
    fake = FakeCalDAV()
    fake.add_calendar("개인")
    fake.add_calendar("학교")
    return fake


@pytest.fixture
def keychain() -> FakeKeychain:
    return FakeKeychain()


@pytest.fixture
def app(settings: Settings, server: FakeCalDAV, keychain: FakeKeychain) -> Iterator[TestClient]:
    def factory(config: Settings, username: str, password: str) -> FakeCalDAV:
        if password != PASSWORD:

            class Refusing(FakeCalDAV):
                async def calendars(self) -> list[RemoteCalendar]:
                    raise AuthError("애플 ID나 앱 전용 암호가 맞지 않아요")

            return Refusing()
        return server

    with TestClient(create_app(settings)) as client:
        sync = client.app.state.calendar_sync  # type: ignore[attr-defined]
        sync.keychain = keychain
        sync.server_factory = factory
        yield client


def connect(client: TestClient) -> dict[str, Any]:
    response = client.put(
        "/api/v1/settings/icloud", json={"username": "me@icloud.com", "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    return response.json()


def sync(client: TestClient) -> dict[str, int]:
    status = client.post("/api/v1/calendar/sync").json()["status"]
    assert status["last_error"] is None, status["last_error"]
    return status["last_result"]


def events(client: TestClient, days: int = 30) -> list[dict[str, Any]]:
    start = datetime(2026, 10, 1, tzinfo=UTC)
    return client.get(
        "/api/v1/events",
        params={"start": start.isoformat(), "end": (start + timedelta(days=days)).isoformat()},
    ).json()


def channel_named(client: TestClient, name: str) -> str:
    channels = client.get("/api/v1/channels").json()["channels"]
    return next(c["id"] for c in channels if c["name"] == name)


def test_wrong_password_is_refused_and_not_kept(app: TestClient, keychain: FakeKeychain) -> None:
    refused = app.put(
        "/api/v1/settings/icloud", json={"username": "me@icloud.com", "password": "nope"}
    )
    assert refused.status_code == 422
    assert "앱 전용 암호" in refused.json()["error"]["message"]
    assert keychain.saved == {}
    assert app.get("/api/v1/settings/icloud").json()["connected"] is False


def test_password_lives_only_in_the_keychain(
    app: TestClient, keychain: FakeKeychain, settings: Settings
) -> None:
    body = connect(app)
    assert body["connected"] is True and body["username"] == "me@icloud.com"
    assert keychain.saved == {"me@icloud.com": PASSWORD}
    assert PASSWORD not in app.get("/api/v1/settings/icloud").text
    for path in Path(settings.db_path).parent.glob("test.db*"):
        assert PASSWORD.encode() not in path.read_bytes()

    app.delete("/api/v1/settings/icloud")
    assert keychain.saved == {}
    assert app.get("/api/v1/settings/icloud").json()["connected"] is False


def test_first_sync_reads_every_calendar_and_writes_only_to_argos(
    app: TestClient, server: FakeCalDAV
) -> None:
    personal = server.calendars_[f"{HOME}개인/"]
    school = server.calendars_[f"{HOME}학교/"]
    server.store(
        personal,
        "dentist",
        vevent(
            "d1", "치과", ";TZID=Asia/Seoul:20261005T150000", ";TZID=Asia/Seoul:20261005T160000"
        ),
    )
    server.store(
        school, "exam", vevent("e1", "중간고사", ";VALUE=DATE:20261020", ";VALUE=DATE:20261023")
    )
    course = channel_named(app, "컴퓨터구조")
    ours = app.post(
        "/api/v1/events",
        json={
            "channel_id": course,
            "title": "스터디",
            "starts_at": "2026-10-07T10:00:00Z",
            "ends_at": "2026-10-07T11:00:00Z",
        },
    ).json()

    connect(app)  # syncs in the background right after connecting
    status = app.get("/api/v1/settings/icloud").json()
    assert {c["name"] for c in status["calendars"]} == {"개인", "학교", "Argos"}
    assert ("MKCALENDAR", "Argos", None) in server.requests

    shown = {e["title"]: e for e in events(app)}
    dentist = shown["치과"]
    assert dentist["starts_at"] == "2026-10-05T06:00:00Z"  # 15:00 KST
    assert (dentist["source"], dentist["read_only"]) == ("개인", True)
    assert dentist["channel_id"] == channel_named(app, "일상")
    assert (shown["중간고사"]["start_date"], shown["중간고사"]["end_date"]) == (
        "2026-10-20",
        "2026-10-23",
    )
    assert (shown["스터디"]["source"], shown["스터디"]["read_only"]) == ("Argos", False)

    [pushed] = server.in_calendar("Argos").values()
    assert f"UID:event-{ours['id']}@argos".encode() in pushed
    assert b"SUMMARY:\xec\x8a\xa4\xed\x84\xb0\xeb\x94\x94" in pushed  # 스터디
    assert server.in_calendar("개인") and all(
        method != "PUT" or href.startswith(f"{HOME}argos/") for method, href, _ in server.requests
    )


def test_second_sync_changes_nothing(app: TestClient, server: FakeCalDAV) -> None:
    """PLAN 7b done-criterion: syncing twice leaves no duplicates."""
    server.store(
        server.calendars_[f"{HOME}개인/"],
        "gym",
        vevent("g1", "헬스", ":20261006T010000Z", ":20261006T020000Z"),
    )
    connect(app)
    before = (events(app), len(server.requests))
    assert sync(app) == {"created": 0, "updated": 0, "pushed": 0, "deleted": 0, "conflicts": 0}
    assert sync(app)["created"] == 0
    assert (events(app), len(server.requests)) == before


def test_calendar_changes_and_deletions_reach_argos(app: TestClient, server: FakeCalDAV) -> None:
    personal = server.calendars_[f"{HOME}개인/"]
    href = server.store(
        personal, "lunch", vevent("l1", "점심 약속", ":20261008T030000Z", ":20261008T040000Z")
    )
    connect(app)
    server.store(
        personal,
        "lunch",
        vevent(
            "l1",
            "점심 약속 (장소 변경)",
            ":20261008T040000Z",
            ":20261008T050000Z",
            extra="LOCATION:학생회관",
        ),
    )
    assert sync(app)["updated"] == 1
    [lunch] = events(app)
    assert (lunch["title"], lunch["starts_at"], lunch["location"]) == (
        "점심 약속 (장소 변경)",
        "2026-10-08T04:00:00Z",
        "학생회관",
    )

    del server.resources[href]
    assert sync(app)["deleted"] == 1
    assert events(app) == []


def test_argos_changes_go_to_the_argos_calendar_only(app: TestClient, server: FakeCalDAV) -> None:
    connect(app)
    course = channel_named(app, "컴퓨터구조")
    event = app.post(
        "/api/v1/events",
        json={
            "channel_id": course,
            "title": "실습",
            "starts_at": "2026-10-09T01:00:00Z",
            "ends_at": "2026-10-09T02:00:00Z",
        },
    ).json()
    assert sync(app)["pushed"] == 1
    [(href, (_, etag, _))] = [
        (h, r) for h, r in server.resources.items() if h.startswith(f"{HOME}argos/")
    ]

    app.patch(
        f"/api/v1/events/{event['id']}",
        json={"title": "실습 (연장)", "ends_at": "2026-10-09T03:00:00Z"},
    )
    assert sync(app)["pushed"] == 1
    assert server.requests[-1] == ("PUT", href, etag)  # If-Match: only over what we saw
    assert "실습 (연장)".encode() in server.resources[href][2]

    app.delete(f"/api/v1/events/{event['id']}")
    assert sync(app)["deleted"] == 1
    assert href not in server.resources
    assert server.requests[-1][0] == "DELETE"


def test_argos_changes_sync_within_seconds(app: TestClient, server: FakeCalDAV) -> None:
    """Creating or deleting in Argos must not wait for the 10-minute poll (user report:
    a deleted event stayed on the phone until the next poll)."""
    connect(app)
    app.app.state.calendar_sync.debounce = 0.05  # type: ignore[attr-defined]
    course = channel_named(app, "컴퓨터구조")
    event = app.post(
        "/api/v1/events",
        json={"channel_id": course, "title": "곧바로", "start_date": "2026-10-21"},
    ).json()
    wait_until(lambda: len(server.in_calendar("Argos")) == 1)

    app.delete(f"/api/v1/events/{event['id']}")
    wait_until(lambda: server.in_calendar("Argos") == {})


def wait_until(check: Callable[[], bool]) -> None:
    deadline = time.monotonic() + 3
    while not check():
        assert time.monotonic() < deadline, "sync did not follow the change"
        time.sleep(0.02)


def test_other_calendars_are_read_only_in_argos(app: TestClient, server: FakeCalDAV) -> None:
    server.store(
        server.calendars_[f"{HOME}학교/"],
        "class",
        vevent("c1", "수업", ":20261012T000000Z", ":20261012T013000Z"),
    )
    connect(app)
    [event] = events(app)
    refused = app.patch(f"/api/v1/events/{event['id']}", json={"title": "고침"})
    assert refused.status_code == 409
    assert "'학교' 캘린더" in refused.json()["error"]["message"]
    assert app.delete(f"/api/v1/events/{event['id']}").status_code == 409

    course = channel_named(app, "컴퓨터구조")  # filing it under a channel is Argos-only
    assert (
        app.patch(f"/api/v1/events/{event['id']}", json={"channel_id": course}).status_code == 200
    )


def test_calendar_channel_choice_moves_its_events(app: TestClient, server: FakeCalDAV) -> None:
    school = server.calendars_[f"{HOME}학교/"]
    server.store(school, "class", vevent("c1", "수업", ":20261012T000000Z", ":20261012T013000Z"))
    connect(app)
    course = channel_named(app, "컴퓨터구조")
    app.put(
        "/api/v1/settings/icloud/channels", json={"calendar_url": school.url, "channel_id": course}
    )
    assert [e["channel_id"] for e in events(app)] == [course]
    server.store(school, "lab", vevent("c2", "실험", ":20261013T000000Z", ":20261013T013000Z"))
    sync(app)
    assert {e["channel_id"] for e in events(app)} == {course}


def test_both_sides_changed_waits_for_the_user(app: TestClient, server: FakeCalDAV) -> None:
    connect(app)
    course = channel_named(app, "컴퓨터구조")
    event = app.post(
        "/api/v1/events",
        json={
            "channel_id": course,
            "title": "면담",
            "starts_at": "2026-10-14T05:00:00Z",
            "ends_at": "2026-10-14T06:00:00Z",
        },
    ).json()
    sync(app)
    [href] = server.in_calendar("Argos")
    argos = server.calendars_[f"{HOME}argos/"]
    uid = f"event-{event['id']}@argos"
    server.store(
        argos,
        href.rsplit("/", 1)[1].removesuffix(".ics"),
        vevent(uid, "면담 (폰에서 수정)", ":20261014T070000Z", ":20261014T080000Z"),
    )
    app.patch(f"/api/v1/events/{event['id']}", json={"title": "면담 (앱에서 수정)"})

    assert sync(app)["conflicts"] == 1
    assert sync(app)["conflicts"] == 0  # still pending, not reported again
    [conflict] = app.get("/api/v1/calendar/conflicts").json()
    assert conflict["event"]["title"] == "면담 (앱에서 수정)"
    assert conflict["remote"]["title"] == "면담 (폰에서 수정)"
    assert "면담 (폰에서 수정)".encode() in server.resources[href][2]  # nothing merged
    assert app.get("/api/v1/settings/icloud").json()["conflicts"] == 1

    kept = app.post(
        f"/api/v1/calendar/conflicts/{conflict['id']}", json={"keep": "calendar"}
    ).json()
    assert (kept["title"], kept["starts_at"]) == ("면담 (폰에서 수정)", "2026-10-14T07:00:00Z")
    assert app.get("/api/v1/calendar/conflicts").json() == []
    assert sync(app) == {"created": 0, "updated": 0, "pushed": 0, "deleted": 0, "conflicts": 0}


def test_keeping_the_app_version_overwrites_the_calendar(
    app: TestClient, server: FakeCalDAV
) -> None:
    connect(app)
    course = channel_named(app, "컴퓨터구조")
    event = app.post(
        "/api/v1/events",
        json={"channel_id": course, "title": "발표", "start_date": "2026-10-16"},
    ).json()
    sync(app)
    [href] = server.in_calendar("Argos")
    argos = server.calendars_[f"{HOME}argos/"]
    server.store(
        argos,
        href.rsplit("/", 1)[1].removesuffix(".ics"),
        vevent(f"event-{event['id']}@argos", "발표 (폰)", ";VALUE=DATE:20261017"),
    )
    app.patch(f"/api/v1/events/{event['id']}", json={"title": "발표 (앱)"})
    sync(app)
    [conflict] = app.get("/api/v1/calendar/conflicts").json()
    app.post(f"/api/v1/calendar/conflicts/{conflict['id']}", json={"keep": "app"})
    assert "발표 (앱)".encode() in server.resources[href][2]
    assert b"DTSTART;VALUE=DATE:20261016" in server.resources[href][2]
    assert sync(app)["pushed"] == 0


def test_recurring_events_show_on_local_weekdays(app: TestClient, server: FakeCalDAV) -> None:
    """A Tuesday 08:00 KST class is Monday 23:00 UTC: occurrences must stay on Tuesdays."""
    school = server.calendars_[f"{HOME}학교/"]
    server.store(
        school,
        "lecture",
        vevent(
            "r1",
            "컴구 강의",
            ";TZID=Asia/Seoul:20260901T080000",
            ";TZID=Asia/Seoul:20260901T093000",
            rrule="FREQ=WEEKLY;BYDAY=TU;UNTIL=20261231T145959Z",
        ),
    )
    server.store(
        school,
        "holiday",
        vevent(
            "r2",
            "동아리 MT",
            ";VALUE=DATE:20260903",
            ";VALUE=DATE:20260905",
            rrule="FREQ=MONTHLY;UNTIL=20261203",
        ),
    )
    connect(app)
    shown = events(app, days=31)  # October 2026
    lectures = [e for e in shown if e["title"] == "컴구 강의"]
    assert [e["starts_at"] for e in lectures] == [
        f"2026-10-{day:02}T23:00:00Z" for day in (5, 12, 19, 26)
    ]
    assert all(e["read_only"] for e in lectures)
    [mt] = [e for e in shown if e["title"] == "동아리 MT"]
    assert (mt["start_date"], mt["end_date"]) == ("2026-10-03", "2026-10-05")


def test_today_counts_a_series_occurrence(app: TestClient, server: FakeCalDAV) -> None:
    today = datetime.now(UTC).astimezone(caldav_sync.ZoneInfo("Asia/Seoul")).date()
    first = today - timedelta(days=14)
    server.store(
        server.calendars_[f"{HOME}학교/"],
        "daily",
        vevent(
            "r3",
            "아침 운동",
            f";TZID=Asia/Seoul:{first:%Y%m%d}T070000",
            f";TZID=Asia/Seoul:{first:%Y%m%d}T080000",
            rrule="FREQ=DAILY",
        ),
    )
    connect(app)
    titles = [e["title"] for e in app.get("/api/v1/today").json()["events"]]
    assert titles == ["아침 운동"]


def test_feed_leaves_out_synced_events(app: TestClient, server: FakeCalDAV) -> None:
    server.store(
        server.calendars_[f"{HOME}개인/"], "trip", vevent("t1", "여행", ";VALUE=DATE:20261101")
    )
    connect(app)
    feed = app.get(app.get("/api/v1/settings/calendar").json()["path"]).text
    assert "여행" not in feed  # already in Apple Calendar; no duplicate via the feed


def test_sync_error_is_reported_without_details(app: TestClient, keychain: FakeKeychain) -> None:
    connect(app)

    class Broken(FakeCalDAV):
        async def calendars(self) -> list[RemoteCalendar]:
            raise AuthError("애플 ID나 앱 전용 암호가 맞지 않아요")

    app.app.state.calendar_sync.server_factory = lambda *a: Broken()  # type: ignore[attr-defined]
    status = app.post("/api/v1/calendar/sync").json()["status"]
    assert status["last_error"] == "애플 ID나 앱 전용 암호가 맞지 않아요"


# --- CalDAV requests (canned iCloud-style responses) -----------------------------------


NS = 'xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"'
AUTH = "Basic " + base64.b64encode(f"me@icloud.com:{PASSWORD}".encode()).decode()


def multistatus(*responses: tuple[str, str]) -> httpx2.Response:
    body = "".join(
        f"<d:response><d:href>{href}</d:href><d:propstat><d:prop>{props}</d:prop>"
        "</d:propstat></d:response>"
        for href, props in responses
    )
    return httpx2.Response(207, content=f"<d:multistatus {NS}>{body}</d:multistatus>")


def calendar_props(name: str, kind: str, component: str) -> str:
    return (
        f"<d:resourcetype><d:collection/>{kind}</d:resourcetype><d:displayname>{name}</d:displayname>"
        f'<c:supported-calendar-component-set><c:comp name="{component}"/>'
        "</c:supported-calendar-component-set>"
    )


def canned(request: httpx2.Request, seen: list[httpx2.Request]) -> httpx2.Response:
    seen.append(request)
    url, method = str(request.url), request.method
    if request.headers.get("authorization") != AUTH:
        return httpx2.Response(401)
    if method == "PROPFIND" and url == "https://caldav.icloud.com/":
        principal = (
            "<d:current-user-principal><d:href>/123/principal/</d:href></d:current-user-principal>"
        )
        return multistatus(("/", principal))
    if method == "PROPFIND" and url.endswith("/123/principal/"):
        home = f"<c:calendar-home-set><d:href>{HOME}</d:href></c:calendar-home-set>"
        return multistatus(("/123/principal/", home))
    if method == "PROPFIND" and url == HOME:
        return multistatus(
            ("/123/calendars/home/", calendar_props("개인", "<c:calendar/>", "VEVENT")),
            ("/123/calendars/tasks/", calendar_props("미리 알림", "<c:calendar/>", "VTODO")),
            ("/123/calendars/inbox/", calendar_props("", "<c:schedule-inbox/>", "VEVENT")),
        )
    if method == "REPORT":
        ics = vevent("d1", "치과", ":20261005T060000Z").decode()
        props = f'<d:getetag>"e1"</d:getetag><c:calendar-data>{ics}</c:calendar-data>'
        return multistatus(("/123/calendars/home/d1.ics", props))
    if method == "PUT":
        if request.headers.get("if-match") == '"stale"':
            return httpx2.Response(412)
        return httpx2.Response(201, headers={"ETag": '"e2"'})
    if method == "DELETE":
        return httpx2.Response(404)
    return httpx2.Response(400)


@pytest.fixture
def seen() -> list[httpx2.Request]:
    return []


@pytest.fixture
def dav(seen: list[httpx2.Request]) -> CalDAVClient:
    transport = httpx2.MockTransport(lambda r: canned(r, seen))
    return CalDAVClient("https://caldav.icloud.com/", "me@icloud.com", PASSWORD, transport)


async def test_client_discovers_event_calendars(dav: CalDAVClient) -> None:
    assert await dav.calendars() == [RemoteCalendar(f"{HOME}home/", "개인")]


async def test_client_reads_items_with_etags(dav: CalDAVClient, seen: list[httpx2.Request]) -> None:
    [item] = await dav.items(
        f"{HOME}home/", datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 12, 1, tzinfo=UTC)
    )
    assert (item.href, item.etag) == (f"{HOME}home/d1.ics", '"e1"')
    parsed = caldav_sync.parse(item.ics, caldav_sync.ZoneInfo("Asia/Seoul"))
    assert parsed is not None and parsed[1].title == "치과"
    assert b'time-range start="20260901T000000Z" end="20261201T000000Z"' in seen[-1].content
    assert seen[-1].headers["depth"] == "1"


async def test_client_writes_only_what_it_saw(
    dav: CalDAVClient, seen: list[httpx2.Request]
) -> None:
    href, etag = await dav.put(f"{HOME}argos/", "event-1@argos", b"ICS", None, None)
    assert etag == '"e2"' and href.startswith(f"{HOME}argos/")
    assert seen[-1].headers["if-none-match"] == "*"  # create, never overwrite
    await dav.put(f"{HOME}argos/", "event-1@argos", b"ICS", href, '"e2"')
    assert seen[-1].headers["if-match"] == '"e2"'
    with pytest.raises(PreconditionFailed):
        await dav.put(f"{HOME}argos/", "event-1@argos", b"ICS", href, '"stale"')
    await dav.delete(href, None)  # already gone (404) is fine


async def test_client_reports_bad_credentials_without_leaking_them() -> None:
    wrong = CalDAVClient(
        "https://caldav.icloud.com/",
        "me@icloud.com",
        "wrong-pass",
        httpx2.MockTransport(lambda r: canned(r, [])),
    )
    with pytest.raises(AuthError) as caught:
        await wrong.calendars()
    assert "wrong-pass" not in str(caught.value) and "me@icloud.com" not in str(caught.value)


def test_render_round_trips_through_parse() -> None:
    tz = caldav_sync.ZoneInfo("Asia/Seoul")
    fields = caldav_sync.Fields(
        title="세미나", start_date=date(2026, 11, 2), end_date=date(2026, 11, 3), location="301호"
    )
    parsed = caldav_sync.parse(caldav_sync.render("u1", fields, datetime.now(UTC)), tz)
    assert parsed == ("u1", fields)
