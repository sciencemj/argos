"""More native harness tools (v0.2): inline visual artifacts, web pages, and asking
another agent.

- `render_artifact`: the model hands over an HTML/SVG/Mermaid/TSX snippet; the chat
  shows it in a sandboxed frame (the frontend renders the tool call's arguments, so the
  code is not echoed back into the model's context).
- `web_fetch`: reads a public web page as text. Only http(s), and every address a name
  resolves to (and every redirect hop) must be public: no loopback, private, link-local,
  carrier-grade NAT (Tailscale), multicast or reserved ranges. A DNS answer that changes
  between the check and the connection (rebinding) is still a residual risk; the check
  runs right before each request and redirects are followed by hand.
- `ask_agent`: hands a question to another agent as a child run in the same thread and
  returns its answer (depth and cycles are limited by the runner)."""

import asyncio
import ipaddress
import json
import socket
from collections.abc import Awaitable, Callable
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx2

from argos.harness import ToolContext, ToolDef, ToolRegistry

# --- render_artifact --------------------------------------------------------------------

ARTIFACT_KINDS = ("html", "svg", "mermaid", "tsx")
MAX_ARTIFACT_CODE = 100_000


async def render_artifact(args: dict[str, Any], ctx: ToolContext) -> str:
    title = str(args.get("title") or "").strip()[:200] or "Artifact"
    kind = str(args.get("kind") or "").strip().lower()
    code = str(args.get("code") or "")
    if kind not in ARTIFACT_KINDS:
        raise ValueError(f"kind는 {', '.join(ARTIFACT_KINDS)} 중 하나예요")
    if not code.strip():
        raise ValueError("code가 비어 있어요")
    if len(code) > MAX_ARTIFACT_CODE:
        raise ValueError(f"code가 너무 길어요 ({len(code)}자, 최대 {MAX_ARTIFACT_CODE}자)")
    # The chat renders the call's own arguments; the model only needs to know it worked.
    return json.dumps(
        {"rendered": True, "title": title, "kind": kind, "chars": len(code)}, ensure_ascii=False
    )


# --- web_fetch ----------------------------------------------------------------------------

MAX_FETCH_BYTES = 2_000_000
MAX_FETCH_TEXT = 20_000
MAX_REDIRECTS = 5
FETCH_TIMEOUT = 15.0
CGNAT = ipaddress.ip_network("100.64.0.0/10")  # Tailscale and carrier-grade NAT
USER_AGENT = "Argos/0.2 (personal study assistant; +https://github.com)"

type Resolver = Callable[[str, int], Awaitable[list[str]]]


class FetchBlocked(ValueError):
    """An address Argos will not fetch (scheme, host or resolved IP)."""


async def system_resolve(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


def blocked_ip(raw: str) -> bool:
    ip = ipaddress.ip_address(raw.split("%", 1)[0])  # drop an IPv6 zone
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or (isinstance(ip, ipaddress.IPv4Address) and ip in CGNAT)
    )


async def check_url(url: str, resolve: Resolver = system_resolve) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise FetchBlocked("http(s) 주소만 읽을 수 있어요")
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        raise FetchBlocked("주소에 호스트가 없어요")
    if parts.username or parts.password:
        raise FetchBlocked("주소에 계정 정보를 넣을 수 없어요")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".ts.net")):
        raise FetchBlocked("내부 주소는 읽을 수 없어요")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        addresses = [host] if _is_ip(host) else await resolve(host, port)
    except OSError as exc:
        raise FetchBlocked(f"주소를 찾을 수 없어요: {host}") from exc
    if not addresses or any(blocked_ip(a) for a in addresses):
        raise FetchBlocked("내부·사설 네트워크 주소는 읽을 수 없어요")


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return True


class _TextExtractor(HTMLParser):
    """Visible text of an HTML page: scripts, styles and the like dropped, a line break
    at block boundaries."""

    SKIP = {"script", "style", "noscript", "template", "svg", "head"}
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section",
             "article", "header", "footer", "pre", "blockquote", "table", "ul", "ol"}  # fmt: skip

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self._in_title = True
        elif tag in self.SKIP:
            self._skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag in self.SKIP and self._skip:
            self._skip -= 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> tuple[str, str]:
    parser = _TextExtractor()
    parser.feed(html)
    lines = (" ".join(line.split()) for line in "".join(parser.parts).splitlines())
    text = "\n".join(line for line in lines if line)
    return " ".join(parser.title.split()), text


async def fetch_page(
    url: str,
    *,
    resolve: Resolver = system_resolve,
    transport: httpx2.AsyncBaseTransport | None = None,
) -> tuple[str, str, str]:
    """(final url, content type, text) of a public page; FetchBlocked otherwise."""
    async with httpx2.AsyncClient(
        follow_redirects=False,
        trust_env=False,  # no proxies from the environment
        timeout=FETCH_TIMEOUT,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,text/plain;q=0.9,*/*;q=0.5"},
        transport=transport,
    ) as http:
        for _ in range(MAX_REDIRECTS + 1):
            await check_url(url, resolve)
            async with http.stream("GET", url) as response:
                if response.is_redirect and "location" in response.headers:
                    url = urljoin(url, response.headers["location"])
                    continue
                if response.status_code >= 400:
                    raise ValueError(f"페이지가 {response.status_code}로 답했어요")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body += chunk
                    if len(body) > MAX_FETCH_BYTES:
                        break
                kind = response.headers.get("content-type", "").split(";")[0].strip().lower()
                encoding = response.encoding or "utf-8"
                text = bytes(body[:MAX_FETCH_BYTES]).decode(encoding, errors="replace")
                return url, kind, text
        raise FetchBlocked(f"리다이렉트가 {MAX_REDIRECTS}번을 넘었어요")


async def web_fetch(args: dict[str, Any], ctx: ToolContext) -> str:
    url = str(args.get("url") or "").strip()
    final, kind, raw = await fetch_page(url)
    if kind in ("text/html", "application/xhtml+xml") or raw.lstrip()[:15].lower().startswith(
        ("<!doctype html", "<html")
    ):
        title, text = await asyncio.to_thread(html_to_text, raw)
    elif kind.startswith("text/") or kind in ("application/json", "application/xml", ""):
        title, text = "", raw
    else:
        raise ValueError(f"글로 읽을 수 없는 형식이에요 ({kind})")
    head = f"# {title}\n" if title else ""
    if len(text) > MAX_FETCH_TEXT:
        text = text[:MAX_FETCH_TEXT] + f"\n…(잘림: {len(text) - MAX_FETCH_TEXT}자 더 있음)"
    return f"{head}출처: {final}\n\n{text}"


# --- ask_agent ----------------------------------------------------------------------------


async def ask_agent(args: dict[str, Any], ctx: ToolContext) -> str:
    name = str(args.get("agent") or "").strip().lstrip("@")
    prompt = str(args.get("prompt") or "").strip()
    if not name or not prompt:
        raise ValueError("agent와 prompt가 필요해요")
    if ctx.delegate is None:
        raise ValueError("이 실행에서는 다른 에이전트를 부를 수 없어요")
    return await ctx.delegate(name, prompt)


# --- registration -------------------------------------------------------------------------

_STR = {"type": "string"}

EXTRA_TOOLS = [
    ToolDef(
        "render_artifact",
        "Show a visual in the chat: an HTML page, an SVG image, a Mermaid diagram, or a "
        "small React component (tsx, `export default function App()`; React hooks via "
        "`import { useState } from 'react'`). It runs sandboxed without network access, "
        "so inline everything (no external scripts, fonts or images).",
        {
            "type": "object",
            "properties": {
                "title": _STR,
                "kind": {"type": "string", "enum": list(ARTIFACT_KINDS)},
                "code": _STR,
            },
            "required": ["title", "kind", "code"],
        },
        "read",
        render_artifact,
    ),
    ToolDef(
        "web_fetch",
        "Read a public web page (http/https) as plain text: its title and visible text, "
        "cut at 20,000 characters. Internal or private network addresses are refused.",
        {"type": "object", "properties": {"url": _STR}, "required": ["url"]},
        "read",
        web_fetch,
    ),
    ToolDef(
        "ask_agent",
        "Ask another Argos agent (by its @name, e.g. claude or codex) a question or hand "
        "it a subtask; returns its answer. Its reply also appears in this thread.",
        {
            "type": "object",
            "properties": {"agent": _STR, "prompt": _STR},
            "required": ["agent", "prompt"],
        },
        "write",
        ask_agent,
        summarize=lambda a: (
            f"@{str(a.get('agent', '')).lstrip('@')}에게 묻기: {str(a.get('prompt', ''))[:150]}"
        ),
    ),
]


def register_extra_tools(registry: ToolRegistry) -> None:
    for tool in EXTRA_TOOLS:
        registry.register(tool, replace=True)
