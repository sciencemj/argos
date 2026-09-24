"""WebSocket fan-out (PLAN §7.2). Every message is {"type", "data", "ts"}."""

from datetime import UTC, datetime
from typing import Any

from fastapi import WebSocket


class Hub:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

    async def publish(self, type_: str, data: dict[str, Any]) -> None:
        message = {"type": type_, "data": data, "ts": datetime.now(UTC).isoformat()}
        for ws in list(self._clients):
            try:
                await ws.send_json(message)
            except Exception:  # a dead socket must not break the write that published
                self.disconnect(ws)


hub = Hub()
