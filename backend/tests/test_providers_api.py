from collections.abc import Iterator
from typing import Any, cast

import pytest
from fakes import FakeKeychain
from fastapi.testclient import TestClient

from argos import providers, services
from argos.config import Settings
from argos.main import create_app
from argos.providers import ProviderConfig, ProviderModel

GPU = {"name": "GPU", "kind": "ollama", "base_url": "http://gpu.test:11434", "api_key": "k"}


@pytest.fixture
def keychain() -> FakeKeychain:
    return FakeKeychain()


@pytest.fixture
def api(settings: Settings, keychain: FakeKeychain) -> Iterator[TestClient]:
    with TestClient(create_app(settings, provider_keychain=keychain)) as c:
        yield c


@pytest.fixture
def listed(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Fake model servers: each answers with one model named after its address."""
    asked: list[str] = []

    async def fake(p: ProviderConfig, *, decision: bool = False) -> list[ProviderModel]:
        asked.append(p.base_url)
        if "down.test" in p.base_url:
            raise providers.ProviderError("refused")
        return [ProviderModel(f"model@{p.base_url}", remote=False, parameter_size=None)]

    monkeypatch.setattr(providers, "list_models", fake)
    return asked


def state(client: TestClient) -> Any:
    return cast(Any, client.app).state


def test_provider_crud_roundtrip(api: TestClient, keychain: FakeKeychain) -> None:
    created = api.post("/api/v1/providers", json=GPU)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["base_url"] == "http://gpu.test:11434/v1"
    assert body["has_api_key"] is True and body["is_default"] is False
    assert body["locality"] == "cloud"  # gpu.test is not a private address
    assert '"k"' not in created.text
    assert keychain.saved == {body["id"]: "k"}

    renamed = api.patch(f"/api/v1/providers/{body['id']}", json={"name": "Box"})
    assert renamed.json()["name"] == "Box"
    names = [p["name"] for p in api.get("/api/v1/providers").json()]
    assert names == ["이 Mac", "Box"]

    assert api.delete(f"/api/v1/providers/{body['id']}").status_code == 204
    assert keychain.saved == {}
    assert len(api.get("/api/v1/providers").json()) == 1


def test_patch_api_key_empty_clears_and_omitted_keeps(
    api: TestClient, keychain: FakeKeychain
) -> None:
    pid = api.post("/api/v1/providers", json=GPU).json()["id"]
    kept = api.patch(f"/api/v1/providers/{pid}", json={"name": "GPU2"}).json()
    assert kept["has_api_key"] is True and keychain.saved == {pid: "k"}
    cleared = api.patch(f"/api/v1/providers/{pid}", json={"api_key": ""}).json()
    assert cleared["has_api_key"] is False and keychain.saved == {}


def test_delete_in_use_provider_409(api: TestClient) -> None:
    pid = api.post("/api/v1/providers", json=GPU).json()["id"]
    s = state(api)

    async def make() -> None:
        async with s.sessionmaker() as session:
            await services.create_agent(
                session,
                {"name": "tutor", "display_name": "Tutor", "backend": "ollama", "provider_id": pid},
                [],
                "user",
            )

    cast(Any, api).portal.call(make)
    r = api.delete(f"/api/v1/providers/{pid}")
    assert r.status_code == 409
    assert "@tutor" in r.json()["error"]["message"]


def test_delete_default_409(api: TestClient) -> None:
    default = api.get("/api/v1/providers").json()[0]
    assert default["is_default"] is True
    assert api.delete(f"/api/v1/providers/{default['id']}").status_code == 409


def test_provider_test_endpoint(api: TestClient, listed: list[str]) -> None:
    ok = api.post(
        "/api/v1/providers/test", json={"kind": "ollama", "base_url": "http://100.100.1.1:11434"}
    ).json()
    assert ok["reachable"] is True and ok["locality"] == "network"
    assert [m["name"] for m in ok["models"]] == ["model@http://100.100.1.1:11434/v1"]
    down = api.post(
        "/api/v1/providers/test", json={"kind": "ollama", "base_url": "http://down.test:11434"}
    ).json()
    assert down["reachable"] is False
    assert down["error"] == "연결할 수 없어요. 주소와 서버가 켜져 있는지 확인해 주세요."
    bad = api.post("/api/v1/providers/test", json={"kind": "ollama", "base_url": "gpu"})
    assert bad.status_code == 422


def test_provider_test_uses_saved_key(
    api: TestClient, monkeypatch: pytest.MonkeyPatch, keychain: FakeKeychain
) -> None:
    pid = api.post("/api/v1/providers", json=GPU).json()["id"]
    keys: list[str | None] = []

    async def fake(p: ProviderConfig, *, decision: bool = False) -> list[ProviderModel]:
        keys.append(p.api_key)
        return []

    monkeypatch.setattr(providers, "list_models", fake)
    api.post(
        "/api/v1/providers/test",
        json={"kind": "ollama", "base_url": "http://gpu.test:11434", "provider_id": pid},
    )
    assert keys == ["k"]


def test_cloud_locality_reported(api: TestClient) -> None:
    body = api.post(
        "/api/v1/providers",
        json={"name": "Router", "kind": "openai", "base_url": "https://api.example.test/v1"},
    ).json()
    assert body["locality"] == "cloud"
    marked = api.post(
        "/api/v1/providers",
        json={
            "name": "LAN",
            "kind": "openai",
            "base_url": "http://192.168.0.9:1234/v1",
            "strict_locality": "cloud",
        },
    ).json()
    assert marked["locality"] == "cloud"


def test_provider_models_endpoint(api: TestClient, listed: list[str]) -> None:
    pid = api.post("/api/v1/providers", json=GPU).json()["id"]
    body = api.get(f"/api/v1/providers/{pid}/models").json()
    assert body["reachable"] is True
    assert [m["name"] for m in body["models"]] == ["model@http://gpu.test:11434/v1"]


def test_models_cache_follows_provider_edit(api: TestClient, listed: list[str]) -> None:
    pid = api.post("/api/v1/providers", json=GPU).json()["id"]
    q = {"backend": "ollama", "provider_id": pid}
    first = api.get("/api/v1/agents/models", params=q).json()
    assert [m["id"] for m in first["models"]] == ["model@http://gpu.test:11434/v1"]
    api.patch(f"/api/v1/providers/{pid}", json={"base_url": "http://box.test:11434"})
    second = api.get("/api/v1/agents/models", params=q).json()
    assert [m["id"] for m in second["models"]] == ["model@http://box.test:11434/v1"]


def test_agent_models_describe_locality(api: TestClient, listed: list[str]) -> None:
    pid = api.post(
        "/api/v1/providers", json={**GPU, "base_url": "http://100.100.1.1:11434"}
    ).json()["id"]
    body = api.get("/api/v1/agents/models", params={"backend": "ollama", "provider_id": pid})
    assert body.json()["models"][0]["description"] == "내 네트워크에서 실행"
    home = api.get("/api/v1/agents/models", params={"backend": "ollama"}).json()
    assert home["models"][0]["description"] == "이 컴퓨터에서 실행"


def test_keychain_failure_leaves_no_provider(api: TestClient, keychain: FakeKeychain) -> None:
    keychain.fail = True
    r = api.post("/api/v1/providers", json=GPU)
    assert r.status_code == 422
    assert r.json()["error"]["message"] == "키체인에 API 키를 저장하지 못했어요"
    assert [p["name"] for p in api.get("/api/v1/providers").json()] == ["이 Mac"]


def test_keychain_failure_on_edit_keeps_key_state(api: TestClient, keychain: FakeKeychain) -> None:
    pid = api.post(
        "/api/v1/providers", json={k: v for k, v in GPU.items() if k != "api_key"}
    ).json()["id"]
    keychain.fail = True
    r = api.patch(f"/api/v1/providers/{pid}", json={"api_key": "new"})
    assert r.status_code == 422
    listed = {p["id"]: p for p in api.get("/api/v1/providers").json()}
    assert listed[pid]["has_api_key"] is False


def test_runner_sees_new_provider(api: TestClient) -> None:
    pid = api.post("/api/v1/providers", json=GPU).json()["id"]
    s = state(api)
    assert pid in [p.id for p in s.runner.settings.model_providers]
    assert pid in [p.id for p in s.settings.model_providers]
    [gpu] = [p for p in s.runner.settings.model_providers if p.id == pid]
    assert gpu.api_key == "k"


# --- final review fixes ----------------------------------------------------------------


def test_rejected_edit_keeps_key(api: TestClient, keychain: FakeKeychain) -> None:
    pid = api.post("/api/v1/providers", json=GPU).json()["id"]
    bad = api.patch(f"/api/v1/providers/{pid}", json={"api_key": "new", "base_url": "ftp://x"})
    assert bad.status_code == 422
    assert keychain.saved == {pid: "k"}
    api.post(
        "/api/v1/providers", json={"name": "Other", "kind": "ollama", "base_url": "http://o.test"}
    )
    taken = api.patch(f"/api/v1/providers/{pid}", json={"api_key": "", "name": "Other"})
    assert taken.status_code == 409
    assert keychain.saved == {pid: "k"}


def test_delete_survives_keychain_failure(api: TestClient, keychain: FakeKeychain) -> None:
    pid = api.post("/api/v1/providers", json=GPU).json()["id"]
    keychain.fail = True
    assert api.delete(f"/api/v1/providers/{pid}").status_code == 204
    assert pid not in [p.id for p in state(api).settings.model_providers]


def test_test_reuses_saved_key_only_for_same_host(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid = api.post("/api/v1/providers", json=GPU).json()["id"]
    keys: list[str | None] = []

    async def fake(p: ProviderConfig, *, decision: bool = False) -> list[ProviderModel]:
        keys.append(p.api_key)
        return []

    monkeypatch.setattr(providers, "list_models", fake)
    api.post(
        "/api/v1/providers/test",
        json={"kind": "ollama", "base_url": "http://typo.test:11434", "provider_id": pid},
    )
    assert keys == [None]
