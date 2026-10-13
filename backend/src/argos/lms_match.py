"""Which of the user's own course channels is this LMS course? Asked once per LMS
course whose name matched none (e.g. LMS "기계학습" and the user's "머신러닝"), with the
Inbox classifier's chat model. A match becomes a merge suggestion the user approves
in the feed; nothing merges on the model's word alone."""

import logging
import re
from collections.abc import Sequence
from typing import Protocol

from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from argos import services
from argos.config import Settings
from argos.models import ApprovalStatus
from argos.summarizer import classifier_chat

log = logging.getLogger(__name__)

ASKED = "lms_merge_asked"  # ids of LMS course channels already asked about

PROMPT = (
    "학교 LMS의 과목 이름 하나와, 학생이 직접 만든 과목 채널 이름 목록이 있다. "
    "정확히 같은 과목을 가리키는 채널이 있으면(번역, 줄임말, 한국어/영어 표기 차이만 다름) "
    "그 채널 이름을 목록에 적힌 그대로 한 줄로만 써라. "
    "분야가 비슷하거나 관련된 다른 과목은 같은 과목이 아니다"
    "(예: 기계학습과 머신러닝은 같고, 인공지능과 머신러닝은 다르다). "
    "확실하지 않거나 없으면 NONE만 써라. 다른 말은 쓰지 마라."
)


class MatchError(Exception):
    pass


class CourseMatcher(Protocol):
    async def match(self, course: str, channels: Sequence[str]) -> str | None: ...


def parse_match(content: str, channels: Sequence[str]) -> str | None:
    """The channel the model named, only if it is one of `channels` exactly."""
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.S)
    for line in content.splitlines():
        name = line.strip().removeprefix("- ").strip().strip("\"'`“”‘’").strip()
        if name:
            return name if name in channels else None
    return None


class OpenAIMatcher:
    def __init__(self, client: AsyncOpenAI, model: str, effort: str | None) -> None:
        self._client = client
        self.model = model
        self.extra: dict[str, str] = {"reasoning_effort": effort} if effort else {}

    async def match(self, course: str, channels: Sequence[str]) -> str | None:
        listing = "\n".join(f"- {name}" for name in channels)
        try:
            response = await self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": PROMPT},
                    {"role": "user", "content": f"LMS 과목: {course}\n채널 목록:\n{listing}"},
                ],
                temperature=0,
                extra_body=self.extra,
            )
            content = response.choices[0].message.content or ""
        except Exception as exc:
            raise MatchError(f"match request failed: {type(exc).__name__}") from exc
        return parse_match(content, channels)


def build_matcher(settings: Settings) -> CourseMatcher | None:
    chat = classifier_chat(settings)
    return OpenAIMatcher(*chat) if chat else None


async def suggest_merges(session: AsyncSession, matcher: CourseMatcher) -> int:
    """Asks about each LMS course not asked about yet while the user has course
    channels of their own; returns how many merge suggestions were posted. A failed
    request is asked again at the next sync."""
    # A channel already offered in a suggestion waiting for the user is not offered again.
    offered = {
        approval.payload_json.get("target_channel_id")
        for approval in await services.list_approvals(session, ApprovalStatus.PENDING)
        if approval.action == "merge_channels"
    }
    targets = [t for t in await services.lms_merge_targets(session) if t.id not in offered]
    if not targets:
        return 0
    asked: list[str] = list(await services.get_setting(session, ASKED) or [])
    made = 0
    for link, channel in await services.lms_channels(session):
        if link.container_name == services.LMS_INBOX or channel.id in asked or not targets:
            continue
        try:
            name = await matcher.match(
                link.container_name or channel.name, [t.name for t in targets]
            )
        except MatchError as exc:
            log.warning("LMS course match for %s failed: %s", channel.id, exc)
            continue
        asked.append(channel.id)
        await services.set_setting_quietly(session, ASKED, asked)
        target = next((t for t in targets if t.name == name), None)
        if target is not None:
            await services.request_lms_merge(session, channel, target)
            targets.remove(target)
            made += 1
    return made
