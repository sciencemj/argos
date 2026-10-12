# 아침 요약 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 매일 설정 시각 이후 첫 확인 때 Argos 정보를 모아 고른 에이전트가 #today 스레드에 아침 브리핑을 쓰고 메신저로도 보내며, 실패하면 규칙 기반 목록으로 대신한다.

**Architecture:** `notify.py`에 데이터 수집(`morning_digest_data`), 프롬프트(`digest_prompt`), 대체 목록(`digest_markdown`), 생성기(`MorningDigest`)를 둔다. `Notifier.run()`이 15분마다 `MorningDigest.start_if_due`를 부르고, 생성은 별도 태스크에서 `Runner.run_turn(no_tools=True)`로 한다. 설정은 `app_setting`(`OVERRIDABLE`) + `/settings/notify` 확장, 화면은 `OpsSettings.tsx`의 `NotifySection`.

**Tech Stack:** FastAPI, async SQLAlchemy(SQLite), asyncio, Argos Runner/에이전트 어댑터, `hermes send`, React 19 + TanStack Query, vitest.

**Spec:** `docs/superpowers/specs/2026-10-12-morning-digest-design.md`

## Global Constraints

- 쓰기는 `services.py` 헬퍼(`create_message`, `update_message`, `start_run` 등)로만. actor는 `system`.
- UI 문구 한국어 + 영어(`tr("한국어", "English")`), 코드·식별자·커밋 메시지 영어. 서버가 만드는 문구는 `settings.language`(ko/en)를 따른다.
- 테스트는 실제 에이전트·Hermes 없이: `fake_agents(...)`로 `app.state.runner.adapter_factory`를, `notifier.send`를 가짜 함수로 바꾼다.
- 기본 꺼짐(`morning_digest_enabled=False`), 기본 7시, 정오(12시) 이후엔 그날 자동 요약 없음. 하루 한 번(`digest:YYYY-MM-DD`).
- 데이터 상한: 마감 15개, LMS 10개, LMS 원문 미리보기 200자. 프롬프트는 "20줄 이내", "데이터에 없는 것 지어내지 마라".
- 메신저 대상(`notify_hermes_target`)이 없으면 보내지 않는다. 메신저 실패는 알림 행의 `send_error`에만.
- API 모델 변경 → `make api-types`. `make lint`·`make test` 통과. 커밋은 Task마다(CLAUDE.md).

## Review Focus

1. 생성 중(에이전트가 몇십 초 답하는 동안) 다음 15분 확인이 또 돎 → 두 번째 요약이 시작되지 않아야 한다. (Task 3 `test_digest_starts_once_while_running`)
2. 고른 에이전트가 삭제됐거나 이름이 틀림 → 대체 목록이 오고 오류로 서버가 멈추지 않는다. (Task 3 `test_missing_agent_falls_back_to_the_list`)
3. 에이전트가 빈 답을 내고 끝남(`done`이지만 본문 없음) → 빈 메시지를 메신저로 보내지 않고 대체 목록. (Task 3 `test_empty_answer_falls_back`)
4. 시간대 경계: 현지 6:59/7:00/11:59/12:00 → 7:00~11:59에만 시작. (Task 3 `test_digest_window`)
5. 데이터가 비어 있는 새 사용자 → 프롬프트·대체 목록이 비어 있는 섹션 없이 짧게 나온다. (Task 2 `test_empty_day_digest_markdown`)

---

### Task 1: 설정 필드와 `/settings/notify` 확장

**Files:**
- Modify: `backend/src/argos/config.py` (`notify_digest_hour` 근처)
- Modify: `backend/src/argos/classifier.py` (`OVERRIDABLE`, 약 650행)
- Modify: `backend/src/argos/api.py` (`NotifySettingsOut`/`NotifySettingsIn` 약 2015행, `_notify_settings` 2099행, `put_notify_settings` 2115행)
- Modify: `frontend/src/api-types.ts` (생성)
- Test: `backend/tests/test_notify.py`

**Interfaces:**
- Produces: `Settings.morning_digest_enabled: bool = False`, `Settings.morning_digest_hour: int = 7`, `Settings.morning_digest_agent: str = ""`. `NotifySettingsOut`에 같은 세 필드. `NotifySettingsIn`에 `morning_digest_enabled: bool | None = None`, `morning_digest_hour: int | None = Field(default=None, ge=0, le=23)`, `morning_digest_agent: str | None = Field(default=None, max_length=40)` (None = 그대로).

- [ ] **Step 1: 실패하는 테스트** — `tests/test_notify.py` 끝에:

```python
def test_morning_digest_settings(app: TestClient) -> None:
    current = app.get("/api/v1/settings/notify").json()
    assert (
        current["morning_digest_enabled"],
        current["morning_digest_hour"],
        current["morning_digest_agent"],
    ) == (False, 7, "")
    base = {"hermes_target": None, "weekly_review_weekday": 6, "weekly_review_hour": 20}
    saved = app.put(
        "/api/v1/settings/notify",
        json=base | {"morning_digest_enabled": True, "morning_digest_hour": 8,
                     "morning_digest_agent": "claude"},
    ).json()
    assert (saved["morning_digest_enabled"], saved["morning_digest_hour"]) == (True, 8)
    assert saved["morning_digest_agent"] == "claude"
    # Older clients leave the digest fields out: they stay as they were.
    assert app.put("/api/v1/settings/notify", json=base).json()["morning_digest_hour"] == 8
    bad_hour = app.put("/api/v1/settings/notify", json=base | {"morning_digest_hour": 24})
    assert bad_hour.status_code == 422
    unknown = app.put("/api/v1/settings/notify", json=base | {"morning_digest_agent": "nobody"})
    assert unknown.status_code == 422
```

- [ ] **Step 2: 실패 확인** — `cd backend && uv run pytest tests/test_notify.py::test_morning_digest_settings -q` → FAIL `KeyError: 'morning_digest_enabled'`

- [ ] **Step 3: 구현**

`config.py`, `notify_digest_hour` 줄 아래:

```python
    # Morning digest (PLAN Phase 15b): an agent writes the day's briefing into #today
    # (and the messenger) once after this hour. Off until the user turns it on.
    morning_digest_enabled: bool = False
    morning_digest_hour: int = 7
    morning_digest_agent: str = ""  # agent name; empty: the default agent
```

`classifier.py` `OVERRIDABLE`에 `"morning_digest_enabled", "morning_digest_hour", "morning_digest_agent",` 추가.

`api.py`:

```python
class NotifySettingsOut(BaseModel):
    ...  # 기존 필드
    morning_digest_enabled: bool
    morning_digest_hour: int
    morning_digest_agent: str


class NotifySettingsIn(BaseModel):
    ...  # 기존 필드
    # None: leave as is (clients from before Phase 15b don't send these).
    morning_digest_enabled: bool | None = None
    morning_digest_hour: int | None = Field(default=None, ge=0, le=23)
    morning_digest_agent: str | None = Field(default=None, max_length=40)
```

`_notify_settings`에 세 필드(`config.morning_digest_*`) 추가. `put_notify_settings`에서 기존 `set_setting` 세 줄 다음:

```python
    if body.morning_digest_agent is not None:
        name = body.morning_digest_agent.strip()
        if name and await services.find_agent(session, name) is None:
            raise services.InvalidError("등록되지 않은 에이전트예요")
        await services.set_setting(session, "morning_digest_agent", name, USER)
    if body.morning_digest_enabled is not None:
        await services.set_setting(
            session, "morning_digest_enabled", body.morning_digest_enabled, USER
        )
    if body.morning_digest_hour is not None:
        await services.set_setting(session, "morning_digest_hour", body.morning_digest_hour, USER)
```

- [ ] **Step 4: 통과 확인** — 같은 테스트 + `uv run pytest tests/test_notify.py tests/test_settings.py -q`, `uv run pyright` → PASS, 0 errors. 그다음 `make -C .. api-types`.

- [ ] **Step 5: Commit** — `git add backend/src/argos/config.py backend/src/argos/classifier.py backend/src/argos/api.py backend/tests/test_notify.py frontend/src/api-types.ts && git commit -m "feat: morning digest settings"`

---

### Task 2: 데이터 수집, 프롬프트, 대체 목록

**Files:**
- Modify: `backend/src/argos/notify.py` (`roundup_candidates` 109행에서 쿼리 두 개 추출, `briefing_markdown` 아래에 새 함수)
- Test: `backend/tests/test_digest.py` (새 파일)

**Interfaces:**
- Consumes: `briefing(session, now, settings)`, `LmsNotice`, `SourceLink`, `services.get_today`
- Produces:
  - `async def stale_inbox(session, now, settings) -> list[InboxItem]`, `async def undated_tasks(session, now, settings) -> list[Task]` (roundup과 공유)
  - `async def morning_digest_data(session, now, settings) -> dict[str, Any]` — `briefing()` 키 + `"lms": list[dict]`, `"attention": {"overlaps": list[list[str]], "overdue": int, "undated": int, "stale_inbox": int}`
  - `def digest_prompt(data: dict[str, Any], language: str) -> str`
  - `def digest_markdown(data: dict[str, Any], reason: str | None, language: str) -> str`
  - 상수 `DIGEST_TASKS = 15`, `DIGEST_LMS = 10`, `DIGEST_PREVIEW = 200`

- [ ] **Step 1: 실패하는 테스트** — `backend/tests/test_digest.py`:

`import_lms`·`create_task`는 실제 현재 시각으로 `created_at`을 찍으므로, 테스트의 기준 시각 `NOW`도 실제 현재 시각으로 잡는다. 일정은 오늘(서울) 낮 시각에 둔다.

```python
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from argos import notify, services
from argos.config import Settings
from argos.models import Channel, ChannelKind, Event, LmsNotice

NOW = datetime.now(UTC).replace(microsecond=0)
LONG = "중간고사 범위는 3장부터 7장까지입니다. " * 20


async def course(session: AsyncSession) -> Channel:
    channel = Channel(name="자료구조", kind=ChannelKind.COURSE)
    session.add(channel)
    await session.commit()
    return channel


async def lms(session: AsyncSession, item_id: str, text: str) -> None:
    await services.import_lms(
        session,
        account_id="42",
        courses=[{"id": "7", "name": "알고리즘"}],
        items=[
            {
                "id": item_id,
                "course_id": "7",
                "kind": "announcement",
                "title": f"공지 {item_id}",
                "text": text,
                "url": "https://lms.example/1",
                "due_at": None,
                "file": None,
            }
        ],
    )


def seoul_today(settings: Settings, hour: int) -> datetime:
    local = NOW.astimezone(settings.zoneinfo)
    return local.replace(hour=hour, minute=0, second=0).astimezone(UTC)


async def test_digest_data_collects_today_lms_and_attention(
    session: AsyncSession, settings: Settings
) -> None:
    channel = await course(session)
    start = seoul_today(settings, 14)
    session.add_all(
        [
            Event(channel_id=channel.id, title="세미나", starts_at=start,
                  ends_at=start + timedelta(hours=2)),
            Event(channel_id=channel.id, title="면담", starts_at=start + timedelta(hours=1),
                  ends_at=start + timedelta(hours=3)),
        ]
    )  # fmt: skip
    await session.commit()
    await services.create_task(
        session, channel_id=channel.id, title="지난 과제", due_at=NOW - timedelta(hours=3),
        actor="u",
    )  # fmt: skip
    await lms(session, "a1", LONG)
    data = await notify.morning_digest_data(session, NOW, settings)
    assert data["attention"]["overlaps"] == [["세미나", "면담"]]
    assert data["attention"]["overdue"] == 1
    assert [item["title"] for item in data["lms"]] == ["공지 a1"]
    assert data["lms"][0]["text"] == LONG[: notify.DIGEST_PREVIEW]
    assert data["lms"][0]["summary"] is None


async def test_digest_prefers_summaries_and_skips_old_lms(
    session: AsyncSession, settings: Settings
) -> None:
    await lms(session, "a1", LONG)
    notice = await session.scalar(select(LmsNotice))
    assert notice is not None
    notice.summary_state, notice.summary = "done", "- 3~7장"
    await session.commit()
    data = await notify.morning_digest_data(session, NOW + timedelta(minutes=1), settings)
    assert data["lms"][0]["summary"] == "- 3~7장" and data["lms"][0]["text"] is None
    later = await notify.morning_digest_data(session, NOW + timedelta(days=2), settings)
    assert later["lms"] == []


async def test_digest_caps_lms_items(session: AsyncSession, settings: Settings) -> None:
    for i in range(notify.DIGEST_LMS + 3):
        await lms(session, f"n{i:02d}", "내용 " * 10)
    data = await notify.morning_digest_data(session, NOW + timedelta(minutes=1), settings)
    assert len(data["lms"]) == notify.DIGEST_LMS


async def test_digest_prompt_carries_data_and_language(
    session: AsyncSession, settings: Settings
) -> None:
    data = await notify.morning_digest_data(session, NOW, settings)
    prompt = notify.digest_prompt(data, "ko")
    assert "지어내지 마라" in prompt
    assert json.loads(prompt.split("\n\n", 1)[1]) == data
    assert "Do not invent" in notify.digest_prompt(data, "en")


async def test_empty_day_digest_markdown(session: AsyncSession, settings: Settings) -> None:
    data = await notify.morning_digest_data(session, NOW, settings)
    text = notify.digest_markdown(data, "응답 시간이 초과됐어요", "ko")
    assert text.startswith("AI 요약을 만들지 못했어요 (응답 시간이 초과됐어요)")
    assert "## 새 LMS 소식" not in text and "## 챙길 것" not in text
```

`Event`·`Channel` 생성자와 `services.create_task` 인자는 `tests/test_services.py`가 쓰는 형태를 따른다(필수 필드가 더 있으면 그쪽에 맞춘다). 시드 데이터(`seed.example.toml`)가 날짜 없는 할 일이나 인박스를 만들면 `attention` 숫자 단언을 그에 맞춘다.

- [ ] **Step 2: 실패 확인** — `uv run pytest tests/test_digest.py -q` → FAIL `AttributeError: module 'argos.notify' has no attribute 'morning_digest_data'`

- [ ] **Step 3: 구현** — `notify.py`:

`roundup_candidates`의 두 쿼리를 함수로 뽑고 원래 자리에서 호출:

```python
async def stale_inbox(session: AsyncSession, now: datetime, settings: Settings) -> list[InboxItem]:
    return list(
        (
            await session.scalars(
                select(InboxItem).where(
                    InboxItem.status.in_(OPEN_INBOX),
                    InboxItem.created_at < now - timedelta(hours=settings.inbox_stale_hours),
                )
            )
        ).all()
    )


async def undated_tasks(session: AsyncSession, now: datetime, settings: Settings) -> list[Task]:
    return list(
        (
            await session.scalars(
                select(Task).where(
                    Task.status.in_([TaskStatus.TODO, TaskStatus.IN_PROGRESS]),
                    Task.due_at.is_(None),
                    Task.created_at < now - timedelta(days=settings.undated_after_days),
                )
            )
        ).all()
    )
```

`briefing_markdown` 아래:

```python
DIGEST_TASKS = 15
DIGEST_LMS = 10
DIGEST_PREVIEW = 200


def _overlaps(events: list[Event]) -> list[list[str]]:
    timed = sorted(
        (e for e in events if not e.all_day and e.starts_at and e.ends_at),
        key=lambda e: cast(datetime, e.starts_at),
    )
    pairs: list[list[str]] = []
    for i, a in enumerate(timed):
        for b in timed[i + 1 :]:
            assert a.ends_at and b.starts_at
            if b.starts_at < a.ends_at:
                pairs.append([a.title, b.title])
    return pairs


async def morning_digest_data(
    session: AsyncSession, now: datetime, settings: Settings
) -> dict[str, Any]:
    """What the morning digest is written from (PLAN Phase 15b): the briefing, the last
    day's LMS news and things to tidy up. Capped so the prompt stays small."""
    data = await briefing(session, now, settings)
    data["due_tasks"] = data["due_tasks"][:DIGEST_TASKS]
    names = {c.id: c.name for c in (await session.scalars(select(Channel))).all()}
    since = now - timedelta(hours=24)
    news: list[tuple[datetime, dict[str, Any]]] = []
    rows = await session.execute(
        select(LmsNotice, Message.channel_id)
        .join(Message, Message.id == LmsNotice.message_id)
        .where(LmsNotice.created_at >= since, LmsNotice.created_at <= now)
    )
    for notice, channel_id in rows.tuples():
        summary = notice.summary if notice.summary_state == "done" else None
        news.append((notice.created_at, {
            "channel": names.get(channel_id), "kind": notice.kind, "title": notice.title,
            "summary": summary, "text": None if summary else notice.text[:DIGEST_PREVIEW],
        }))  # fmt: skip
    tasks = await session.scalars(
        select(Task)
        .join(SourceLink, SourceLink.object_id == Task.id)
        .where(
            SourceLink.source == "learningx",
            SourceLink.object_type == "task",
            Task.created_at >= since,
            Task.created_at <= now,
        )
    )
    for task in tasks.all():
        due = task.due_at.astimezone(settings.zoneinfo).isoformat() if task.due_at else None
        news.append((task.created_at, {
            "channel": names.get(task.channel_id), "kind": "assignment", "title": task.title,
            "due_at": due,
        }))  # fmt: skip
    news.sort(key=lambda pair: pair[0], reverse=True)
    data["lms"] = [item for _, item in news[:DIGEST_LMS]]
    today = await services.get_today(
        session, now=now, tz=settings.zoneinfo, due_soon_days=settings.due_soon_days
    )
    data["attention"] = {
        "overlaps": _overlaps(list(today["events"])),
        "overdue": sum(1 for t in data["due_tasks"] if t["overdue"]),
        "undated": len(await undated_tasks(session, now, settings)),
        "stale_inbox": len(await stale_inbox(session, now, settings)),
    }
    return data


DIGEST_PROMPT = {
    "ko": (
        "아래 데이터만으로 오늘 아침 브리핑을 써라. 첫 줄은 오늘 가장 중요한 것 한 문장. 그다음 "
        "`## 오늘`, `## 새 LMS 소식`, `## 챙길 것` 순서로 쓰고 비어 있는 섹션은 빼라. 데이터에 없는 "
        "일정이나 마감은 지어내지 마라. 전체 20줄 이내, 한국어로."
    ),
    "en": (
        "Write this morning's briefing from the data below only. First line: the one most "
        "important thing today. Then `## Today`, `## New from the LMS`, `## To tidy up`, "
        "leaving out empty sections. Do not invent events or deadlines that are not in the "
        "data. At most 20 lines, in English."
    ),
}


def digest_prompt(data: dict[str, Any], language: str) -> str:
    return (
        DIGEST_PROMPT.get(language, DIGEST_PROMPT["ko"])
        + "\n\n"
        + json.dumps(data, ensure_ascii=False)
    )


def digest_markdown(data: dict[str, Any], reason: str | None, language: str) -> str:
    """The digest without an agent: the same data as a plain list."""
    en = language == "en"
    lines: list[str] = []
    if reason:
        lines += [
            f"Could not write the AI digest ({reason})" if en
            else f"AI 요약을 만들지 못했어요 ({reason})",
            "",
        ]  # fmt: skip
    lines.append(briefing_markdown(data))
    news = cast(list[dict[str, Any]], data.get("lms") or [])
    if news:
        lines += ["", "## New from the LMS" if en else "## 새 LMS 소식"]
        for item in news:
            where = f" (#{item['channel']})" if item.get("channel") else ""
            lines.append(f"- {item['title']}{where}")
            if item.get("summary"):
                lines += [f"  {line}" for line in str(item["summary"]).splitlines()]
    attention = cast(dict[str, Any], data.get("attention") or {})
    tidy = [
        *(
            (f"Overlapping: {a} / {b}" if en else f"시간 겹침: {a} / {b}")
            for a, b in attention.get("overlaps", [])
        ),
        *(
            [f"{attention['undated']} tasks without a date" if en
             else f"날짜 없는 할 일 {attention['undated']}개"]
            if attention.get("undated") else []
        ),
        *(
            [f"{attention['stale_inbox']} Inbox items waiting" if en
             else f"오래된 인박스 {attention['stale_inbox']}개"]
            if attention.get("stale_inbox") else []
        ),
    ]  # fmt: skip
    if tidy:
        lines += ["", "## To tidy up" if en else "## 챙길 것", *(f"- {t}" for t in tidy)]
    return "\n".join(lines)
```

import에 `Event`, `LmsNotice`, `Message`, `SourceLink`를 models 목록에 추가.

- [ ] **Step 4: 통과 확인** — `uv run pytest tests/test_digest.py tests/test_notify.py -q && uv run pyright src/argos/notify.py tests/test_digest.py` → PASS (roundup 테스트 포함), 0 errors

- [ ] **Step 5: Commit** — `git add backend/src/argos/notify.py backend/tests/test_digest.py && git commit -m "feat: collect the morning digest's data and its fallback list"`

---

### Task 3: `MorningDigest` 생성기와 Notifier 연결, "지금 보내기"

**Files:**
- Modify: `backend/src/argos/notify.py` (`Notifier` 560행 부근, 새 클래스)
- Modify: `backend/src/argos/main.py` (Notifier 생성에 runner 전달)
- Modify: `backend/src/argos/api.py` (`POST /notify/digest`)
- Modify: `frontend/src/api-types.ts` (생성)
- Test: `backend/tests/test_digest.py`

**Interfaces:**
- Consumes: Task 1 설정, Task 2 함수, `services.find_agent/get_message/update_message/start_run/get_run/create_message`, `Runner.run_turn`, `record`, `Candidate`
- Produces:
  - `class MorningDigest(sessionmaker, settings: Callable[[], Settings], runner: Runner | None, send: Callable[[], Callable[[Settings, str, str], Any]])` with `def due(now) -> bool`, `async def start_if_due(session, now) -> None`, `async def start(now, *, manual: bool = False) -> str` (루트 메시지 id), `async def wait() -> None`, `async def stop() -> None`
  - `Notifier.__init__(..., runner: "Runner | None" = None)`, `Notifier.digest: MorningDigest`
  - `POST /api/v1/notify/digest` → `{"message_id": str}`
  - `DIGEST_LATEST_HOUR = 12`

- [ ] **Step 1: 실패하는 테스트** — `tests/test_digest.py` 끝에(클라이언트 기반):

```python
from collections.abc import Iterator

import pytest
from fakes import FakeAgent, fake_agents
from fastapi.testclient import TestClient

from argos.main import create_app


@pytest.fixture
def app(settings: Settings) -> Iterator[TestClient]:
    on = settings.model_copy(update={"morning_digest_enabled": True})
    with TestClient(create_app(on)) as client:
        yield client


def seoul(client: TestClient, hour: int, minute: int = 0) -> datetime:
    tz = client.app.state.settings.zoneinfo  # type: ignore[attr-defined]
    return datetime.now(UTC).astimezone(tz).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    ).astimezone(UTC)


def check(client: TestClient, now: datetime) -> None:
    notifier = client.app.state.notifier  # type: ignore[attr-defined]
    client.portal.call(notifier.run, now)  # type: ignore[union-attr]
    client.portal.call(notifier.digest.wait)  # type: ignore[union-attr]


def sends(client: TestClient, target: str | None = "discord") -> list[str]:
    sent: list[str] = []

    async def fake_send(config: Settings, to: str, text: str) -> str | None:
        sent.append(text)
        return None

    client.app.state.notifier.send = fake_send  # type: ignore[attr-defined]
    if target:
        client.put("/api/v1/settings/notify", json={
            "hermes_target": target, "weekly_review_weekday": 6, "weekly_review_hour": 20,
        })  # fmt: skip
    return sent


def today_thread(client: TestClient) -> list[dict[str, Any]]:
    channels = client.get("/api/v1/channels").json()["channels"]
    today = next(c for c in channels if c["name"] == "today")
    items = client.get(f"/api/v1/channels/{today['id']}/messages").json()["items"]
    root = next(m for m in items if "아침 요약" in m["body"])
    replies = client.get(f"/api/v1/messages/{root['id']}/thread").json()
    return [root, *replies["replies"]]


def test_agent_writes_the_digest_into_today_and_the_messenger(app: TestClient) -> None:
    hermes = FakeAgent("hermes", ["오늘은 ", "세미나가 있어요"])
    app.app.state.runner.adapter_factory = fake_agents(hermes=hermes)  # type: ignore[attr-defined]
    sent = sends(app)
    check(app, seoul(app, 7, 5))
    root, reply = today_thread(app)
    assert reply["body"] == "오늘은 세미나가 있어요" and reply["author_type"] == "agent"
    assert root["sticky_agent_id"] is not None
    assert sent == ["오늘은 세미나가 있어요"]
    assert hermes.no_tools == [True]
    assert "지어내지 마라" in hermes.transcripts[0][0].text


def test_missing_agent_falls_back_to_the_list(app: TestClient) -> None:
    app.app.state.runner.adapter_factory = fake_agents()  # every agent unavailable
    sent = sends(app)
    check(app, seoul(app, 8))
    root, fallback = today_thread(app)
    assert fallback["author_type"] == "system"
    assert fallback["body"].startswith("AI 요약을 만들지 못했어요")
    assert sent == [fallback["body"]]


def test_empty_answer_falls_back(app: TestClient) -> None:
    app.app.state.runner.adapter_factory = fake_agents(hermes=FakeAgent("hermes", []))  # type: ignore[attr-defined]
    sent = sends(app)
    check(app, seoul(app, 8))
    *_, last = today_thread(app)
    assert last["author_type"] == "system" and sent == [last["body"]]


def test_digest_window(app: TestClient) -> None:
    app.app.state.runner.adapter_factory = fake_agents(hermes=FakeAgent("hermes", ["안녕"]))  # type: ignore[attr-defined]
    sends(app, target=None)
    for hour, minute in ((6, 59), (12, 0)):
        check(app, seoul(app, hour, minute))
    titles = [n["title"] for n in app.get("/api/v1/notifications").json()["items"]]
    assert "아침 요약이 도착했어요" not in titles
    check(app, seoul(app, 11, 59))
    check(app, seoul(app, 11, 59))  # once a day
    titles = [n["title"] for n in app.get("/api/v1/notifications").json()["items"]]
    assert titles.count("아침 요약이 도착했어요") == 1


def test_digest_starts_once_while_running(app: TestClient) -> None:
    import asyncio

    hold = asyncio.Event()
    slow = FakeAgent("hermes", ["느린 ", "답"], hold=hold)
    app.app.state.runner.adapter_factory = fake_agents(hermes=slow)  # type: ignore[attr-defined]
    sends(app, target=None)
    notifier = app.app.state.notifier  # type: ignore[attr-defined]
    app.portal.call(notifier.run, seoul(app, 7, 5))  # type: ignore[union-attr]
    app.portal.call(notifier.run, seoul(app, 7, 20))  # type: ignore[union-attr]
    app.portal.call(hold.set)  # type: ignore[union-attr]
    app.portal.call(notifier.digest.wait)  # type: ignore[union-attr]
    assert len(slow.transcripts) == 1


def test_off_means_no_digest(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        sends(client, target=None)
        check(client, seoul(client, 8))
        titles = [n["title"] for n in client.get("/api/v1/notifications").json()["items"]]
        assert "아침 요약이 도착했어요" not in titles


def test_send_now_does_not_block_the_morning_digest(app: TestClient) -> None:
    app.app.state.runner.adapter_factory = fake_agents(hermes=FakeAgent("hermes", ["지금"]))  # type: ignore[attr-defined]
    sends(app, target=None)
    response = app.post("/api/v1/notify/digest")
    assert response.status_code == 200 and response.json()["message_id"]
    app.portal.call(app.app.state.notifier.digest.wait)  # type: ignore[attr-defined]
    check(app, seoul(app, 8))
    titles = [n["title"] for n in app.get("/api/v1/notifications").json()["items"]]
    assert titles.count("아침 요약이 도착했어요") == 2
```

(위 블록의 import는 파일 맨 위 import 목록으로 옮긴다(ruff E402). `/api/v1/messages/{id}/thread`의 실제 경로·응답 키는 `tests/test_chat.py`의 스레드 조회 테스트를 보고 맞춘다. `hold.set`을 portal로 부르는 대신 `threading`이 필요하면 `test_agents.py`의 `test_cancel_stops_the_run`이 hold를 푸는 방식을 따른다. `fake_agents()`의 빈 호출 타입 오류는 `# type: ignore[attr-defined]`를 붙인다.)

- [ ] **Step 2: 실패 확인** — `uv run pytest tests/test_digest.py -q` → 새 테스트 FAIL(`AttributeError: 'Notifier' object has no attribute 'digest'`)

- [ ] **Step 3: 구현** — `notify.py`:

상단 import에 `from typing import TYPE_CHECKING`, `from argos.agents import Turn`, models에 `AgentRun`·`RunStatus` 불필요(services로 읽음) — `RunStatus`만 추가. 그리고:

```python
if TYPE_CHECKING:
    from argos.runner import Runner

DIGEST_LATEST_HOUR = 12  # a "morning" digest that would land after noon is skipped


class MorningDigest:
    """The morning digest (PLAN Phase 15b): a #today thread the chosen agent writes into,
    then the same text to the messenger. Falls back to the plain list when the agent
    can't answer, so a digest always arrives."""

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        settings: Callable[[], Settings],
        runner: "Runner | None",
        send: Callable[[], Callable[[Settings, str, str], Any]],
    ) -> None:
        self._sessionmaker = sessionmaker
        self._settings = settings
        self._runner = runner
        self._send = send
        self._tasks: set[asyncio.Task[None]] = set()

    def due(self, now: datetime) -> bool:
        config = self._settings()
        hour = now.astimezone(config.zoneinfo).hour
        return config.morning_digest_enabled and (
            config.morning_digest_hour <= hour < DIGEST_LATEST_HOUR
        )

    def _key(self, now: datetime) -> str:
        return f"digest:{now.astimezone(self._settings().zoneinfo).date().isoformat()}"

    async def start_if_due(self, session: AsyncSession, now: datetime) -> None:
        """Called from Notifier.run under its lock, so two passes never both start."""
        if not self.due(now):
            return
        if await session.scalar(select(Notification.id).where(Notification.key == self._key(now))):
            return
        await self.start(now, session=session)

    async def start(
        self, now: datetime, *, manual: bool = False, session: AsyncSession | None = None
    ) -> str:
        config = self._settings()
        local = now.astimezone(config.zoneinfo)
        key = self._key(now) + (f":manual:{uuid.uuid4().hex[:8]}" if manual else "")
        heading = (
            f"☀️ {local:%B} {local.day} morning digest" if config.language == "en"
            else f"☀️ {local.month}월 {local.day}일({WEEKDAY_KO[local.weekday()]}) 아침 요약"
        )  # fmt: skip
        async with self._session(session) as db:
            today = await db.scalar(
                select(Channel).where(Channel.name == "today", Channel.kind == ChannelKind.SYSTEM)
            )
            assert today is not None  # seed_defaults always makes #today
            root = await services.create_message(
                db, channel_id=today.id, body=heading, author_type=AuthorType.SYSTEM,
                actor="system",
            )  # fmt: skip
            [notice] = await record(db, [Candidate(
                key, "morning_digest",
                "Your morning digest is ready" if config.language == "en"
                else "아침 요약이 도착했어요",
                None, "message", root.id, today.id,
            )])  # fmt: skip
        task = asyncio.create_task(self._write(notice.id, root.id, now))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return root.id

    def _session(self, session: AsyncSession | None) -> Any:
        if session is not None:
            return contextlib.nullcontext(session)
        return self._sessionmaker()

    async def _write(self, notice_id: str, root_id: str, now: datetime) -> None:
        config = self._settings()
        reason: str | None = None
        text: str | None = None
        try:
            async with self._sessionmaker() as session:
                data = await morning_digest_data(session, now, config)
                agent = await services.find_agent(
                    session, config.morning_digest_agent or config.default_agent
                )
                root = await services.get_message(session, root_id)
            if agent is None or self._runner is None:
                reason = "에이전트를 찾을 수 없어요" if config.language != "en" else "no agent"
            else:
                async with self._sessionmaker() as session:
                    await services.update_message(
                        session, root_id, {"sticky_agent_id": agent.id}, "system"
                    )
                    run, reply = await services.start_run(
                        session, agent=agent, channel_id=root.channel_id, trigger=root,
                        reply_thread_root_id=root_id, actor="system", kind="digest",
                    )  # fmt: skip
                await self._runner.run_turn(
                    run.id, agent, reply.id,
                    [Turn("user", digest_prompt(data, config.language))],
                    "아침 요약" if config.language != "en" else "Morning digest",
                    f"morning-{data['date']}", no_tools=True,
                )  # fmt: skip
                async with self._sessionmaker() as session:
                    finished = await services.get_run(session, run.id)
                    answer = (await services.get_message(session, reply.id)).body.strip()
                if finished.status == RunStatus.DONE and answer:
                    text = answer
                else:
                    reason = finished.error or (
                        "응답이 비었어요" if config.language != "en" else "empty answer"
                    )
            if text is None:
                text = digest_markdown(data, reason, config.language)
                async with self._sessionmaker() as session:
                    await services.create_message(
                        session, channel_id=root.channel_id, body=text,
                        author_type=AuthorType.SYSTEM, thread_root_id=root_id, actor="system",
                    )  # fmt: skip
        except Exception:
            log.exception("morning digest failed")
            return
        if config.notify_hermes_target:
            error = await self._send()(config, config.notify_hermes_target, text)
            async with self._sessionmaker() as session:
                notice = await session.get(Notification, notice_id)
                if notice is not None:
                    notice.sent_at = None if error else datetime.now(UTC)
                    notice.send_error = error
                    await session.commit()

    async def wait(self) -> None:
        """For tests and shutdown: until the digests being written are done."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def stop(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*list(self._tasks), return_exceptions=True)
```

`import contextlib`, `import uuid` 추가. `Notifier.__init__`에 `runner: "Runner | None" = None` 인자를 받고:

```python
        self.digest = MorningDigest(sessionmaker, settings, runner, lambda: self.send)
```

`Notifier.run`의 `if review_due(...)` 블록 다음(같은 세션, 잠금 안):

```python
                await self.digest.start_if_due(session, now)
```

`Notifier.stop()` 끝에 `await self.digest.stop()`.

`main.py`: `notify.Notifier(app.state.sessionmaker, lambda: app.state.settings, runner=app.state.runner)`.

`api.py`(알림 라우트 근처):

```python
class DigestStarted(BaseModel):
    message_id: str


@router.post("/notify/digest")
async def send_digest_now(request: Request) -> DigestStarted:
    """Writes a morning digest right away (settings' "지금 보내기"), even when it is off
    or today's already went out; the scheduled one still comes."""
    notifier: notify.Notifier = request.app.state.notifier
    return DigestStarted(message_id=await notifier.digest.start(datetime.now(UTC), manual=True))
```

- [ ] **Step 4: 통과 확인** — `uv run pytest tests/test_digest.py tests/test_notify.py tests/test_debate.py -q && uv run pyright && uv run pytest -q` → 모두 PASS. `make -C .. api-types`.

- [ ] **Step 5: Commit** — `git add backend/src/argos/notify.py backend/src/argos/main.py backend/src/argos/api.py backend/tests/test_digest.py frontend/src/api-types.ts && git commit -m "feat: write the morning digest with an agent into #today and the messenger"`

---

### Task 4: 설정 화면 (알림 섹션)

**Files:**
- Modify: `frontend/src/api.ts` (`useSaveNotify` 아래 `useSendDigest`)
- Modify: `frontend/src/pages/OpsSettings.tsx` (`NotifySection`, 약 38~193행)
- Create: `frontend/src/pages/NotifySection.test.tsx`

**Interfaces:**
- Consumes: `NotifySettingsOut/In`의 `morning_digest_*`, `POST /api/v1/notify/digest`, `useAgents()`
- Produces: `export const useSendDigest = () => useWrite("message", () => call(client.POST("/api/v1/notify/digest")))`

- [ ] **Step 1: 실패하는 테스트** — `frontend/src/pages/NotifySection.test.tsx`:

```tsx
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { NotifySection } from "./OpsSettings";

const mocks = vi.hoisted(() => ({
  settings: { data: {} as Record<string, unknown> },
  save: { mutate: vi.fn(), error: null },
  send: { mutate: vi.fn(), isPending: false, isSuccess: false, error: null },
}));
vi.mock("../api", () => ({
  useNotifySettings: () => mocks.settings,
  useSaveNotify: () => mocks.save,
  useTestNotify: () => ({ mutate: vi.fn(), error: null }),
  useNotifyTargets: () => ({ data: [], error: null }),
  useSendDigest: () => mocks.send,
  useAgents: () => ({
    data: [
      { name: "hermes", display_name: "Hermes" },
      { name: "claude", display_name: "Claude" },
    ],
  }),
  useBackupKeep: vi.fn(),
  useBackupNow: vi.fn(),
  useInstallService: vi.fn(),
  useOps: vi.fn(),
  usePrepareUninstall: vi.fn(),
  useUninstallService: vi.fn(),
}));
vi.mock("../desktop", () => ({ inDesktopApp: () => false }));

afterEach(cleanup);
beforeEach(() => {
  localStorage.setItem("argos-language", "ko");
  mocks.save.mutate.mockReset();
  mocks.settings.data = {
    hermes_target: null,
    hermes_available: false,
    weekly_review_weekday: 6,
    weekly_review_hour: 20,
    digest_hour: 9,
    last_run: null,
    morning_digest_enabled: false,
    morning_digest_hour: 7,
    morning_digest_agent: "",
  };
});

test("turning the morning digest on keeps the other notice settings", () => {
  render(<NotifySection />);
  fireEvent.click(screen.getByRole("checkbox", { name: /아침 요약/ }));
  expect(mocks.save.mutate).toHaveBeenCalledWith(
    expect.objectContaining({
      morning_digest_enabled: true,
      weekly_review_hour: 20,
    }),
  );
});

test("hour and agent pickers save their choice", () => {
  render(<NotifySection />);
  fireEvent.change(screen.getByLabelText("아침 요약 시각"), {
    target: { value: "8" },
  });
  expect(mocks.save.mutate).toHaveBeenLastCalledWith(
    expect.objectContaining({ morning_digest_hour: 8 }),
  );
  fireEvent.change(screen.getByLabelText("아침 요약 에이전트"), {
    target: { value: "claude" },
  });
  expect(mocks.save.mutate).toHaveBeenLastCalledWith(
    expect.objectContaining({ morning_digest_agent: "claude" }),
  );
});

test("send now asks for a digest", () => {
  render(<NotifySection />);
  fireEvent.click(screen.getByRole("button", { name: "지금 보내기" }));
  expect(mocks.send.mutate).toHaveBeenCalled();
});
```

(`OpsSettings.tsx`가 import하는 다른 훅이 더 있으면 mock 목록에 `vi.fn()`으로 추가.)

- [ ] **Step 2: 실패 확인** — `cd frontend && bunx vitest run src/pages/NotifySection.test.tsx` → FAIL(체크박스 없음)

- [ ] **Step 3: 구현**

`api.ts`, `useMakeReview` 아래:

```ts
export const useSendDigest = () =>
  useWrite("message", () => call(client.POST("/api/v1/notify/digest")));
```

`OpsSettings.tsx`: import에 `useAgents`, `useSendDigest`. `NotifySection` 안:

```tsx
  const agents = useAgents();
  const sendDigest = useSendDigest();
```

`change`가 보내는 body에 세 필드 추가:

```tsx
      morning_digest_enabled:
        patch.morning_digest_enabled ?? data.morning_digest_enabled,
      morning_digest_hour: patch.morning_digest_hour ?? data.morning_digest_hour,
      morning_digest_agent:
        patch.morning_digest_agent ?? data.morning_digest_agent,
```

주간 리뷰 블록(`<div className="flex flex-col gap-1">` … `</div>`) 다음, `<ErrorText` 앞:

```tsx
      <div className="flex flex-col gap-2">
        <label className="flex items-start gap-2 text-sm">
          <input
            type="checkbox"
            className="mt-1 accent-[var(--ink)]"
            checked={data.morning_digest_enabled}
            onChange={(e) =>
              change({ morning_digest_enabled: e.target.checked })
            }
          />
          <span>
            {tr("아침 요약", "Morning digest")}
            <span className="block text-xs text-meta">
              {tr(
                "매일 아침 일정·마감·LMS 소식·챙길 것을 고른 에이전트가 정리해 오늘 채널과 메신저로 보내요. 정오가 지나면 그날은 건너뛰어요.",
                "Each morning the chosen agent sums up events, deadlines, LMS news and things to tidy up, in Today and the messenger. Skipped after noon.",
              )}
            </span>
          </span>
        </label>
        <div className="flex flex-wrap items-center gap-2 text-[13.5px] text-text">
          <select
            aria-label={tr("아침 요약 시각", "Morning digest time")}
            className={`${field} min-w-24 flex-1`}
            value={data.morning_digest_hour}
            onChange={(e) =>
              change({ morning_digest_hour: Number(e.target.value) })
            }
          >
            {HOURS.filter((h) => h < 12).map((h) => (
              <option key={h} value={h}>
                {h}
                {tr("시")}
              </option>
            ))}
          </select>
          <select
            aria-label={tr("아침 요약 에이전트", "Morning digest agent")}
            className={`${field} min-w-32 flex-1`}
            value={data.morning_digest_agent}
            onChange={(e) => change({ morning_digest_agent: e.target.value })}
          >
            <option value="">{tr("기본 에이전트", "Default agent")}</option>
            {(agents.data ?? []).map((a) => (
              <option key={a.name} value={a.name}>
                {a.display_name}
              </option>
            ))}
          </select>
          <button
            type="button"
            className={btn}
            disabled={sendDigest.isPending}
            onClick={() => sendDigest.mutate()}
          >
            {tr("지금 보내기", "Send now")}
          </button>
        </div>
      </div>
```

`ErrorText`의 error 체인에 `?? sendDigest.error` 추가. 시각 선택은 0~11시만(정오 이후는 그날 건너뛰므로 고를 수 없게) — 백엔드는 0~23을 받되 화면이 11시까지만 보여 준다.

- [ ] **Step 4: 통과 확인** — `bunx vitest run src/pages/NotifySection.test.tsx && bunx tsc --noEmit -p . && bunx biome check src/pages/OpsSettings.tsx src/pages/NotifySection.test.tsx src/api.ts` → PASS

- [ ] **Step 5: Commit** — `git add frontend/src/api.ts frontend/src/pages/OpsSettings.tsx frontend/src/pages/NotifySection.test.tsx && git commit -m "feat: morning digest controls in notice settings"`

---

### Task 5: 문서와 전체 확인

**Files:**
- Modify: `CLAUDE.md` (백엔드 목록 `summarizer.py` 항목 아래)
- Modify: `docs/decisions.md` (구현 중 생긴 결정이 있으면)
- Modify: `docs/superpowers/specs/2026-10-12-morning-digest-design.md` (구현과 달라진 곳)

- [ ] **Step 1: CLAUDE.md** — 추가:

```
- **`notify.py` 아침 요약**(Phase 15b): `Notifier.run()`이 `MorningDigest.start_if_due`를 불러 설정 시각~정오 사이 하루 한 번
  #today에 루트 메시지를 만들고(알림 키 `digest:날짜`), 별도 태스크에서 고른 에이전트를 `run_turn(no_tools=True)`로
  실행해 스레드에 쓰게 한 뒤 같은 글을 `hermes send`로 보낸다. 실패하면 `digest_markdown` 목록. "지금 보내기"는
  `POST /notify/digest`.
```

- [ ] **Step 2: 전체 확인** — `make lint && make test && cd backend && uv run alembic check` → 모두 통과 (모델 변경 없음: `alembic check`는 "No new upgrade operations detected")

- [ ] **Step 3: Commit** — `git add CLAUDE.md docs/decisions.md docs/superpowers/specs/2026-10-12-morning-digest-design.md && git commit -m "docs: morning digest notes"`
