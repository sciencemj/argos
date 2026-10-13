"""A course's LMS material as agent context (PLAN Phase 16d).

Assignments (tasks) and notices (messages with an `lms_notice` card) imported from the
LMS, found by channel and ranked against the user's question. No index or model: a
course holds hundreds of items at most, so character bigrams over title and body are
enough and work for Korean without a tokenizer (particles stay glued to words).
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from argos import services
from argos.models import LmsNotice, Message, SourceLink, Task

KIND_KO = {
    "assignment": "과제",
    "announcement": "공지",
    "activity": "최근 활동",
    "conversation": "메시지",
    "attendance": "출결",
    "material": "학습자료",
}


@dataclass(frozen=True)
class LmsDoc:
    kind: str  # LmsItem kind; "notice" for cards imported before Phase 15a
    title: str
    text: str  # the original LMS text (not the AI summary)
    url: str
    date: datetime | None  # assignments: the deadline; notices: when posted (or imported)
    channel_id: str
    summary: str | None = None

    def label(self, language: str = "ko") -> str:
        if language == "en":
            return self.kind
        return KIND_KO.get(self.kind, "LMS")

    def date_text(self, tz: ZoneInfo, language: str = "ko") -> str:
        if self.date is None:
            return ""
        local = self.date.astimezone(tz)
        if self.kind == "assignment":
            return (
                f"due {local:%Y-%m-%d %H:%M}"
                if language == "en"
                else f"마감 {local:%Y-%m-%d %H:%M}"
            )
        return f"{local:%Y-%m-%d}"


async def course_docs(session: AsyncSession, channel_ids: list[str] | None = None) -> list[LmsDoc]:
    """LMS items of the given channels (all channels when None), newest first."""
    docs: list[LmsDoc] = []
    tasks = select(Task, SourceLink.container).join(
        SourceLink,
        (SourceLink.object_id == Task.id)
        & (SourceLink.source == "learningx")
        & (SourceLink.object_type == "task"),
    )
    if channel_ids is not None:
        tasks = tasks.where(Task.channel_id.in_(channel_ids))
    for task, url in (await session.execute(tasks)).tuples():
        parts = services.split_lms_body(task.description or "")
        title, text, link = parts if parts else (task.title, task.description or "", url or "")
        docs.append(
            LmsDoc(
                kind="assignment",
                title=task.title or title,
                text=text,
                url=link or url or "",
                date=task.due_at,
                channel_id=task.channel_id,
            )
        )
    notices = select(LmsNotice, Message).join(Message, Message.id == LmsNotice.message_id)
    if channel_ids is not None:
        notices = notices.where(Message.channel_id.in_(channel_ids))
    for notice, message in (await session.execute(notices)).tuples():
        docs.append(
            LmsDoc(
                kind=notice.kind or "notice",
                title=notice.title,
                text=notice.text,
                url=notice.url,
                date=notice.posted_at or message.created_at,
                channel_id=message.channel_id,
                summary=notice.summary,
            )
        )
    docs.sort(key=lambda d: d.date or datetime.min.replace(tzinfo=UTC), reverse=True)
    return docs


_SPACE = re.compile(r"\s+")


def _bigrams(text: str) -> set[str]:
    words = _SPACE.split(text.lower())
    grams: set[str] = set()
    for word in words:
        if len(word) == 1:
            grams.add(word)
        grams.update(word[i : i + 2] for i in range(len(word) - 1))
    return grams


def rank(docs: list[LmsDoc], query: str, limit: int) -> list[LmsDoc]:
    """Best matches first (title counts triple); ties and an empty query keep the
    newest-first order. Items sharing nothing with the query are left out."""
    wanted = _bigrams(query)
    if not wanted:
        return docs[:limit]
    scored: list[tuple[float, int, LmsDoc]] = []
    for index, doc in enumerate(docs):
        title = _bigrams(doc.title)
        body = _bigrams(doc.text)
        score = sum(3.0 if g in title else 1.0 if g in body else 0.0 for g in wanted)
        if score > 0:
            scored.append((score / len(wanted), index, doc))
    scored.sort(key=lambda row: (-row[0], row[1]))
    return [doc for _, _, doc in scored[:limit]]


def context_section(
    docs: list[LmsDoc],
    question: str,
    budget: int,
    tz: ZoneInfo,
    language: str = "ko",
) -> str:
    """For the agent's system context: the passages most related to the question in
    full (as far as `budget` allows), then a list of other recent items to cite."""
    if not docs or budget <= 0:
        return ""
    en = language == "en"
    head = (
        "LMS material for this course (cite title, date and link of what you use; "
        "use the search_course_lms tool for anything not here):"
        if en
        else "이 과목의 LMS 자료(답에 쓴 자료는 제목·날짜·링크를 밝혀라. "
        "여기 없는 내용은 search_course_lms 도구로 찾아라):"
    )
    lines = [head]
    used = len(head)
    shown: set[int] = set()
    related = rank(docs, question, 4) if question.strip() else []
    for doc in related:
        when = doc.date_text(tz, language)
        meta = f"[{doc.label(language)}] {doc.title}" + (f" ({when})" if when else "")
        room = budget - used - len(meta) - len(doc.url) - 8
        if room < 200:
            break
        body = doc.text.strip()
        if len(body) > room:
            body = body[: room - 1] + "…"
        block = f"### {meta}\n{doc.url}\n{body}"
        lines.append(block)
        used += len(block) + 1
        shown.add(id(doc))
    listed: list[str] = []
    title = "Other recent items:" if en else "그 밖의 최근 자료:"
    used += len(title) + 1
    for doc in [d for d in docs if id(d) not in shown][:15]:
        when = doc.date_text(tz, language)
        line = f"- [{doc.label(language)}] {doc.title}" + (f" ({when})" if when else "")
        line += f" {doc.url}"
        if used + len(line) + 1 > budget:
            break
        listed.append(line)
        used += len(line) + 1
    if listed:
        lines += [title, *listed]
    return "\n".join(lines)[:budget]
