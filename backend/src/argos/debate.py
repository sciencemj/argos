"""Multi-agent debates (PLAN Phase 10).

`/debate @a @b [@c @d] topic` opens a thread; agents take turns in it. Each speaker gets
the whole shared record with speakers marked (`[codex]: …`, `[사용자]: …`), so anything
the user writes in the thread is part of the debate from the next turn on.

- round_robin: everyone once per round, in the order given
- pro_con: two agents, the first argues for and the second against, alternating
- moderated: the moderator (the channel's /ask agent) picks each next speaker and may
  close the debate early

Limits: the number of rounds, a time budget for the whole debate, the per-turn agent
timeout, and the user's cancel. Claude and Codex speak without Argos tools unless the
debate was started with --tools. At the end the moderator writes a summary and a
conclusion, which the debate card can turn into a task or a note."""

import asyncio
import logging
import re
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from argos import services
from argos.agents import AgentUnavailable, Token, Turn
from argos.hub import hub
from argos.models import (
    Agent,
    AuthorType,
    Debate,
    DebateMode,
    DebateStatus,
    Message,
    RunStatus,
)

if TYPE_CHECKING:
    from argos.runner import Runner

log = logging.getLogger(__name__)

USER = "사용자"
MODE_TEXT = {
    DebateMode.ROUND_ROBIN: "돌아가며 말하기",
    DebateMode.PRO_CON: "찬반 토론",
    DebateMode.MODERATED: "사회자 진행",
}


async def debate_record(session: AsyncSession, root_id: str) -> list[tuple[str, str]]:
    """(speaker, text) in order. A debate opened inside an existing thread starts from
    that thread's conversation (e.g. the document or code under review); then the
    agents' turns and the user's interjections. /debate commands themselves are left
    out."""
    root = await session.get(Message, root_id)
    rows = await session.scalars(
        select(Message)
        .where(Message.thread_root_id == root_id)
        .order_by(Message.created_at, Message.id)
    )
    record: list[tuple[str, str]] = []
    for m in [*([root] if root is not None else []), *rows.all()]:
        if m.author_type == AuthorType.SYSTEM or not m.body.strip() or m.ref_type == "debate":
            continue
        speaker = m.author_id if m.author_type == AuthorType.AGENT and m.author_id else USER
        record.append((speaker, m.body.strip()))
    return record


def render_record(record: list[tuple[str, str]]) -> str:
    if not record:
        return "(아직 발언이 없어요. 네가 처음이다.)"
    return "\n\n".join(f"[{speaker}]: {text}" for speaker, text in record)


def turn_context(debate: Debate, agent: Agent, names: dict[str, str], stance: str | None) -> str:
    others = ", ".join(f"{names[p]}(@{p})" for p in debate.participants_json if p != agent.name)
    lines = [
        f'너는 Argos 앱의 토론에 참여한 에이전트 "{agent.display_name}"(@{agent.name})다.',
        f"토론 주제: {debate.topic}",
        f"방식: {MODE_TEXT[DebateMode(debate.mode)]}, 최대 {debate.max_rounds}라운드. "
        f"다른 참가자: {others}.",
        "기록의 [이름]: 은 그 참가자의 발언, [사용자]: 는 토론을 지켜보는 사용자의 개입이다. "
        "사용자의 개입이 있으면 먼저 반영한다.",
        "앞선 발언을 구체적으로 짚어 반박하거나 보완하고, 새 근거를 하나 이상 보탠다. "
        "같은 말을 반복하지 않는다.",
        "기록 앞부분에 검토할 문서나 코드가 있으면 그것을 더 낫게 만드는 것이 목적이다: "
        "고칠 부분을 인용하고 고친 안을 구체적으로 제시한다.",
        "한국어로 4~6문장 이내. 이름표([이름]:)는 붙이지 않는다.",
    ]
    if stance:
        lines.insert(2, f"너의 입장: {stance}. 이 입장을 끝까지 지킨다.")
    if agent.system_prompt:
        lines.append(agent.system_prompt)
    return "\n".join(lines)


def stance_of(debate: Debate, name: str) -> str | None:
    if debate.mode != DebateMode.PRO_CON:
        return None
    return "찬성" if debate.participants_json.index(name) == 0 else "반대"


NEXT = re.compile(r"NEXT\s*[:：]\s*@?([a-z0-9_-]+)", re.IGNORECASE)


def parse_moderator(text: str, participants: list[str]) -> str | None:
    """ "NEXT: codex" → "codex"; "END" (or nothing usable) → None."""
    if re.search(r"\bEND\b", text) and not NEXT.search(text):
        return None
    if (found := NEXT.search(text)) and found.group(1).lower() in participants:
        return found.group(1).lower()
    return None


class DebateRunner:
    """Runs one debate to its end as a background task of the Runner."""

    def __init__(self, runner: "Runner", debate_id: str) -> None:
        self.runner = runner
        self.debate_id = debate_id
        self.turn = 0

    async def run(self) -> None:
        settings = self.runner.settings
        loop = asyncio.get_running_loop()
        deadline = loop.time() + settings.debate_budget_seconds
        status, error = DebateStatus.DONE, None
        async with self.runner.sessionmaker() as session:
            debate = await services.get_debate(session, self.debate_id)
            agents = {a.name: a for a in await services.list_agents(session)}
            speakers = [agents[p] for p in debate.participants_json if p in agents]
            moderator = agents.get(debate.moderator) or speakers[0]
            names = {a.name: a.display_name for a in speakers}
            for agent in speakers:  # a nearly used-up plan: say so before starting
                await self.runner.warn_if_busy(
                    session, agent, debate.channel_id, debate.thread_root_id
                )
        failures = 0

        async def turn(agent: Agent) -> None:
            nonlocal failures
            if loop.time() > deadline:
                await self._note(debate, "시간 예산을 다 써서 여기서 정리할게요.")
                raise _Stop
            ok = await self._speak(debate, agent, names, stance_of(debate, agent.name))
            failures = 0 if ok else failures + 1
            if failures >= len(speakers):
                raise AgentUnavailable("모든 참가자가 답하지 못했어요")

        try:
            by_name = {a.name: a for a in speakers}
            for round_no in range(1, debate.max_rounds + 1):
                if debate.mode == DebateMode.MODERATED:
                    for position in range(len(speakers)):
                        pick = await self._ask_moderator(debate, moderator, by_name, round_no)
                        if pick is None and round_no == 1 and position == 0:
                            pick = speakers[0].name  # nobody has spoken yet: start anyway
                        if pick is None:
                            await self._note(
                                debate, f"사회자({moderator.display_name})가 토론을 마쳤어요."
                            )
                            raise _Stop
                        await turn(by_name[pick])
                else:
                    for agent in speakers:
                        await turn(agent)
                async with self.runner.sessionmaker() as session:
                    debate = await services.update_debate(
                        session, debate.id, {"rounds_done": round_no}, "system"
                    )
        except _Stop:
            pass
        except asyncio.CancelledError:
            status = DebateStatus.CANCELLED
        except AgentUnavailable as exc:
            status, error = DebateStatus.ERROR, str(exc)
        except Exception as exc:
            log.exception("debate %s failed", self.debate_id)
            status, error = DebateStatus.ERROR, f"{type(exc).__name__}: {exc}"
        summary_id = None
        if status == DebateStatus.DONE:
            summary_id = await self._summarize(debate, moderator, names)
        async with self.runner.sessionmaker() as session:
            await services.update_debate(
                session,
                debate.id,
                {"status": status, "error": error, "summary_message_id": summary_id},
                "system",
            )

    async def _ask_moderator(
        self, debate: Debate, moderator: Agent, speakers: dict[str, Agent], round_no: int
    ) -> str | None:
        async with self.runner.sessionmaker() as session:
            record = await debate_record(session, debate.thread_root_id or "")
        prompt = (
            f"토론 주제: {debate.topic}\n지금까지의 토론:\n{render_record(record)}\n\n"
            f"너는 사회자다. {round_no}/{debate.max_rounds}라운드. 참가자: "
            + ", ".join(speakers)
            + "\n다음 발언자를 한 명 골라 `NEXT: 이름` 한 줄로만 답하거나, 논점이 충분히 "
            "나왔으면 `END` 한 줄로만 답하라."
        )
        adapter = self.runner.adapter_factory(moderator, self.runner.settings, no_tools=True)
        parts: list[str] = []
        try:
            async with asyncio.timeout(self.runner.settings.agent_timeout):
                async for event in adapter.stream(
                    [Turn("user", prompt)], "토론 사회자", "moderator"
                ):
                    if isinstance(event, Token):
                        parts.append(event.text)
        except (TimeoutError, AgentUnavailable):
            return next(iter(speakers))  # keep going in order when the moderator is stuck
        return parse_moderator("".join(parts), list(speakers))

    async def _speak(
        self, debate: Debate, agent: Agent, names: dict[str, str], stance: str | None
    ) -> bool:
        self.turn += 1
        async with self.runner.sessionmaker() as session:
            record = await debate_record(session, debate.thread_root_id or "")
            root = await services.get_message(session, debate.thread_root_id or "")
            run, reply = await services.start_run(
                session,
                agent=agent,
                channel_id=debate.channel_id,
                trigger=root,
                reply_thread_root_id=root.id,
                actor="system",
                kind="debate",
            )
        await hub.publish(
            "debate.turn",
            {
                "debate_id": debate.id,
                "agent": agent.name,
                "turn": self.turn,
                "message_id": reply.id,
            },
        )
        prompt = f"지금까지의 토론:\n{render_record(record)}\n\n이제 네 차례다."
        await self.runner.run_turn(
            run.id,
            agent,
            reply.id,
            [Turn("user", prompt)],
            turn_context(debate, agent, names, stance),
            f"debate-{debate.id}-{self.turn}",
            no_tools=not debate.use_tools,
        )
        _raise_if_cancelled()  # the turn swallows cancellation; the debate must not
        async with self.runner.sessionmaker() as session:
            finished = await services.get_run(session, run.id)
        return finished.status == RunStatus.DONE

    async def _summarize(
        self, debate: Debate, moderator: Agent, names: dict[str, str]
    ) -> str | None:
        async with self.runner.sessionmaker() as session:
            record = await debate_record(session, debate.thread_root_id or "")
            root = await services.get_message(session, debate.thread_root_id or "")
            run, reply = await services.start_run(
                session,
                agent=moderator,
                channel_id=debate.channel_id,
                trigger=root,
                reply_thread_root_id=root.id,
                actor="system",
                kind="debate",
            )
        context = (
            f'너는 토론의 사회자 "{moderator.display_name}"다. 토론 주제: {debate.topic}. '
            "참가자: " + ", ".join(f"{n}(@{p})" for p, n in names.items()) + ". 한국어로 답한다."
        )
        prompt = (
            f"토론 기록:\n{render_record(record)}\n\n"
            "토론을 정리하라. 형식:\n## 요약\n(핵심 쟁점과 각 참가자의 주장 3~5줄)\n"
            "## 합의와 이견\n(짧게)\n## 결론\n(사용자가 취할 행동 한두 가지)\n"
            "토론이 문서나 코드를 다뤘다면 마지막에 ## 개선안 을 두고, 합의된 수정을 모두 "
            "반영한 최종본을 그대로 쓸 수 있게 제시하라(코드는 코드 블록으로)."
        )
        await self.runner.run_turn(
            run.id, moderator, reply.id, [Turn("user", prompt)], context,
            f"debate-{debate.id}-summary", no_tools=True,
        )  # fmt: skip
        _raise_if_cancelled()
        async with self.runner.sessionmaker() as session:
            finished = await services.get_run(session, run.id)
        return reply.id if finished.status == RunStatus.DONE else None

    async def _note(self, debate: Debate, text: str) -> None:
        async with self.runner.sessionmaker() as session:
            await services.create_message(
                session,
                channel_id=debate.channel_id,
                body=text,
                author_type=AuthorType.SYSTEM,
                thread_root_id=debate.thread_root_id,
                actor="system",
            )


def _raise_if_cancelled() -> None:
    task = asyncio.current_task()
    if task is not None and task.cancelling():
        raise asyncio.CancelledError


class _Stop(Exception):
    """Out of time: end normally and summarize what was said."""
