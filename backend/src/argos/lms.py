"""Read-only LMS imports from a paired extension. No school login credentials cross here."""

import asyncio
import hashlib
import io
import logging
import secrets
from datetime import UTC, datetime
from typing import Annotated, Literal
from urllib.parse import unquote, urlsplit

import httpx2 as httpx
from fastapi import APIRouter, Depends, Form, Header, Request, UploadFile
from pydantic import (
    AwareDatetime,
    BaseModel,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from starlette.exceptions import HTTPException

from argos import lms_extension, services
from argos.api import Config, Session

router = APIRouter(prefix="/api/v1/lms", tags=["lms"])
TOKEN_KEY = "lms_extension_token_hash"
HOSTS = {"lms.korea.ac.kr", "mylms.korea.ac.kr"}
STORAGE_HOST = "kr.object.gov-ncloudstorage.com"
sync_lock = asyncio.Lock()


class _StorageLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Desktop logs httpx2 at INFO. Its URL argument otherwise persists signatures.
        if isinstance(record.args, tuple):
            record.args = tuple(
                value.copy_with(query=None)
                if isinstance(value, httpx.URL) and value.host == STORAGE_HOST
                else value
                for value in record.args
            )
        return True


logging.getLogger("httpx2").addFilter(_StorageLogFilter())


def local_settings_request(request: Request) -> None:
    """Pairing stays on Argos' own local UI; no cross-site token minting."""
    if request.url.hostname not in {"127.0.0.1", "localhost", "testserver"}:
        raise HTTPException(403, "로컬 Argos에서 연결해 주세요")
    origin = request.headers.get("origin")
    if origin and (
        urlsplit(origin).scheme not in {"http", "https"}
        or urlsplit(origin).hostname not in {"127.0.0.1", "localhost"}
    ):
        raise HTTPException(403, "로컬 Argos에서 연결해 주세요")


async def paired(session: Session, authorization: Annotated[str | None, Header()] = None) -> None:
    saved = await services.get_setting(session, TOKEN_KEY)
    supplied = authorization[7:] if authorization and authorization.startswith("Bearer ") else ""
    digest = hashlib.sha256(supplied.encode()).hexdigest()
    if not saved or not secrets.compare_digest(str(saved), digest):
        raise HTTPException(401, "확장 프로그램을 Argos에 다시 연결해 주세요")


class LmsStatus(BaseModel):
    connected: bool
    last_sync: str | None = None
    extension_path: str | None = None
    extension_version: str | None = None
    extension_seen_at: str | None = None
    extension_revision: str | None = None
    extension_manual_update: bool = False
    clean_course_names: bool = True
    courses: list["LmsChannel"] = []


class LmsChannel(BaseModel):
    channel_id: str
    original: str
    name: str


class LmsCourseNames(BaseModel):
    enabled: bool


class LmsExtensionCheck(BaseModel):
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    permissions: str = Field(pattern=r"^[a-f0-9]{64}$")


class LmsExtensionUpdate(BaseModel):
    revision: str | None = None
    reload: bool = False
    manual_update: bool = False


class LmsToken(BaseModel):
    token: str


class LmsCourse(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)


class LmsFile(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    size: int = Field(ge=1, le=25 * 1024 * 1024)
    updated_at: AwareDatetime


class LmsItem(BaseModel):
    id: str = Field(min_length=1, max_length=300)
    kind: Literal[
        "assignment", "announcement", "activity", "conversation", "attendance", "material"
    ]
    course_id: str | None = Field(default=None, max_length=100)
    title: str = Field(min_length=1, max_length=500)
    text: str = Field(default="", max_length=20000)
    url: str = Field(max_length=2000)
    due_at: AwareDatetime | None = None
    file: LmsFile | None = None

    @model_validator(mode="after")
    def material_file(self) -> "LmsItem":
        if self.kind == "material" and (self.file is None or self.course_id is None):
            raise ValueError("학습자료의 과목과 파일 정보가 필요해요")
        if self.kind != "material" and self.file is not None:
            raise ValueError("학습자료만 파일을 포함할 수 있어요")
        return self

    @field_validator("url")
    @classmethod
    def school_url(cls, value: str) -> str:
        url = urlsplit(value)
        if (
            url.scheme != "https"
            or url.hostname not in HOSTS
            or url.username
            or url.password
            or url.port not in {None, 443}
        ):
            raise ValueError("고려대 LMS 주소가 필요해요")
        return value


class LmsImport(BaseModel):
    account_id: str = Field(min_length=1, max_length=100)
    courses: list[LmsCourse] = Field(max_length=200)
    items: list[LmsItem] = Field(max_length=5000)

    @model_validator(mode="after")
    def unique_and_known(self) -> "LmsImport":
        courses = {course.id for course in self.courses}
        keys = {(item.kind, item.course_id, item.id) for item in self.items}
        if len(courses) != len(self.courses) or len(keys) != len(self.items):
            raise ValueError("중복된 LMS 항목이에요")
        if any(item.course_id is not None and item.course_id not in courses for item in self.items):
            raise ValueError("과목 정보가 없는 LMS 항목이에요")
        return self


class LmsImportResult(BaseModel):
    created: int
    updated: int
    unchanged: int


class LmsMaterialUpload(BaseModel):
    account_id: str = Field(min_length=1, max_length=100)
    item: LmsItem

    @model_validator(mode="after")
    def material_only(self) -> "LmsMaterialUpload":
        if self.item.kind != "material":
            raise ValueError("학습자료가 필요해요")
        return self


class LmsMaterialsNeeded(BaseModel):
    item_ids: list[str]


class LmsMaterialDownload(LmsMaterialUpload):
    download_url: str = Field(max_length=8192, repr=False)

    @model_validator(mode="after")
    def school_storage(self) -> "LmsMaterialDownload":
        url = urlsplit(self.download_url)
        path = unquote(url.path)
        assert self.item.file is not None
        if (
            url.scheme != "https"
            or url.hostname != STORAGE_HOST
            or url.username
            or url.password
            or url.port not in {None, 443}
            or not path.startswith("/korea-canvas-contents/")
            or any(part in {".", ".."} for part in path.split("/"))
            or self.item.file.id not in path.split("/")
        ):
            raise ValueError("학교 학습자료 저장소 주소가 필요해요")
        return self


@router.get("/status", dependencies=[Depends(local_settings_request)])
async def status(session: Session, config: Config) -> LmsStatus:
    build = await asyncio.to_thread(lms_extension.installed, config.data_dir)
    seen: dict[str, str] = await services.get_setting(session, "lms_extension_seen") or {}
    return LmsStatus(
        connected=bool(await services.get_setting(session, TOKEN_KEY)),
        last_sync=await services.get_setting(session, "lms_last_sync"),
        extension_path=str(lms_extension.install_dir(config.data_dir)) if build else None,
        extension_version=build["version"] if build else None,
        extension_seen_at=seen.get("at"),
        extension_revision=seen.get("revision"),
        extension_manual_update=bool(
            build and seen and build["permissions"] != seen.get("permissions")
        ),
        clean_course_names=await services.lms_clean_names(session),
        courses=[
            LmsChannel(channel_id=channel.id, original=link.container_name or "", name=channel.name)
            for link, channel in await services.lms_channels(session)
        ],
    )


@router.put("/course-names", dependencies=[Depends(local_settings_request)])
async def course_names(body: LmsCourseNames, session: Session, config: Config) -> LmsStatus:
    async with sync_lock:
        await services.set_lms_clean_names(session, body.enabled, "user")
    return await status(session, config)


@router.post("/extension/prepare", dependencies=[Depends(local_settings_request)])
async def prepare_extension(session: Session, config: Config) -> LmsStatus:
    async with sync_lock:
        try:
            await asyncio.to_thread(lms_extension.prepare, config.data_dir)
        except (OSError, ValueError) as exc:
            raise HTTPException(500, "확장 프로그램 파일을 준비하지 못했어요") from exc
    return await status(session, config)


@router.post("/extension/check")
async def check_extension(
    body: LmsExtensionCheck,
    session: Session,
    config: Config,
    authorization: Annotated[str | None, Header()] = None,
) -> LmsExtensionUpdate:
    async with sync_lock:
        await paired(session, authorization)
        build = await asyncio.to_thread(lms_extension.installed, config.data_dir)
        await services.set_setting_quietly(
            session,
            "lms_extension_seen",
            {"at": datetime.now(UTC).isoformat(), **body.model_dump()},
        )
        if not build:
            return LmsExtensionUpdate()
        manual = body.permissions != build["permissions"]
        return LmsExtensionUpdate(
            revision=build["revision"],
            reload=not manual and body.revision != build["revision"],
            manual_update=manual,
        )


@router.post("/connection", dependencies=[Depends(local_settings_request)])
async def connect(session: Session) -> LmsToken:
    token = secrets.token_urlsafe(32)
    async with sync_lock:
        await services.set_setting_quietly(
            session, TOKEN_KEY, hashlib.sha256(token.encode()).hexdigest()
        )
        await services.set_setting_quietly(session, "lms_extension_seen", None)
    return LmsToken(token=token)


@router.delete("/connection", dependencies=[Depends(local_settings_request)])
async def disconnect(session: Session) -> LmsStatus:
    async with sync_lock:
        await services.set_setting_quietly(session, TOKEN_KEY, None)
        await services.set_setting_quietly(session, "lms_extension_seen", None)
    return LmsStatus(connected=False)


@router.post("/import")
async def import_data(
    body: LmsImport,
    session: Session,
    authorization: Annotated[str | None, Header()] = None,
) -> LmsImportResult:
    async with sync_lock:
        # Check after acquiring the write lock so rotation revokes queued imports too.
        await paired(session, authorization)
        return LmsImportResult(
            **await services.import_lms(
                session,
                account_id=body.account_id,
                courses=[course.model_dump() for course in body.courses],
                items=[item.model_dump() for item in body.items],
            )
        )


@router.post("/materials/needed")
async def materials_needed(
    body: LmsImport,
    session: Session,
    config: Config,
    authorization: Annotated[str | None, Header()] = None,
) -> LmsMaterialsNeeded:
    async with sync_lock:
        await paired(session, authorization)
        ids: list[str] = []
        for item in body.items:
            if item.kind == "material" and await services.lms_material_needed(
                session, body.account_id, item.model_dump(), config.attachments_dir
            ):
                ids.append(item.id)
        return LmsMaterialsNeeded(item_ids=ids)


@router.post("/materials/file")
async def material_file(
    session: Session,
    config: Config,
    metadata: Annotated[str, Form(max_length=30000)],
    file: UploadFile,
    authorization: Annotated[str | None, Header()] = None,
) -> LmsImportResult:
    async with sync_lock:
        await paired(session, authorization)
        try:
            body = LmsMaterialUpload.model_validate_json(metadata)
        except ValidationError as exc:
            raise HTTPException(422, "학습자료 정보를 확인해 주세요") from exc
        return LmsImportResult(
            **await services.import_lms_material(
                session,
                body.account_id,
                body.item.model_dump(),
                file.file,
                config.attachments_dir,
                min(config.attachment_max_mb, 25) * 1024 * 1024,
            )
        )


@router.post("/materials/download")
async def material_download(
    body: LmsMaterialDownload,
    session: Session,
    config: Config,
    authorization: Annotated[str | None, Header()] = None,
) -> LmsImportResult:
    """Read a file-specific signed storage URL, without school cookies or redirects."""
    async with sync_lock:
        await paired(session, authorization)
        item = body.item.model_dump()
        source = io.BytesIO()
        if await services.lms_material_needed(
            session, body.account_id, item, config.attachments_dir
        ):
            limit = min(config.attachment_max_mb, 25) * 1024 * 1024
            assert body.item.file is not None
            try:
                # No ambient proxies, credentials, cookies, or school/Argos auth headers.
                async with httpx.AsyncClient(
                    trust_env=False, follow_redirects=False, timeout=30
                ) as client:
                    async with client.stream("GET", body.download_url) as response:
                        if response.status_code != 200:
                            raise HTTPException(
                                502, "자료 다운로드 주소가 만료됐거나 사용할 수 없어요"
                            )
                        if "html" in response.headers.get("content-type", "").lower():
                            raise HTTPException(502, "학습자료 대신 HTML 페이지가 반환됐어요")
                        async for chunk in response.aiter_bytes():
                            if source.tell() + len(chunk) > min(limit, body.item.file.size):
                                raise services.TooLargeError(
                                    "학습자료 크기가 저장 한도를 초과했어요"
                                )
                            source.write(chunk)
            except httpx.HTTPError as exc:
                # httpx errors include signed URLs. Never expose them in the API response.
                raise HTTPException(
                    502, "자료 저장소에 연결하지 못했어요. 다시 수집해 주세요"
                ) from exc
        source.seek(0)
        return LmsImportResult(
            **await services.import_lms_material(
                session,
                body.account_id,
                item,
                source,
                config.attachments_dir,
                min(config.attachment_max_mb, 25) * 1024 * 1024,
            )
        )
