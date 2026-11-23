"""An agent's reply text and its run's status always arrive together: `finish_run` writes
both in one commit, and the message/thread endpoints read from one SQLite snapshot even
when the run finishes between their SELECTs (the race behind flaky "done, but empty
reply" results)."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from argos import services
from argos.config import Settings
from argos.main import create_app
from argos.models import Agent, AgentBackend, Channel, ChannelKind, RunStatus


async def _started_run(app: Any) -> tuple[str, str, str]:
    """(channel id, root message id, run id): a run whose reply is still empty."""
    async with app.state.sessionmaker() as session:
        channel = await session.scalar(
            select(Channel).where(Channel.kind != ChannelKind.DM).limit(1)
        )
        assert channel is not None
        agent = Agent(name="snap", display_name="Snap", backend=AgentBackend.NATIVE)
        session.add(agent)
        await session.commit()
        root = await services.create_message(
            session, channel_id=channel.id, body="질문", actor="user"
        )
        run, _ = await services.start_run(
            session,
            agent=agent,
            channel_id=channel.id,
            trigger=root,
            reply_thread_root_id=root.id,
            actor="user",
        )
        return channel.id, root.id, run.id


async def _finish(app: Any, run_id: str) -> None:
    async with app.state.sessionmaker() as other:
        await services.finish_run(other, run_id, text="답이에요", status=RunStatus.DONE, error=None)


def _portal(client: TestClient) -> Any:
    assert client.portal is not None  # inside `with TestClient(app)`
    return client.portal


def _finish_after(monkeypatch: pytest.MonkeyPatch, app: Any, name: str, run_id: str) -> None:
    """The run finishes (in another session) right after the endpoint's first read."""
    original = getattr(services, name)

    async def racing(*args: Any, **kwargs: Any) -> Any:
        result = await original(*args, **kwargs)
        await _finish(app, run_id)
        return result

    monkeypatch.setattr(services, name, racing)


def test_thread_reads_reply_and_run_from_one_snapshot(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_app(settings)
    with TestClient(app) as client:
        _, root_id, run_id = _portal(client).call(_started_run, app)
        _finish_after(monkeypatch, app, "list_thread", run_id)
        [reply] = client.get(f"/api/v1/messages/{root_id}/thread").json()["replies"]
        # The commit landed mid-request: the page still shows the state before it.
        assert (reply["body"], reply["run"]["status"]) == ("", "running")
        monkeypatch.undo()
        [reply] = client.get(f"/api/v1/messages/{root_id}/thread").json()["replies"]
        assert (reply["body"], reply["run"]["status"]) == ("답이에요", "done")


def test_channel_messages_read_from_one_snapshot(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_app(settings)
    with TestClient(app) as client:
        channel_id, root_id, run_id = _portal(client).call(_started_run, app)
        _finish_after(monkeypatch, app, "list_messages", run_id)

        def answer() -> tuple[str, str]:
            items = client.get(f"/api/v1/channels/{channel_id}/messages").json()["items"]
            [root] = [m for m in items if m["id"] == root_id]
            [reply] = root["agent_replies"]
            return reply["body"], reply["run"]["status"]

        assert answer() == ("", "running")
        monkeypatch.undo()
        assert answer() == ("답이에요", "done")


def test_finish_run_writes_reply_and_run_in_one_commit(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_app(settings)
    published: list[tuple[str, str, dict[str, Any]]] = []

    async def publish(type_: str, payload: dict[str, Any]) -> None:
        published.append((type_, payload["object_type"], payload["object"]))

    async def finish_counting(run_id: str) -> int:
        commits = 0
        async with app.state.sessionmaker() as session:
            commit = session.commit

            async def counted() -> None:
                nonlocal commits
                commits += 1
                await commit()

            session.commit = counted  # type: ignore[method-assign]
            await services.finish_run(
                session, run_id, text="답이에요", status=RunStatus.DONE, error=None
            )
        return commits

    with TestClient(app) as client:
        _, _, run_id = _portal(client).call(_started_run, app)
        monkeypatch.setattr(services.hub, "publish", publish)
        assert _portal(client).call(finish_counting, run_id) == 1
    # Both rows still announce their change, after the commit.
    kinds = [(t, o) for t, o, _ in published]
    assert kinds == [("object.updated", "message"), ("object.updated", "agent_run")]
    assert published[0][2]["body"] == "답이에요"
    assert published[1][2]["status"] == "done"
