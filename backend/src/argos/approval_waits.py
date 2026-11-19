"""Native harness runs paused on a tool approval (v0.2): the run awaits here and
`services.resolve_approval` wakes it once the decision is committed.

In-process futures only; the waiter also re-reads the approval now and then, so a
decision made through another path (or before the waiter registered) is not missed."""

import asyncio
from collections.abc import Awaitable, Callable

POLL_SECONDS = 5.0


class ApprovalWaits:
    """Several waiters may wait on one approval (a retried gate, another view of the
    run): each gets its own future and all of them are woken."""

    def __init__(self) -> None:
        self._futures: dict[str, set[asyncio.Future[bool]]] = {}

    def settle(self, approval_id: str, approved: bool) -> None:
        for future in self._futures.get(approval_id, set()):
            if not future.done():
                future.set_result(approved)

    async def wait(
        self,
        approval_id: str,
        decided: Callable[[], Awaitable[bool | None]],
        poll: float = POLL_SECONDS,
    ) -> bool:
        """True once approved, False once rejected (or failed). `decided` reads the saved
        decision: None while it is still pending."""
        future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self._futures.setdefault(approval_id, set()).add(future)
        try:
            while True:
                answer = await decided()
                if answer is not None:
                    return answer
                try:
                    return await asyncio.wait_for(asyncio.shield(future), poll)
                except TimeoutError:
                    continue
        finally:
            waiting = self._futures.get(approval_id)
            if waiting is not None:
                waiting.discard(future)
                if not waiting:
                    del self._futures[approval_id]

    def waiting(self) -> list[str]:
        return list(self._futures)


approval_waits = ApprovalWaits()
