"""Model providers (PLAN Phase 20): named Ollama or OpenAI-compatible servers that
agents and the inbox classifier choose between.

Rows live in `model_provider`; API keys live in the macOS Keychain. Runtime code reads a
`ProviderConfig` snapshot from `Settings.model_providers`, so sync builders such as
`agents.build_adapter` need no database session."""

import asyncio
import ipaddress
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol, cast
from urllib.parse import urlsplit

import httpx2
import keyring
import keyring.errors
from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from argos.models import ModelProvider

if TYPE_CHECKING:
    from argos.config import Settings

type ProviderKind = Literal["ollama", "openai"]
type Locality = Literal["device", "network", "cloud"]

LOCALITY_ORDER: tuple[Locality, ...] = ("device", "network", "cloud")
# Home networks and Tailscale (CGNAT): "my network", not the internet. Spelled out
# because ipaddress's is_private also covers documentation ranges.
NETWORKS = tuple(
    ipaddress.ip_network(n)
    for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "fc00::/7")
)
NETWORK_SUFFIXES = (".ts.net", ".local")
LIST_TIMEOUT = 5
KEYCHAIN_SERVICE = "Argos model provider API key"
log = logging.getLogger(__name__)
DEFAULT_NAME = "이 Mac"
DEFAULT_BASE_URL = "http://127.0.0.1:11434/v1"


class ProviderError(Exception):
    """The server could not be reached or answered something unexpected."""


@dataclass(frozen=True)
class ProviderConfig:
    id: str
    name: str
    kind: ProviderKind
    base_url: str  # up to and including /v1
    api_key: str | None = field(repr=False)
    locality: Locality
    is_default: bool


@dataclass(frozen=True)
class ProviderModel:
    name: str
    remote: bool  # Ollama "*-cloud" models run on ollama.com
    parameter_size: str | None
    decision: bool = False  # Ollama decision models: classify only, cannot chat


def normalize_base_url(kind: ProviderKind, url: str) -> str:
    """http(s) only, no trailing slash; Ollama addresses get their /v1."""
    url = url.strip().rstrip("/")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("주소는 http:// 또는 https://로 시작해야 해요")
    if kind == "ollama" and not url.endswith("/v1"):
        url += "/v1"
    return url


def locality_of(base_url: str) -> Locality:
    host = (urlsplit(base_url).hostname or "").lower()
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        if host == "localhost":
            return "device"
        return "network" if host.endswith(NETWORK_SUFFIXES) else "cloud"
    if ip.is_loopback:
        return "device"
    return "network" if any(ip in n for n in NETWORKS) else "cloud"


def effective_locality(computed: Locality, strict: Locality | None) -> Locality:
    """The user may mark a provider stricter than its address says, never looser."""
    if strict is None:
        return computed
    return strict if LOCALITY_ORDER.index(strict) > LOCALITY_ORDER.index(computed) else computed


def unreachable_text(p: ProviderConfig) -> str:
    return f"{p.name}에 연결할 수 없어요 ({p.base_url})"


def chat_client(p: ProviderConfig, *, timeout: float, max_retries: int) -> AsyncOpenAI:
    # The SDK refuses an empty key; Ollama and most local servers ignore it.
    return AsyncOpenAI(
        base_url=p.base_url, api_key=p.api_key or "none", timeout=timeout, max_retries=max_retries
    )


def _root(p: ProviderConfig) -> str:
    return p.base_url.removesuffix("/v1")


async def _get_json(url: str, headers: dict[str, str] | None = None) -> Any:
    try:
        async with httpx2.AsyncClient(timeout=LIST_TIMEOUT, follow_redirects=False) as http:
            response = await http.get(url, headers=headers)
            response.raise_for_status()
            return response.json()
    except Exception as exc:
        raise ProviderError(f"cannot reach {url}: {type(exc).__name__}") from exc


async def list_models(p: ProviderConfig, *, decision: bool = False) -> list[ProviderModel]:
    """Chat-capable models (plus Ollama decision models when `decision`), sorted local
    first. Raises ProviderError when the server cannot be reached."""
    if p.kind == "openai":
        headers = {"Authorization": f"Bearer {p.api_key}"} if p.api_key else None
        data = await _get_json(f"{p.base_url}/models", headers)
        try:
            names = sorted(str(m["id"]) for m in data["data"])
        except (KeyError, TypeError) as exc:
            raise ProviderError("unexpected /models answer") from exc
        return [ProviderModel(name, False, None) for name in names]
    data = await _get_json(f"{_root(p)}/api/tags")
    models: list[ProviderModel] = []
    for m in cast(list[dict[str, Any]], data.get("models", [])):
        capabilities: list[str] | None = m.get("capabilities")
        is_decision = capabilities is not None and "decision" in capabilities
        if capabilities is not None and "completion" not in capabilities:
            if not (decision and is_decision):
                continue
        details: dict[str, Any] = m.get("details") or {}
        models.append(
            ProviderModel(
                name=str(m["name"]),
                remote=bool(m.get("remote_host")),
                parameter_size=details.get("parameter_size") or None,
                decision=is_decision,
            )
        )
    return sorted(models, key=lambda m: (m.remote, m.name))


_VISION: dict[tuple[str, str], bool] = {}


async def vision(p: ProviderConfig, model: str) -> bool:
    """Whether the model takes images. Ollama says so in /api/show; when that fails
    the server decides (True, not cached). OpenAI-compatible servers do not say: the
    agent's send_images switch decides instead."""
    if p.kind != "ollama":
        return False
    key = (p.base_url, model)
    if key not in _VISION:
        try:
            async with httpx2.AsyncClient(timeout=LIST_TIMEOUT, follow_redirects=False) as http:
                response = await http.post(f"{_root(p)}/api/show", json={"model": model})
                response.raise_for_status()
                capabilities = cast(list[str], response.json().get("capabilities") or [])
        except (httpx2.HTTPError, ValueError):
            return True
        _VISION[key] = "vision" in capabilities
    return _VISION[key]


class KeyStore(Protocol):
    def get(self, provider_id: str, /) -> str | None: ...
    def set(self, provider_id: str, key: str, /) -> None: ...
    def delete(self, provider_id: str, /) -> None: ...


class Keychain:
    """Provider API keys in the macOS Keychain, one item per provider id."""

    def get(self, provider_id: str) -> str | None:
        return keyring.get_password(KEYCHAIN_SERVICE, provider_id)

    def set(self, provider_id: str, key: str) -> None:
        # keyring replaces a macOS item by deleting it first; skip when unchanged.
        if self.get(provider_id) != key:
            keyring.set_password(KEYCHAIN_SERVICE, provider_id, key)

    def delete(self, provider_id: str) -> None:
        if self.get(provider_id) is not None:
            keyring.delete_password(KEYCHAIN_SERVICE, provider_id)


# --- the snapshot runtime code reads ----------------------------------------------------


def resolve(settings: "Settings", provider_id: str | None) -> ProviderConfig:
    """The chosen provider; a missing or deleted choice falls back to the default."""
    for p in settings.model_providers:
        if p.id == provider_id:
            return p
    return _default(settings)


def classifier_provider(settings: "Settings") -> ProviderConfig:
    """Where the Ollama classifier (and the built-in local agent) run."""
    return resolve(settings, settings.classifier_provider_id)


def _default(settings: "Settings") -> ProviderConfig:
    for p in settings.model_providers:
        if p.is_default:
            return p
    # Before the snapshot is loaded (and in tests that build Settings directly): the
    # address .env gives, as the pre-provider code used it.
    base = settings.classifier_base_url or DEFAULT_BASE_URL
    key = settings.classifier_api_key.get_secret_value() if settings.classifier_api_key else None
    return ProviderConfig("", DEFAULT_NAME, "ollama", base, key, locality_of(base), True)


def with_providers(settings: "Settings", loaded: tuple[ProviderConfig, ...]) -> "Settings":
    return settings.model_copy(update={"model_providers": loaded})


async def ensure_default_provider(
    session: AsyncSession, base: "Settings", keychain: KeyStore
) -> None:
    """First start after the upgrade (or ever): make "이 Mac" from what .env says. A Hermes
    classifier's address and key are Hermes's, so they stay out of it."""
    from argos import services

    if await session.scalar(select(ModelProvider.id).limit(1)) is not None:
        return
    ollama = base.classifier_provider == "ollama"
    url = (base.classifier_base_url if ollama else None) or DEFAULT_BASE_URL
    try:
        url = normalize_base_url("ollama", url)
    except ValueError:
        # Before providers a bad address only broke classification: keep starting.
        log.warning(
            "ARGOS_CLASSIFIER_BASE_URL is not an http(s) address; using %s", DEFAULT_BASE_URL
        )
        url = DEFAULT_BASE_URL
    key = base.classifier_api_key.get_secret_value() if ollama and base.classifier_api_key else None
    provider = await services.create_provider(
        session,
        {"name": DEFAULT_NAME, "kind": "ollama", "base_url": url, "has_api_key": key is not None},
        "system",
    )
    provider.is_default = True
    if key is not None:
        try:
            await asyncio.to_thread(keychain.set, provider.id, key)
        except keyring.errors.KeyringError:
            log.warning("could not store the classifier API key in the Keychain")
            provider.has_api_key = False
    await session.commit()


async def _read_key(keychain: KeyStore, row: ModelProvider) -> str | None:
    """A locked or denied Keychain must not stop the app: that provider runs keyless."""
    try:
        return await asyncio.to_thread(keychain.get, row.id)
    except keyring.errors.KeyringError:
        log.warning("could not read the API key of model provider %r", row.name)
        return None


async def load_providers(session: AsyncSession, keychain: KeyStore) -> tuple[ProviderConfig, ...]:
    rows = (await session.scalars(select(ModelProvider).order_by(ModelProvider.name))).all()
    loaded: list[ProviderConfig] = []
    for row in rows:
        key = await _read_key(keychain, row) if row.has_api_key else None
        locality = effective_locality(
            locality_of(row.base_url), cast(Locality | None, row.strict_locality)
        )
        kind = cast(ProviderKind, row.kind)
        loaded.append(
            ProviderConfig(row.id, row.name, kind, row.base_url, key, locality, row.is_default)
        )
    return tuple(loaded)
