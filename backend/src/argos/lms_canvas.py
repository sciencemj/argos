"""LMS sync with a Canvas personal access token (Phase 17), the default LMS connection.

The user generates a token in their school's Canvas (Account → Settings → New Access
Token) and pastes it with the LMS address. The token lives only in the macOS Keychain;
`app_setting` keeps the address and the account's display name. Argos then reads the
same Canvas REST endpoints the browser extension reads, on a timer, without Chrome.

Only GET requests reach Canvas, only on the configured origin under `/api/v1/` (plus
the file download route). Redirects are never followed with the token: file bytes come
from a separate client that carries no credentials.
"""

import asyncio
import io
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any, cast
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import httpx2 as httpx
import keyring
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from argos import services
from argos.config import Settings
from argos.summarizer import LmsSummaries

log = logging.getLogger(__name__)

KEYCHAIN_SERVICE = "Argos LMS access token"
CANVAS_SETTING = "lms_canvas"  # {"url": origin, "user": display name, "account_id": id}
MATERIALS_SETTING = "lms_canvas_materials"
FILE_LIMIT = 25 * 1024 * 1024
MAX_PAGES = 50


class CanvasError(Exception):
    """A message safe to show in settings (never the token or a signed URL)."""


class CanvasAuthError(CanvasError):
    pass


def normalize_url(value: str) -> str:
    """The LMS origin from what the user pasted ("lms.school.ac.kr/courses/1" too)."""
    raw = value.strip()
    if "://" not in raw:
        raw = f"https://{raw}"
    try:
        url = urlsplit(raw)
        port = url.port
    except ValueError as exc:
        raise CanvasError("LMS 주소를 확인해 주세요") from exc
    if url.scheme != "https" or not url.hostname or url.username or url.password:
        raise CanvasError("https로 시작하는 LMS 주소가 필요해요")
    host = url.hostname if port in {None, 443} else f"{url.hostname}:{port}"
    return f"https://{host}"


class Keychain:
    """The access token in the macOS Keychain, keyed by LMS origin, via `keyring`."""

    def get(self, origin: str) -> str | None:
        return keyring.get_password(KEYCHAIN_SERVICE, origin)

    def set(self, origin: str, token: str) -> None:
        if self.get(origin) != token:
            keyring.set_password(KEYCHAIN_SERVICE, origin, token)

    def delete(self, origin: str) -> None:
        if self.get(origin) is not None:
            keyring.delete_password(KEYCHAIN_SERVICE, origin)


# --- HTML to text, the same reading as the extension's DOMParser textContent ------------


class _Html(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text: list[str] = []
        self.links: list[tuple[str, str]] = []
        self._skip = 0
        self._link: tuple[str, list[str]] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._skip += 1
        elif tag == "a" and (href := dict(attrs).get("href")) is not None:
            self._link = (href, [])

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._skip:
            self._skip -= 1
        elif tag == "a" and self._link is not None:
            href, label = self._link
            self.links.append((href, "".join(label)))
            self._link = None

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        self.text.append(data)
        if self._link is not None:
            self._link[1].append(data)


def _parse(html: object) -> _Html:
    parser = _Html()
    parser.feed("" if html is None else str(html))
    parser.close()
    return parser


def html_text(html: object) -> str:
    return "".join(_parse(html).text).strip()[:20000]


_SECRET_PARAM = re.compile(r"auth|token|key|sig|session|pass|secret|user_?id|uid", re.I)


def body_links(html: object, origin: str) -> str:
    """Up to 20 http(s) links of an LMS body; text conversion would lose them.
    Query parameters that may carry the student's credentials or ids are dropped."""
    out: list[str] = []
    for href, label in _parse(html).links:
        try:
            url = urlsplit(urljoin(origin + "/", href.strip()))
            if url.scheme not in {"http", "https"} or url.username or url.password:
                continue
            query = urlencode(
                [
                    (k, v)
                    for k, v in parse_qsl(url.query, keep_blank_values=True)
                    if not _SECRET_PARAM.search(k)
                ]
            )
            link = urlunsplit(url._replace(query=query))
        except ValueError:
            continue
        name = " ".join(label.split())[:200] or link
        if not any(line.endswith(link) for line in out):
            out.append(f"- {name}: {link}")
        if len(out) >= 20:
            break
    return "\n\n본문 링크:\n" + "\n".join(out) if out else ""


_WEEKDAYS = "월화수목금토일"


def when(value: object, tz: ZoneInfo) -> str:
    """Like the extension's ko-KR toLocaleString: "2026. 10. 13. (화) 오후 11:59"."""
    moment = _datetime(value)
    if moment is None:
        return ""
    local = moment.astimezone(tz)
    half = "오전" if local.hour < 12 else "오후"
    hour = local.hour % 12 or 12
    return (
        f"{local.year}. {local.month}. {local.day}. ({_WEEKDAYS[local.weekday()]}) "
        f"{half} {hour:02d}:{local.minute:02d}"
    )


def _datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else None


SUBMISSION_TYPES = {
    "online_upload": "파일 업로드",
    "online_text_entry": "텍스트 입력",
    "online_url": "웹 주소",
    "media_recording": "미디어 녹화",
    "online_quiz": "퀴즈",
    "discussion_topic": "토론 참여",
    "external_tool": "외부 도구",
    "on_paper": "오프라인(종이) 제출",
    "none": "제출 없음",
    "not_graded": "채점 없음",
    "wiki_page": "페이지 작성",
    "student_annotation": "문서에 주석 달기",
}


def submission_guide(row: dict[str, Any], tz: ZoneInfo) -> str:
    """How to hand an assignment in, from Canvas assignment fields."""
    lines: list[str] = []
    types = row.get("submission_types")
    if isinstance(types, list) and types:
        names = [SUBMISSION_TYPES.get(str(t), str(t)) for t in cast(list[object], types)]
        lines.append(f"제출 방식: {', '.join(names)}")
    extensions = row.get("allowed_extensions")
    if isinstance(extensions, list) and extensions:
        lines.append(
            f"허용 파일 형식: {', '.join(str(e) for e in cast(list[object], extensions)[:30])}"
        )
    attempts = row.get("allowed_attempts")
    if isinstance(attempts, int) and not isinstance(attempts, bool):
        lines.append(f"제출 가능 횟수: {'제한 없음' if attempts < 0 else f'{attempts}회'}")
    points = row.get("points_possible")
    if isinstance(points, int | float) and not isinstance(points, bool):
        lines.append(f"배점: {_number(points)}점")
    if row.get("due_at"):
        lines.append(f"마감: {when(row['due_at'], tz)}")
    if row.get("unlock_at") or row.get("lock_at"):
        start = when(row["unlock_at"], tz) if row.get("unlock_at") else "지금"
        end = when(row["lock_at"], tz) if row.get("lock_at") else "마감 후에도 가능"
        lines.append(f"제출 가능 기간: {start} ~ {end}")
    if row.get("locked_for_user") and row.get("lock_explanation"):
        lines.append(f"잠김: {html_text(row['lock_explanation'])[:500]}")
    return "\n\n제출 안내:\n" + "\n".join(lines) if lines else ""


def _number(value: float) -> str:
    # JavaScript prints 10.0 as "10"; keep the extension's text so hashes stay the same.
    return str(int(value)) if float(value).is_integer() else str(value)


# --- Canvas client ----------------------------------------------------------------------


class Canvas:
    """GET-only Canvas API reader for one origin and token."""

    def __init__(
        self, origin: str, token: str, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.origin = origin
        self._token = token
        self._transport = transport
        self._client = httpx.AsyncClient(
            transport=transport,
            trust_env=False,
            follow_redirects=False,
            timeout=15,
            headers={"Accept": "application/json"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _url(self, path: str) -> str:
        url = urlsplit(urljoin(self.origin + "/", path))
        if f"{url.scheme}://{url.netloc}" != self.origin or not url.path.startswith("/api/v1/"):
            raise CanvasError("LMS가 예상하지 못한 조회 주소를 반환했어요")
        return urlunsplit(url)

    async def get(self, path: str) -> tuple[Any, httpx.Response]:
        try:
            response = await self._client.get(
                self._url(path), headers={"Authorization": f"Bearer {self._token}"}
            )
        except httpx.HTTPError as exc:
            raise CanvasError("LMS에 연결하지 못했어요") from exc
        if response.status_code == 401:
            raise CanvasAuthError(
                "LMS 토큰이 만료됐거나 취소됐어요. 새 토큰을 발급해 다시 연결해 주세요"
            )
        if response.status_code != 200:
            raise CanvasError(f"LMS 조회 실패 ({response.status_code})")
        if "json" not in response.headers.get("content-type", ""):
            raise CanvasError("LMS가 JSON 대신 다른 응답을 보냈어요")
        try:
            return response.json(), response
        except ValueError as exc:
            raise CanvasError("LMS 응답 형식을 확인할 수 없어요") from exc

    async def list(self, path: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        next_path: str | None = path
        for _ in range(MAX_PAGES):
            if next_path is None:
                break
            if next_path in seen:
                raise CanvasError("LMS 페이지 목록이 반복돼요")
            seen.add(next_path)
            data, response = await self.get(next_path)
            if not isinstance(data, list):
                raise CanvasError("LMS 목록 형식을 확인할 수 없어요")
            rows.extend(
                cast(dict[str, Any], row)
                for row in cast(list[object], data)
                if isinstance(row, dict)
            )
            next_path = _next_link(response.headers.get("link", ""))
        if next_path is not None:
            raise CanvasError("조회량이 너무 많아요. 목록 전체를 가져오지 못했어요")
        return rows

    async def download(self, course_id: str, file: dict[str, Any], limit: int) -> bytes:
        """The bytes of a file the collection just listed, checked against its listing."""
        expected = int(file["size"])
        if expected < 1 or expected > limit:
            raise CanvasError("파일은 25MB까지 저장할 수 있어요")
        data, _ = await self.get(f"/api/v1/courses/{course_id}/files/{file['id']}")
        current = _object(data)
        if (
            str(current.get("id")) != str(file["id"])
            or current.get("size") != expected
            or current.get("updated_at") != file["updated_at"]
            or current.get("locked_for_user")
            or current.get("hidden_for_user")
        ):
            raise CanvasError("자료가 변경되거나 잠겼어요. 다음 동기화 때 다시 받아요")
        target: str | None = None
        try:
            preview, _ = await self.get(f"/api/v1/files/{file['id']}/public_url")
            if isinstance(public := _object(preview).get("public_url"), str):
                target = public
        except CanvasAuthError:
            raise
        except CanvasError:
            target = None  # some Canvas setups turn this off; use the download route
        if target is None or _same_origin(target, self.origin):
            download = current.get("url")
            if not isinstance(download, str) or not _same_origin(download, self.origin):
                raise CanvasError("외부 저장소의 파일은 아직 지원하지 않아요")
            target = urljoin(self.origin + "/", download)
        return await self._bytes(target, expected, limit)

    async def _bytes(self, url: str, expected: int, limit: int) -> bytes:
        """Follow at most five redirects; the token only goes to the LMS origin."""
        async with httpx.AsyncClient(
            transport=self._transport, trust_env=False, follow_redirects=False, timeout=30
        ) as client:
            for _ in range(6):
                parts = urlsplit(url)
                if parts.scheme != "https" or parts.username or parts.password:
                    raise CanvasError("학습자료 주소가 안전하지 않아요")
                headers = (
                    {"Authorization": f"Bearer {self._token}"}
                    if _same_origin(url, self.origin)
                    else {}
                )
                try:
                    async with client.stream("GET", url, headers=headers) as response:
                        if response.status_code in {301, 302, 303, 307, 308}:
                            location = response.headers.get("location")
                            if not location:
                                raise CanvasError("학습자료 다운로드에 실패했어요")
                            url = urljoin(url, location)
                            continue
                        if response.status_code != 200:
                            raise CanvasError("학습자료 다운로드에 실패했어요")
                        if "html" in response.headers.get("content-type", "").lower():
                            raise CanvasError("학습자료 대신 HTML 페이지가 반환됐어요")
                        out = io.BytesIO()
                        async for chunk in response.aiter_bytes():
                            if out.tell() + len(chunk) > min(limit, expected):
                                raise CanvasError("파일 크기가 목록과 달라요")
                            out.write(chunk)
                        if out.tell() != expected:
                            raise CanvasError("파일이 일부만 내려받아졌어요")
                        return out.getvalue()
                except httpx.HTTPError as exc:
                    # httpx errors include signed URLs; keep them out of messages.
                    raise CanvasError("자료 저장소에 연결하지 못했어요") from exc
        raise CanvasError("학습자료 주소가 너무 여러 번 바뀌었어요")


def _object(data: object) -> dict[str, Any]:
    return cast(dict[str, Any], data) if isinstance(data, dict) else {}


def _same_origin(url: str, origin: str) -> bool:
    parts = urlsplit(urljoin(origin + "/", url))
    return f"{parts.scheme}://{parts.netloc}" == origin


def _next_link(header: str) -> str | None:
    for part in header.split(","):
        if match := re.search(r'<([^>]+)>;\s*rel="next"', part):
            return match.group(1)
    return None


# --- collection: the extension's collectLms, server side ----------------------------------


@dataclass
class Collected:
    payload: dict[str, Any]
    warnings: list[str]
    files: dict[str, dict[str, Any]] = field(default_factory=lambda: {})  # item id → file


async def profile(canvas: Canvas) -> dict[str, Any]:
    data, _ = await canvas.get("/api/v1/users/self/profile")
    if not (me := _object(data)).get("id"):
        raise CanvasError("로그인 계정을 확인할 수 없어요")
    return me


async def collect(canvas: Canvas, *, materials: bool, tz: ZoneInfo) -> Collected:
    origin = canvas.origin
    me = await profile(canvas)
    raw_courses = await canvas.list(
        "/api/v1/courses?enrollment_type=student&enrollment_state=active&per_page=100"
    )
    courses = [
        {"id": str(c["id"]), "name": str(c["name"])[:200]} for c in raw_courses if c.get("name")
    ]
    items: dict[tuple[str, str | None, str], dict[str, Any]] = {}
    files: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []

    def safe_url(value: object, fallback: str) -> str:
        if isinstance(value, str) and value:
            url = urlsplit(urljoin(origin + "/", value))
            if (
                f"{url.scheme}://{url.netloc}" == origin
                and not url.username
                and len(urlunsplit(url)) <= 2000
            ):
                return urlunsplit(url)
        return urljoin(origin + "/", fallback.lstrip("/"))

    def add(
        kind: str,
        row: dict[str, Any],
        course: dict[str, str] | None,
        title: object,
        body: object,
        fallback: str,
        extra: str = "",
        posted_at: object = None,
    ) -> dict[str, Any]:
        item: dict[str, Any] = {
            "kind": kind,
            "id": str(row["id"]),
            "course_id": course["id"] if course else None,
            "title": str(title or "제목 없음")[:500],
            "text": (html_text(body) + extra).strip()[:20000],
            "url": safe_url(row.get("html_url"), fallback),
            "due_at": row.get("due_at") if kind == "assignment" else None,
        }
        if _datetime(posted_at) is not None:
            item["posted_at"] = posted_at
        # Some Canvas streams repeat an item; keep the last copy, like the extension.
        items[(kind, item["course_id"], item["id"])] = item
        return item

    for course in courses:
        cid = course["id"]
        for kind in ("assignment", "announcement"):
            try:
                path = (
                    f"/api/v1/courses/{cid}/assignments?include[]=submission&per_page=100"
                    if kind == "assignment"
                    else f"/api/v1/courses/{cid}/discussion_topics"
                    "?only_announcements=true&per_page=100"
                )
                for row in await canvas.list(path):
                    body = row.get("description") or row.get("message") or ""
                    submission = row.get("submission")
                    submitted = (
                        "\n\n제출 상태: "
                        + str(
                            cast(dict[str, Any], submission).get("workflow_state") or "알 수 없음"
                        )
                        if kind == "assignment" and isinstance(submission, dict)
                        else ""
                    )
                    extra = (
                        body_links(body, origin)
                        + (submission_guide(row, tz) if kind == "assignment" else "")
                        + submitted
                    )
                    section = "assignments" if kind == "assignment" else "discussion_topics"
                    add(
                        kind,
                        row,
                        course,
                        row.get("name") or row.get("title"),
                        body,
                        f"/courses/{cid}/{section}/{row['id']}",
                        extra,
                        row.get("posted_at") or row.get("created_at")
                        if kind == "announcement"
                        else None,
                    )
            except CanvasAuthError:
                raise
            except CanvasError as exc:
                warnings.append(
                    f"{course['name']} {'과제' if kind == 'assignment' else '공지'}: {exc}"
                )
        if materials:
            try:
                await _materials(canvas, course, add, files, warnings)
            except CanvasAuthError:
                raise
            except CanvasError as exc:
                warnings.append(f"{course['name']} 주차학습: {exc}")
    by_id = {c["id"]: c for c in courses}
    for kind, path in (
        ("activity", "/api/v1/users/self/activity_stream?only_active_courses=true&per_page=100"),
        ("conversation", "/api/v1/conversations?per_page=100"),
    ):
        try:
            for row in await canvas.list(path):
                course = by_id.get(str(row.get("course_id")))
                add(
                    kind,
                    {**row, "id": f"{row.get('type') or kind}:{row.get('id')}"},
                    course,
                    row.get("title") or row.get("subject") or row.get("type"),
                    row.get("message") or row.get("last_message"),
                    "/conversations"
                    if kind == "conversation"
                    else f"/courses/{course['id']}"
                    if course
                    else "/",
                    "",
                    row.get("last_message_at") or row.get("updated_at") or row.get("created_at"),
                )
        except CanvasAuthError:
            raise
        except CanvasError as exc:
            warnings.append(f"{'최근 활동' if kind == 'activity' else '메시지 요약'}: {exc}")
    if len(courses) > 200 or len(items) > 5000:
        raise CanvasError("수집량이 지원 범위를 초과했어요")
    if not items and warnings:
        raise CanvasError("\n".join(warnings))
    payload = {"account_id": str(me["id"]), "courses": courses, "items": list(items.values())}
    return Collected(payload, warnings, files)


async def _materials(
    canvas: Canvas,
    course: dict[str, str],
    add: Callable[..., dict[str, Any]],
    files: dict[str, dict[str, Any]],
    warnings: list[str],
) -> None:
    cid = course["id"]
    base = f"/api/v1/courses/{cid}/modules"
    for module in await canvas.list(f"{base}?include[]=items&per_page=100"):
        listed = module.get("items")
        if isinstance(listed, list) and len(cast(list[object], listed)) == module.get(
            "items_count"
        ):
            rows = [_object(row) for row in cast(list[object], listed)]
        else:
            rows = await canvas.list(f"{base}/{module['id']}/items?per_page=100")
        for row in rows:
            if row.get("type") != "File":
                continue  # videos and external tools are never launched
            try:
                content_id = str(row.get("content_id"))
                if not content_id.isdigit():
                    raise CanvasError("파일 식별자가 올바르지 않아요")
                data, _ = await canvas.get(f"/api/v1/courses/{cid}/files/{content_id}")
                file = _object(data)
                if file.get("locked_for_user") or file.get("hidden_for_user"):
                    continue
                size = file.get("size")
                if not isinstance(size, int) or size <= 0 or size > FILE_LIMIT:
                    raise CanvasError("파일은 25MB까지 저장할 수 있어요")
                if (
                    str(file.get("id")) != content_id
                    or not file.get("updated_at")
                    or not file.get("display_name")
                ):
                    raise CanvasError("파일 정보를 확인할 수 없어요")
                item = add(
                    "material",
                    row,
                    course,
                    row.get("title") or file["display_name"],
                    f"주차/단원: {module.get('name')}",
                    f"/courses/{cid}/modules/items/{row['id']}",
                )
                item["file"] = {
                    "id": content_id,
                    "name": str(file["display_name"])[:200],
                    "size": size,
                    "updated_at": file["updated_at"],
                }
                files[item["id"]] = item["file"]
                # Its place in the feed: when the file was uploaded.
                uploaded = file.get("created_at") or file.get("updated_at")
                if _datetime(uploaded) is not None:
                    item["posted_at"] = uploaded
            except CanvasAuthError:
                raise
            except CanvasError as exc:
                warnings.append(f"{course['name']} {row.get('title') or '학습자료'}: {exc}")


# --- the background worker ----------------------------------------------------------------

TransportFactory = Callable[[], httpx.AsyncBaseTransport | None]


@dataclass
class SyncStatus:
    running: bool = False
    last_sync_at: datetime | None = None
    last_error: str | None = None
    warnings: list[str] = field(default_factory=lambda: [])
    last_result: dict[str, int] | None = None


class LmsSync:
    """Runs the token sync on a timer and on demand, one at a time."""

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        settings: Callable[[], Settings],
        summaries: Callable[[], LmsSummaries],
        keychain: Keychain,
        lock: asyncio.Lock,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._settings = settings
        self._summaries = summaries
        self.keychain = keychain
        self.lock = lock  # shared with the extension endpoints: one LMS writer at a time
        self.transport: TransportFactory = lambda: None  # tests serve a fake Canvas here
        self.status = SyncStatus()
        self._run_lock = asyncio.Lock()
        self._loop: asyncio.Task[None] | None = None
        self._tasks: set[asyncio.Task[SyncStatus]] = set()

    def canvas(self, origin: str, token: str) -> Canvas:
        return Canvas(origin, token, self.transport())

    async def connection(self, session: AsyncSession) -> tuple[str, str] | None:
        saved: dict[str, Any] | None = await services.get_setting(session, CANVAS_SETTING)
        if not saved:
            return None
        token = await asyncio.to_thread(self.keychain.get, saved["url"])
        return (saved["url"], token) if token else None

    def start_soon(self) -> None:
        task = asyncio.create_task(self.run())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def run(self) -> SyncStatus:
        async with self._run_lock:
            self.status.running = True
            try:
                async with self._sessionmaker() as session:
                    found = await self.connection(session)
                    if found is None:
                        return self.status
                    materials = bool(await services.get_setting(session, MATERIALS_SETTING))
                canvas = self.canvas(*found)
                try:
                    await self._sync(canvas, materials)
                finally:
                    await canvas.aclose()
            except CanvasError as exc:
                self.status.last_error = str(exc)
            except Exception:
                log.exception("LMS token sync failed")
                self.status.last_error = "동기화 중 오류가 났어요. 서버 로그를 확인하세요"
            finally:
                self.status.running = False
                self.status.last_sync_at = datetime.now(UTC)
            return self.status

    async def _sync(self, canvas: Canvas, materials: bool) -> None:
        # Imported here: argos.lms imports this module for its routes.
        from argos.lms import LmsImport

        config = self._settings()
        collected = await collect(canvas, materials=materials, tz=config.zoneinfo)
        host = urlsplit(canvas.origin).hostname
        batch = LmsImport.model_validate(collected.payload, context={"hosts": {host}})
        items = [item.model_dump() for item in batch.items]
        summaries = self._summaries()
        limit = min(config.attachment_max_mb, 25) * 1024 * 1024
        warnings = list(collected.warnings)
        async with self.lock, self._sessionmaker() as session:
            min_chars = (
                await services.lms_summary_min_chars(session, config.lms_summary_min_chars)
                if summaries.available()
                else 0
            )
            result = await services.import_lms(
                session,
                account_id=batch.account_id,
                courses=[course.model_dump() for course in batch.courses],
                items=items,
                summary_min_chars=min_chars,
                origin=canvas.origin,
            )
        summaries.enqueue(result.pop("summarize"))
        for item in items:
            if item["kind"] != "material":
                continue
            async with self._sessionmaker() as session:
                needed = await services.lms_material_needed(
                    session, batch.account_id, item, config.attachments_dir
                )
            if not needed:
                continue
            try:
                data = await canvas.download(item["course_id"], collected.files[item["id"]], limit)
                async with self.lock, self._sessionmaker() as session:
                    await services.import_lms_material(
                        session,
                        batch.account_id,
                        item,
                        io.BytesIO(data),
                        config.attachments_dir,
                        limit,
                    )
            except CanvasAuthError:
                raise
            except (
                CanvasError,
                services.InvalidError,
                services.ConflictError,
                services.TooLargeError,
            ) as exc:
                warnings.append(f"{item['title']}: {exc}")
        self.status.last_result = result
        self.status.last_error = None
        self.status.warnings = warnings[:20]

    def start(self) -> None:
        async def loop() -> None:
            while True:
                await self.run()
                await asyncio.sleep(self._settings().lms_poll_minutes * 60)

        self._loop = asyncio.create_task(loop())

    async def stop(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self._loop is not None:
            self._loop.cancel()
            try:
                await self._loop
            except asyncio.CancelledError:
                pass
