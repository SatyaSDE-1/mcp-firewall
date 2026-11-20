# /Users/anurag/task/walled-ai-mcp-firewall-py/tests/test_approval.py
import asyncio
import time

import pytest
from mcp_firewall import config
from mcp_firewall.approval import ApprovalService
from mcp_firewall.interceptor import Interceptor
from mcp_firewall.models import RequestContext, Status
from mcp_firewall.server import create_firewall_app, create_remote_app


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


def test_simultaneous_requests_keep_distinct_ids():
    async def go():
        s = ApprovalService(5)
        first = asyncio.create_task(s.request_approval(ctx("first")))
        second = asyncio.create_task(s.request_approval(ctx("second")))
        await asyncio.sleep(0)

        assert {item.id for item in s.list_pending()} == {"first", "second"}

        s.decide("second", False)
        s.decide("first", True)

        denied, allowed = await second, await first
        assert denied.status == Status.BLOCKED
        assert allowed.status == Status.ALLOWED
        assert not s.list_pending()
    run(go())


@pytest.mark.asyncio
async def test_valid_mcp_request_token(monkeypatch):
    import httpx

    monkeypatch.setattr(config, "MCP_REQUEST_TOKEN", "x" * 32)
    service = ApprovalService(5)
    app = create_firewall_app(Interceptor(service, {"tools/call"}))

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/mcp/intercept",
            headers={"x-firewall-token": "x" * 32},
            json={
                "source": "claude-desktop",
                "server_name": "test",
                "message": {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            },
        )

    assert response.status_code == 200
    assert response.json() == {"action": "forward"}


@pytest.mark.asyncio
@pytest.mark.parametrize("token", [None, "wrong-token"])
async def test_invalid_or_missing_mcp_request_token(monkeypatch, token):
    import httpx

    monkeypatch.setattr(config, "MCP_REQUEST_TOKEN", "x" * 32)
    app = create_firewall_app(Interceptor(ApprovalService(5), {"tools/call"}))
    headers = {} if token is None else {"x-firewall-token": token}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/mcp/intercept",
            headers=headers,
            json={
                "source": "claude-desktop",
                "server_name": "test",
                "message": {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            },
        )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_gated_allow_and_block_responses(monkeypatch):
    import httpx

    monkeypatch.setattr(config, "MCP_REQUEST_TOKEN", "x" * 32)

    allow_service = ApprovalService(5)
    allow_app = create_firewall_app(Interceptor(allow_service, {"tools/call"}))

    async def allow_request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=allow_app), base_url="http://test") as client:
            task = asyncio.create_task(client.post(
                "/mcp/intercept",
                headers={"x-firewall-token": "x" * 32},
                json={"source":"claude-desktop","server_name":"test","message":{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"read_text_file","arguments":{"path":"/tmp/note.txt"}}}},
            ))
            await asyncio.sleep(0)
            allow_service.decide(allow_service.list_pending()[0].id, True)
            return await task

    allowed = await allow_request()
    assert allowed.json() == {"action": "forward"}

    block_service = ApprovalService(5)
    block_app = create_firewall_app(Interceptor(block_service, {"tools/call"}))

    async def block_request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=block_app), base_url="http://test") as client:
            task = asyncio.create_task(client.post(
                "/mcp/intercept",
                headers={"x-firewall-token": "x" * 32},
                json={"source":"claude-desktop","server_name":"test","message":{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"read_text_file"}}},
            ))
            await asyncio.sleep(0)
            block_service.decide(block_service.list_pending()[0].id, False)
            return await task

    blocked = await block_request()
    assert blocked.status_code == 200
    assert blocked.json()["action"] == "deny"
    assert blocked.json()["response"]["result"]["isError"] is True


@pytest.mark.asyncio
async def test_claude_web_block_response_is_not_forwarded(monkeypatch):
    import httpx

    monkeypatch.setattr(config, "MCP_REQUEST_TOKEN", "x" * 32)
    service = ApprovalService(5)
    interceptor = Interceptor(service, {"tools/call"})
    app = create_remote_app(interceptor, {
        "filesystem": {"url": "http://127.0.0.1:8000/mcp"},
    }, "s" * 32)

    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            task = asyncio.create_task(client.post(
                "/remote/filesystem/" + "s" * 32,
                headers={"content-type": "application/json", "accept": "application/json, text/event-stream"},
                json={
                    "jsonrpc": "2.0",
                    "id": 42,
                    "method": "tools/call",
                    "params": {"name": "read_text_file", "arguments": {"path": "/tmp/note.txt"}},
                },
            ))
            await asyncio.sleep(0)
            assert len(service.list_pending()) == 1
            service.decide(service.list_pending()[0].id, False)
            return await task

    response = await go()
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == 42
    assert body["result"]["isError"] is True
    assert "Blocked by user" in body["result"]["content"][0]["text"]