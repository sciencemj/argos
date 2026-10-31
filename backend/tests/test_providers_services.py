from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from argos import services
from argos.models import ActivityLog, AgentBackend, ModelProvider
from argos.services import ConflictError, InvalidError

GPU: dict[str, Any] = {"name": "GPU", "kind": "ollama", "base_url": "http://gpu.test:11434"}


async def test_create_provider_normalizes_and_logs(session: AsyncSession) -> None:
    before = await session.scalar(select(func.count()).select_from(ActivityLog))
    p = await services.create_provider(session, {**GPU, "name": " GPU "}, "user")
    assert (p.name, p.kind, p.base_url) == ("GPU", "ollama", "http://gpu.test:11434/v1")
    assert p.has_api_key is False and p.is_default is False
    after = await session.scalar(select(func.count()).select_from(ActivityLog))
    assert after == (before or 0) + 1


async def test_provider_name_unique(session: AsyncSession) -> None:
    await services.create_provider(session, GPU, "user")
    with pytest.raises(ConflictError):
        await services.create_provider(session, {**GPU, "base_url": "http://x.test"}, "user")


@pytest.mark.parametrize(
    "bad",
    [
        {"kind": "anthropic"},
        {"base_url": "gpu"},
        {"strict_locality": "device"},
        {"name": "  "},
    ],
)
async def test_provider_rejects_bad_fields(session: AsyncSession, bad: dict[str, Any]) -> None:
    with pytest.raises(InvalidError):
        await services.create_provider(session, {**GPU, **bad}, "user")


async def test_update_provider_renormalizes_url(session: AsyncSession) -> None:
    p = await services.create_provider(session, GPU, "user")
    p = await services.update_provider(
        session, p.id, {"kind": "openai", "base_url": "https://api.example.test/v1/"}, "user"
    )
    assert p.base_url == "https://api.example.test/v1"


async def test_delete_default_provider_conflict(session: AsyncSession) -> None:
    p = await services.create_provider(session, GPU, "user")
    p.is_default = True
    await session.commit()
    with pytest.raises(ConflictError):
        await services.delete_provider(session, p.id, "user")


async def test_delete_provider_in_use_lists_users(session: AsyncSession) -> None:
    p = await services.create_provider(session, GPU, "user")
    agent = await services.create_agent(
        session,
        {
            "name": "tutor",
            "display_name": "Tutor",
            "backend": AgentBackend.OLLAMA,
            "provider_id": p.id,
        },
        [],
        "user",
    )
    await services.set_setting(session, "classifier_provider_id", p.id, "user")
    assert await services.provider_usage(session, p.id) == ["@tutor", "인박스 분류"]
    with pytest.raises(ConflictError) as err:
        await services.delete_provider(session, p.id, "user")
    assert "@tutor" in str(err.value) and "인박스 분류" in str(err.value)
    await services.delete_agent(session, agent.id, "user")
    await services.set_setting(session, "classifier_provider_id", None, "user")
    await services.delete_provider(session, p.id, "user")
    assert await session.get(ModelProvider, p.id) is None


async def test_agent_fields_validate_provider(session: AsyncSession) -> None:
    base = {"display_name": "Tutor", "backend": AgentBackend.OLLAMA}
    with pytest.raises(InvalidError):
        await services.create_agent(
            session, {**base, "name": "tutor", "provider_id": "missing"}, [], "user"
        )
    p = await services.create_provider(session, GPU, "user")
    agent = await services.create_agent(
        session, {**base, "name": "tutor", "provider_id": p.id, "send_images": True}, [], "user"
    )
    assert (agent.provider_id, agent.send_images) == (p.id, True)
