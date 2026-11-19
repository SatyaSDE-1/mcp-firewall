# /Users/anurag/task/walled-ai-mcp-firewall-py/tests/test_approval.py
import asyncio
import time

from mcp_firewall.approval import ApprovalService
from mcp_firewall.models import RequestContext, Status


def ctx(i="r1"):
    return RequestContext(i, 1, "claude-desktop", "t", "tools/call", "read_file", {}, time.time())


def run(coro):
    return asyncio.run(coro)


def test_allow_block_concurrent():
    async def go():
        s = ApprovalService(5)
        a = asyncio.create_task(s.request_approval(ctx("a")))
        b = asyncio.create_task(s.request_approval(ctx("b")))
        await asyncio.sleep(0)
        assert len(s.list_pending()) == 2
        s.decide("a", True)
        s.decide("b", False, "no")
        ra, rb = await a, await b
        assert ra.allowed and not rb.allowed and rb.status == Status.BLOCKED
        assert s.decide("a", False) is False  # late click ignored
    run(go())


def test_timeout_denies():
    async def go():
        s = ApprovalService(0.05)
        r = await s.request_approval(ctx())
        assert r.status == Status.TIMED_OUT and not s.list_pending()
    run(go())


def test_shutdown_denies_pending():
    async def go():
        s = ApprovalService(5)
        t = asyncio.create_task(s.request_approval(ctx()))
        await asyncio.sleep(0)
        s.shutdown()
        assert (await t).status == Status.ERRORED
    run(go())