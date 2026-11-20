
"""Holds requests pending until the user decides. Everything fails closed."""
import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Callable, Optional

from .models import ApprovalRecord, Decision, RequestContext, Status

log = logging.getLogger("approval")


@dataclass
class _Entry:
    record: ApprovalRecord
    future: asyncio.Future
    timer: asyncio.TimerHandle


class ApprovalService:
    def __init__(self, timeout_s: float = 60.0):
        self._timeout = timeout_s
        self._pending: dict[str, _Entry] = {}
        self._created: list[Callable[[RequestContext], None]] = []
        self._resolved: list[Callable[[ApprovalRecord], None]] = []

    def on_created(self, cb): self._created.append(cb)
    def on_resolved(self, cb): self._resolved.append(cb)

    @staticmethod
    def _emit(cbs, arg):
        for cb in cbs:
            try:
                cb(arg)
            except Exception:  # a UI/audit bug must never change a decision
                log.exception("listener failed")

    async def request_approval(self, ctx: RequestContext) -> Decision:
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        timer = loop.call_later(
            self._timeout, self._settle, ctx.id, Status.TIMED_OUT, "No response from user"
        )
        self._pending[ctx.id] = _Entry(ApprovalRecord(ctx), fut, timer)
        self._emit(self._created, ctx)
        try:
            return await fut
        except asyncio.CancelledError:  # HTTP client went away: close the popup
            self._settle(ctx.id, Status.ERRORED, "Client disconnected")
            raise

    def decide(self, request_id: str, allow: bool, reason: Optional[str] = None) -> bool:
        if request_id not in self._pending:
            return False  # already settled (late click)
        self._settle(request_id, Status.ALLOWED if allow else Status.BLOCKED, reason)
        return True

    def fail_closed(self, request_id: str, status: Status, reason: str) -> bool:
        if request_id not in self._pending:
            return False
        self._settle(request_id, status, reason)
        return True

    def list_pending(self) -> list[RequestContext]:
        return [e.record.request for e in self._pending.values()]

    def shutdown(self) -> None:
        for rid in list(self._pending):
            self._settle(rid, Status.ERRORED, "Firewall shutting down")

    def _settle(self, rid: str, status: Status, reason: Optional[str] = None) -> None:
        entry = self._pending.pop(rid, None)
        if entry is None:
            return
        entry.timer.cancel()
        rec = entry.record
        rec.status, rec.decided_at, rec.reason = status, time.time(), reason
        self._emit(self._resolved, rec)
        if not entry.future.done():
            entry.future.set_result(Decision(status == Status.ALLOWED, status, reason))