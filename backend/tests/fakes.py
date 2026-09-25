import asyncio
from collections.abc import AsyncIterator
from typing import Any

from argos.agents import AgentEvent, AgentUnavailable, Status, Token, Turn
from argos.classifier import ClassifierError, ClassifyContext, Suggestion
from argos.config import Settings
from argos.models import Agent


class FakeClassifier:
    """Returns a fixed suggestion (or raises) and remembers what it was asked."""

    def __init__(self, suggestion: Suggestion | None = None, error: str | None = None) -> None:
        self.suggestion = suggestion
        self.error = error
        self.calls: list[tuple[str, ClassifyContext]] = []

    async def classify(self, text: str, context: ClassifyContext) -> Suggestion:
        self.calls.append((text, context))
        if self.error or self.suggestion is None:
            raise ClassifierError(self.error or "no suggestion configured")
        return self.suggestion


class FakeAgent:
    """Streams preset tokens (PLAN §8.6 FakeAgentAdapter). `hold` keeps it streaming until
    released, so a test can cancel it mid-answer."""

    def __init__(self, name: str, tokens: list[str], hold: asyncio.Event | None = None) -> None:
        self.name = name
        self.tokens = tokens
        self.hold = hold
        self.transcripts: list[list[Turn]] = []
        self.contexts: list[str] = []
        self.sessions: list[str] = []

    async def stream(
        self, transcript: list[Turn], context: str, session: str
    ) -> AsyncIterator[AgentEvent]:
        self.transcripts.append(transcript)
        self.contexts.append(context)
        self.sessions.append(session)
        yield Status("thinking")
        for token in self.tokens:
            yield Token(token)
            if self.hold is not None:
                await self.hold.wait()


def fake_agents(**agents: FakeAgent) -> Any:
    """Adapter factory for Runner: agent name → fake; unknown names fail like a real
    unreachable backend."""

    def factory(agent: Agent, settings: Settings, sessions: Any = None) -> FakeAgent:
        if agent.name not in agents:
            raise AgentUnavailable(f"{agent.name} unavailable in tests")
        return agents[agent.name]

    return factory
