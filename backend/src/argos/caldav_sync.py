"""Two-way sync with iCloud calendars over CalDAV (PLAN Phase 7b).

Every calendar is read into Argos events; only the Argos calendar is ever written
(events made in Argos are created, changed and deleted there). Each synced event has a
`source_link` holding the remote href, ETag and a hash of the remote fields, so a sync
can tell what changed on which side since the last one:

- remote changed only → the Argos event takes the calendar's version
- Argos changed only (Argos calendar) → the calendar resource is replaced
- both → nothing is merged; the remote version is kept on the link as a conflict until
  the user picks a side
- gone remotely → the Argos event is deleted; deleted in Argos → the resource is deleted

Syncing twice with no changes in between writes nothing (idempotent).

The CalDAV client only speaks the few requests this needs (PROPFIND, REPORT, PUT,
DELETE, MKCALENDAR). Credentials live in the macOS Keychain; they never reach logs,
errors or WebSocket events."""

# icalendar types its property values as Unknown; the rest of this module is strict.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

import asyncio
import hashlib
import json
import logging
import uuid
import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import httpx2
import keyring
from icalendar import Calendar, vRecur
from icalendar import Event as VEvent
from icalendar.error import IncompleteComponent, InvalidCalendar
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from argos import services
from argos.config import Settings
from argos.models import Event, SourceLink

log = logging.getLogger(__name__)

SOURCE = "icloud"
ACTOR = "sync:icloud"
PAST = timedelta(days=90)  # sync window, like the ICS feed
FUTURE = timedelta(days=365)

# --- CalDAV client ------------------------------------------------------------------


class CalDAVError(Exception):
    """Safe to show: never carries credentials or response bodies."""


class AuthError(CalDAVError):
    pass


class PreconditionFailed(CalDAVError):
    """The resource changed since its ETag was read."""


@dataclass(frozen=True)
class RemoteCalendar:
    url: str
    name: str


@dataclass(frozen=True)
class RemoteItem:
    href: str
    etag: str | None
    ics: bytes


class CalendarServer(Protocol):
    async def calendars(self) -> list[RemoteCalendar]: ...
    async def create_calendar(self, name: str) -> RemoteCalendar: ...
    async def items(
        self, calendar_url: str, start: datetime, end: datetime
    ) -> list[RemoteItem]: ...
    async def put(
        self, calendar_url: str, uid: str, ics: bytes, href: str | None, etag: str | None
    ) -> tuple[str, str | None]: ...
    async def delete(self, href: str, etag: str | None) -> None: ...


NS = {"d": "DAV:", "c": "urn:ietf:params:xml:ns:caldav"}


def _xml(body: str) -> bytes:
    return f'<?xml version="1.0" encoding="utf-8"?>{body}'.encode()


def _utc_stamp(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


class CalDAVClient:
    """iCloud (or any CalDAV server) with HTTP basic auth and an app-specific password."""

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        self._base = base_url
        self._http = httpx2.AsyncClient(
            auth=httpx2.BasicAuth(username, password),
            follow_redirects=True,
            timeout=30,
            transport=transport,
            headers={"User-Agent": "Argos"},
        )
        self._home: str | None = None

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _send(
        self,
        method: str,
        url: str,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx2.Response:
        try:
            response = await self._http.request(method, url, content=body, headers=headers or {})
        except httpx2.HTTPError as exc:
            raise CalDAVError(f"iCloud에 연결하지 못했어요 ({type(exc).__name__})") from None
        if response.status_code == 401:
            raise AuthError("애플 ID나 앱 전용 암호가 맞지 않아요")
        if response.status_code == 412:
            raise PreconditionFailed("캘린더에서 그사이 바뀌었어요")
        if response.status_code >= 400 and not (method == "DELETE" and response.status_code == 404):
            raise CalDAVError(f"iCloud가 요청을 거절했어요 ({method} {response.status_code})")
        return response

    async def _propfind(self, url: str, props: str, depth: str) -> tuple[ET.Element, str]:
        body = _xml(
            f'<d:propfind xmlns:d="DAV:" xmlns:c="{NS["c"]}"><d:prop>{props}</d:prop></d:propfind>'
        )
        headers = {"Depth": depth, "Content-Type": "application/xml; charset=utf-8"}
        response = await self._send("PROPFIND", url, body, headers)
        return ET.fromstring(response.content), str(response.url)

    async def _calendar_home(self) -> str:
        if self._home is None:
            root, where = await self._propfind(self._base, "<d:current-user-principal/>", "0")
            href = root.findtext(".//d:current-user-principal/d:href", namespaces=NS)
            if not href:
                raise CalDAVError("CalDAV 사용자 정보를 찾지 못했어요")
            principal = urljoin(where, href)
            root, where = await self._propfind(principal, "<c:calendar-home-set/>", "0")
            home = root.findtext(".//c:calendar-home-set/d:href", namespaces=NS)
            if not home:
                raise CalDAVError("캘린더 목록 위치를 찾지 못했어요")
            self._home = urljoin(where, home)
        return self._home

    async def calendars(self) -> list[RemoteCalendar]:
        home = await self._calendar_home()
        props = "<d:resourcetype/><d:displayname/><c:supported-calendar-component-set/>"
        root, where = await self._propfind(home, props, "1")
        found: list[RemoteCalendar] = []
        for resp in root.findall("d:response", NS):
            href = resp.findtext("d:href", namespaces=NS)
            prop = resp.find(".//d:prop", NS)
            if not href or prop is None or prop.find("d:resourcetype/c:calendar", NS) is None:
                continue
            components = [c.get("name") for c in prop.findall(".//c:comp", NS)]
            if components and "VEVENT" not in components:
                continue  # reminders lists
            name = prop.findtext("d:displayname", namespaces=NS) or href.rstrip("/").split("/")[-1]
            found.append(RemoteCalendar(urljoin(where, href), name))
        return found

    async def create_calendar(self, name: str) -> RemoteCalendar:
        url = urljoin(await self._calendar_home(), f"{uuid.uuid4()}/")
        escaped = name.replace("&", "&amp;").replace("<", "&lt;")
        body = _xml(
            f'<c:mkcalendar xmlns:d="DAV:" xmlns:c="{NS["c"]}"><d:set><d:prop>'
            f"<d:displayname>{escaped}</d:displayname>"
            '<c:supported-calendar-component-set><c:comp name="VEVENT"/>'
            "</c:supported-calendar-component-set></d:prop></d:set></c:mkcalendar>"
        )
        await self._send(
            "MKCALENDAR", url, body, {"Content-Type": "application/xml; charset=utf-8"}
        )
        return RemoteCalendar(url, name)

    async def items(self, calendar_url: str, start: datetime, end: datetime) -> list[RemoteItem]:
        body = _xml(
            f'<c:calendar-query xmlns:d="DAV:" xmlns:c="{NS["c"]}">'
            "<d:prop><d:getetag/><c:calendar-data/></d:prop>"
            '<c:filter><c:comp-filter name="VCALENDAR"><c:comp-filter name="VEVENT">'
            f'<c:time-range start="{_utc_stamp(start)}" end="{_utc_stamp(end)}"/>'
            "</c:comp-filter></c:comp-filter></c:filter></c:calendar-query>"
        )
        headers = {"Depth": "1", "Content-Type": "application/xml; charset=utf-8"}
        response = await self._send("REPORT", calendar_url, body, headers)
        root = ET.fromstring(response.content)
        found: list[RemoteItem] = []
        for resp in root.findall("d:response", NS):
            href = resp.findtext("d:href", namespaces=NS)
            data = resp.findtext(".//c:calendar-data", namespaces=NS)
            if not href or not data:
                continue
            etag = resp.findtext(".//d:getetag", namespaces=NS)
            found.append(RemoteItem(urljoin(str(response.url), href), etag, data.encode()))
        return found

    async def put(
        self, calendar_url: str, uid: str, ics: bytes, href: str | None, etag: str | None
    ) -> tuple[str, str | None]:
        target = href or urljoin(calendar_url, f"{uuid.uuid5(uuid.NAMESPACE_URL, uid)}.ics")
        headers = {"Content-Type": "text/calendar; charset=utf-8"}
        if etag:
            headers["If-Match"] = etag
        elif href is None:
            headers["If-None-Match"] = "*"  # never overwrite something we did not make
        response = await self._send("PUT", target, ics, headers)
        return target, response.headers.get("ETag")

    async def delete(self, href: str, etag: str | None) -> None:
        await self._send("DELETE", href, None, {"If-Match": etag} if etag else None)


# --- iCalendar <-> event fields --------------------------------------------------------


@dataclass
class Fields:
    """The event fields a calendar resource carries (stored on conflicts as JSON)."""

    title: str
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    start_date: date | None = None
    end_date: date | None = None
    location: str | None = None
    rrule: str | None = None

    def digest(self) -> str:
        data = {k: v.isoformat() if isinstance(v, date) else v for k, v in asdict(self).items()}
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()

    def to_json(self) -> dict[str, Any]:
        return {k: v.isoformat() if isinstance(v, date) else v for k, v in asdict(self).items()}

    @staticmethod
    def from_json(data: dict[str, Any]) -> "Fields":
        def when(key: str) -> datetime | None:
            return datetime.fromisoformat(data[key]) if data.get(key) else None

        def day(key: str) -> date | None:
            return date.fromisoformat(data[key]) if data.get(key) else None

        return Fields(
            title=data["title"],
            starts_at=when("starts_at"),
            ends_at=when("ends_at"),
            start_date=day("start_date"),
            end_date=day("end_date"),
            location=data.get("location"),
            rrule=data.get("rrule"),
        )

    @staticmethod
    def of(event: Event) -> "Fields":
        return Fields(
            title=event.title,
            starts_at=event.starts_at,
            ends_at=event.ends_at,
            start_date=event.start_date,
            end_date=event.end_date,
            location=event.location,
            rrule=event.rrule,
        )


def _moment(value: date | datetime, tz: ZoneInfo) -> datetime:
    if isinstance(value, datetime):
        # Floating times (no TZID) are wall-clock times in the user's zone.
        return (value if value.tzinfo else value.replace(tzinfo=tz)).astimezone(UTC)
    return datetime(value.year, value.month, value.day, tzinfo=tz).astimezone(UTC)


def _text(value: bytes | str) -> str:
    return value.decode() if isinstance(value, bytes) else value


def parse(ics: bytes, tz: ZoneInfo) -> tuple[str, Fields] | None:
    """UID and fields of the resource's main VEVENT. Changed single occurrences of a
    series (RECURRENCE-ID) are skipped: recurring events are read-only for now."""
    try:
        cal = Calendar.from_ical(ics)
    except ValueError:
        return None
    for item in cal.events:
        if "RECURRENCE-ID" in item:
            continue
        try:
            start = item.DTSTART
            end = item.end  # from DTEND or DURATION; all-day defaults to the next day
        except (IncompleteComponent, InvalidCalendar):
            end = None
            start = item.get("DTSTART") and item.DTSTART
        if start is None:
            continue
        fields = Fields(
            title=str(item.get("SUMMARY", "")).strip() or "(제목 없음)",
            location=str(item["LOCATION"]) if item.get("LOCATION") else None,
            rrule=_text(item["RRULE"].to_ical()) if "RRULE" in item else None,
        )
        if isinstance(start, datetime):
            fields.starts_at = _moment(start, tz)
            fields.ends_at = _moment(end, tz) if isinstance(end, datetime) else None
        else:
            fields.start_date = start
            fields.end_date = (
                end if isinstance(end, date) and end > start else start + timedelta(days=1)
            )
        return str(item.get("UID", "")), fields
    return None


def render(uid: str, fields: Fields, now: datetime) -> bytes:
    cal = Calendar()
    cal.add("prodid", "-//Argos//Argos//KO")
    cal.add("version", "2.0")
    item = VEvent()
    item.add("uid", uid)
    item.add("dtstamp", now)
    item.add("summary", fields.title)
    if fields.start_date is not None:
        item.add("dtstart", fields.start_date)
        item.add("dtend", fields.end_date or fields.start_date + timedelta(days=1))
    else:
        item.add("dtstart", fields.starts_at)
        if fields.ends_at is not None:
            item.add("dtend", fields.ends_at)
    if fields.location:
        item.add("location", fields.location)
    if fields.rrule:
        item.add("rrule", vRecur.from_ical(fields.rrule))
    cal.add_component(item)
    return cal.to_ical()


# --- sync -------------------------------------------------------------------------------


@dataclass
class SyncResult:
    calendars: list[RemoteCalendar] = field(default_factory=list)
    created: int = 0  # new in Argos
    updated: int = 0  # changed in Argos from the calendar
    pushed: int = 0  # written to the Argos calendar
    deleted: int = 0  # removed on either side
    conflicts: int = 0


def _in_window(event: Event, start: datetime, end: datetime) -> bool:
    if event.rrule:
        return True
    if event.start_date is not None:
        return (
            event.start_date <= end.date() and (event.end_date or event.start_date) >= start.date()
        )
    begin = event.starts_at
    finish = event.ends_at or begin
    return begin is not None and finish is not None and begin < end and finish >= start


async def _link_for(session: AsyncSession, href: str) -> SourceLink | None:
    return await session.scalar(
        select(SourceLink).where(SourceLink.source == SOURCE, SourceLink.external_id == href)
    )


async def sync(
    session: AsyncSession,
    server: CalendarServer,
    *,
    write_calendar: str,
    channel_for: Callable[[str], Awaitable[str]],
    now: datetime,
    tz: ZoneInfo,
) -> SyncResult:
    result = SyncResult()
    calendars = await server.calendars()
    target = next((c for c in calendars if c.name == write_calendar), None)
    if target is None:
        target = await server.create_calendar(write_calendar)
        calendars.append(target)
    result.calendars = calendars
    start, end = now - PAST, now + FUTURE
    seen: set[str] = set()

    for cal in calendars:
        writable = cal.url == target.url
        for item in await server.items(cal.url, start, end):
            seen.add(item.href)
            parsed = parse(item.ics, tz)
            if parsed is None:
                continue
            uid, remote = parsed
            link = await _link_for(session, item.href)
            if link is None:
                event = await services.create_event(
                    session,
                    channel_id=await channel_for(cal.url),
                    title=remote.title,
                    starts_at=remote.starts_at,
                    ends_at=remote.ends_at,
                    start_date=remote.start_date,
                    end_date=remote.end_date,
                    location=remote.location,
                    rrule=remote.rrule,
                    calendar_id=cal.url,
                    actor=ACTOR,
                )
                session.add(
                    SourceLink(
                        object_type="event",
                        object_id=event.id,
                        source=SOURCE,
                        external_id=item.href,
                        container=cal.url,
                        container_name=cal.name,
                        uid=uid,
                        etag=item.etag,
                        content_hash=remote.digest(),
                        read_only=not writable,
                        local_updated_at=event.updated_at,
                        last_synced_at=now,
                    )
                )
                await session.commit()
                result.created += 1
                continue

            link.container_name = cal.name
            event = await session.get(Event, link.object_id)
            if event is None:  # deleted in Argos
                if not link.read_only:
                    await server.delete(item.href, item.etag)
                    result.deleted += 1
                await session.delete(link)
                await session.commit()
                continue
            if link.conflict is not None:
                continue  # waiting for the user
            remote_changed = item.etag != link.etag and remote.digest() != link.content_hash
            local_changed = (
                not link.read_only
                and link.local_updated_at is not None
                and event.updated_at > link.local_updated_at
            )
            if remote_changed and local_changed:
                # Keep the old ETag: the calendar's change stays pending until resolved.
                link.conflict = {"etag": item.etag, "fields": remote.to_json()}
                result.conflicts += 1
            elif remote_changed:
                event = await _apply(session, event, remote)
                link.etag, link.content_hash = item.etag, remote.digest()
                link.local_updated_at = event.updated_at
                result.updated += 1
            elif local_changed:
                ours = Fields.of(event)
                link.external_id, link.etag = await server.put(
                    cal.url, link.uid, render(link.uid, ours, now), item.href, item.etag
                )
                link.content_hash = ours.digest()
                link.local_updated_at = event.updated_at
                seen.add(link.external_id)
                result.pushed += 1
            else:
                link.etag = item.etag  # e.g. re-saved without changes
            link.last_synced_at = now
            await session.commit()

    await _settle_unseen(session, server, seen, start, end, result)
    await _push_new(session, server, target, now, start, end, result)
    return result


async def _apply(session: AsyncSession, event: Event, remote: Fields) -> Event:
    changes = {k: v for k, v in asdict(remote).items() if getattr(event, k) != v}
    if not changes:
        return event
    return await services.update_event(session, event.id, changes, ACTOR)


async def _settle_unseen(
    session: AsyncSession,
    server: CalendarServer,
    seen: set[str],
    start: datetime,
    end: datetime,
    result: SyncResult,
) -> None:
    """Links the calendars did not list: gone remotely, deleted in Argos, or just
    outside the sync window."""
    links = (await session.scalars(select(SourceLink).where(SourceLink.source == SOURCE))).all()
    for link in links:
        if link.external_id in seen:
            continue
        event = await session.get(Event, link.object_id)
        if event is None:  # deleted in Argos while outside the window
            if not link.read_only:
                await server.delete(link.external_id, None)
            await session.delete(link)
            result.deleted += 1
        elif _in_window(event, start, end):  # deleted in the calendar
            await session.delete(link)
            await session.commit()
            await services.delete_event(session, event.id, ACTOR)
            result.deleted += 1
            continue
        await session.commit()


async def _push_new(
    session: AsyncSession,
    server: CalendarServer,
    target: RemoteCalendar,
    now: datetime,
    start: datetime,
    end: datetime,
    result: SyncResult,
) -> None:
    """Events made in Argos (no link, no source calendar) go to the Argos calendar."""
    linked = select(SourceLink.object_id).where(SourceLink.source == SOURCE)
    events = (
        await session.scalars(
            select(Event).where(Event.calendar_id.is_(None), Event.id.not_in(linked))
        )
    ).all()
    for event in events:
        if not _in_window(event, start, end):
            continue
        uid = f"event-{event.id}@argos"
        ours = Fields.of(event)
        href, etag = await server.put(target.url, uid, render(uid, ours, now), None, None)
        session.add(
            SourceLink(
                object_type="event",
                object_id=event.id,
                source=SOURCE,
                external_id=href,
                container=target.url,
                container_name=target.name,
                uid=uid,
                etag=etag,
                content_hash=ours.digest(),
                read_only=False,
                local_updated_at=event.updated_at,
                last_synced_at=now,
            )
        )
        await session.commit()
        result.pushed += 1


async def resolve(
    session: AsyncSession,
    server: CalendarServer,
    link_id: str,
    keep: str,
    now: datetime,
) -> Event:
    """Ends a conflict: keep="app" writes the Argos version over the calendar's,
    keep="calendar" takes the calendar's version."""
    link = await session.get(SourceLink, link_id)
    if link is None or link.conflict is None:
        raise services.NotFoundError("conflict", link_id)
    event = await session.get(Event, link.object_id)
    if event is None:
        raise services.NotFoundError("event", link.object_id)
    remote = Fields.from_json(link.conflict["fields"])
    remote_etag = link.conflict.get("etag")
    if keep == "calendar":
        event = await _apply(session, event, remote)
        link.content_hash = remote.digest()
        link.etag = remote_etag
    else:
        ours = Fields.of(event)
        link.external_id, link.etag = await server.put(
            link.container, link.uid, render(link.uid, ours, now), link.external_id, remote_etag
        )
        link.content_hash = ours.digest()
    link.local_updated_at = event.updated_at
    link.conflict = None
    link.last_synced_at = now
    await session.commit()
    return event


# --- credentials and the background loop ---------------------------------------------------

KEYCHAIN_SERVICE = "Argos iCloud CalDAV"


class Keychain:
    """App-specific password in the macOS Keychain (user decision), via `keyring`."""

    def get(self, username: str) -> str | None:
        return keyring.get_password(KEYCHAIN_SERVICE, username)

    def set(self, username: str, password: str) -> None:
        # keyring replaces a macOS item by deleting it first. A previously installed
        # app may still read its item but be unable to delete it (Security -25244).
        if self.get(username) != password:
            keyring.set_password(KEYCHAIN_SERVICE, username, password)

    def delete(self, username: str) -> None:
        if self.get(username) is not None:
            keyring.delete_password(KEYCHAIN_SERVICE, username)


ServerFactory = Callable[[Settings, str, str], CalendarServer]


def icloud_server(settings: Settings, username: str, password: str) -> CalendarServer:
    return CalDAVClient(settings.caldav_url, username, password)


@dataclass
class SyncStatus:
    running: bool = False
    last_sync_at: datetime | None = None
    last_error: str | None = None
    last_result: dict[str, int] | None = None


class CalendarSync:
    """Runs sync on a timer and on demand, one at a time."""

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        settings: Callable[[], Settings],
        keychain: Keychain,
        server_factory: ServerFactory = icloud_server,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._settings = settings
        self.keychain = keychain
        self.server_factory = server_factory
        self.status = SyncStatus()
        self._lock = asyncio.Lock()
        self._loop: asyncio.Task[None] | None = None
        self.debounce = 3.0  # seconds after an Argos-side change before syncing it
        self._soon: asyncio.TimerHandle | None = None
        self._tasks: set[asyncio.Task[SyncStatus]] = set()

    def nudge(self) -> None:
        """An event changed in Argos: sync shortly instead of at the next poll, so a
        created or deleted event reaches the phone in seconds. Changes made by the sync
        itself arrive while it runs and are ignored; bursts collapse into one run."""
        if self.status.running or self._soon is not None:
            return

        def fire() -> None:
            self._soon = None
            task = asyncio.create_task(self.run())
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

        self._soon = asyncio.get_running_loop().call_later(self.debounce, fire)

    async def credentials(self, session: AsyncSession) -> tuple[str, str] | None:
        username = await services.get_setting(session, ICLOUD_USERNAME)
        if not username:
            return None
        password = await asyncio.to_thread(self.keychain.get, username)
        return (username, password) if password else None

    async def run(self) -> SyncStatus:
        async with self._lock:
            self.status.running = True
            try:
                async with self._sessionmaker() as session:
                    creds = await self.credentials(session)
                    if creds is None:
                        return self.status
                    config = self._settings()
                    server = self.server_factory(config, *creds)
                    try:
                        result = await sync(
                            session,
                            server,
                            write_calendar=config.caldav_write_calendar,
                            channel_for=lambda url: channel_for(session, url),
                            now=datetime.now(UTC),
                            tz=config.zoneinfo,
                        )
                    finally:
                        if isinstance(server, CalDAVClient):
                            await server.aclose()
                    await services.set_setting_quietly(
                        session,
                        ICLOUD_CALENDARS,
                        [{"url": c.url, "name": c.name} for c in result.calendars],
                    )
                counts = asdict(result)
                counts.pop("calendars")
                self.status.last_result = counts
                self.status.last_error = None
            except CalDAVError as exc:
                self.status.last_error = str(exc)
            except Exception:
                log.exception("calendar sync failed")
                self.status.last_error = "동기화 중 오류가 났어요. 서버 로그를 확인하세요"
            finally:
                self.status.running = False
                self.status.last_sync_at = datetime.now(UTC)
            return self.status

    def start(self) -> None:
        async def loop() -> None:
            while True:
                await self.run()
                await asyncio.sleep(self._settings().caldav_poll_minutes * 60)

        self._loop = asyncio.create_task(loop())

    async def stop(self) -> None:
        if self._soon is not None:
            self._soon.cancel()
        for task in list(self._tasks):
            task.cancel()
        if self._loop is not None:
            self._loop.cancel()
            try:
                await self._loop
            except asyncio.CancelledError:
                pass


ICLOUD_USERNAME = "icloud_username"
ICLOUD_CALENDARS = "icloud_calendars"
CALENDAR_CHANNELS = "calendar_channels"


async def channel_for(session: AsyncSession, calendar_url: str) -> str:
    """Channel for events of a calendar: the user's pick, else #일상."""
    mapping: dict[str, str] = await services.get_setting(session, CALENDAR_CHANNELS) or {}
    if (chosen := mapping.get(calendar_url)) and await services.channel_exists(session, chosen):
        return chosen
    personal = await services.get_personal_channel(session)
    if personal is None:
        raise CalDAVError("일정을 넣을 #일상 채널이 없어요")
    return personal.id
