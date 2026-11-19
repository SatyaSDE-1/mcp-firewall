# /Users/anurag/task/walled-ai-mcp-firewall-py/src/mcp_firewall/server.py
"""HTTP surfaces: local /mcp/intercept (Claude Desktop wrapper) and the
public-facing Claude Web proxy (separate app/port so a tunnel exposes only it)."""
import hmac
import json
import logging
from contextlib import asynccontextmanager
from typing import Literal, Optional

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from . import config
from .interceptor import Interceptor

log = logging.getLogger("server")

MAX_BODY_BYTES = 1_000_000
REQ_HEADERS = ["content-type", "accept", "mcp-session-id", "mcp-protocol-version", "last-event-id"]
RES_HEADERS = ["content-type", "mcp-session-id", "cache-control"]


class InterceptBody(BaseModel):
    source: Literal["claude-desktop", "claude-web", "unknown"]
    server_name: str = Field(max_length=200)
    message: dict


def create_firewall_app(interceptor: Interceptor) -> FastAPI:
    app = FastAPI(title="MCP Firewall")

    @app.post("/mcp/intercept")
    async def intercept(
        body: InterceptBody,
        x_firewall_token: Optional[str] = Header(None),
    ):
        if not x_firewall_token or not hmac.compare_digest(
            x_firewall_token.encode(),
            config.MCP_REQUEST_TOKEN.encode(),
        ):
            log.warning(
                "MCP intercept authentication failed: source=%s server=%s",
                body.source,
                body.server_name,
            )
            raise HTTPException(
                status_code=403,
                detail="Invalid or missing MCP request token",
            )

        r = await interceptor.intercept(
            body.message,
            body.source,
            body.server_name,
        )

        if r.action == "forward":
            return {"action": "forward"}

        return {
            "action": "deny",
            "response": r.response,
            "status": r.status.value,
        }

    @app.get("/health")
    async def health():
        return {"ok": True}

    return app

def _rpc_error(code: int, message: str) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": code, "message": message}})


def create_remote_app(interceptor: Interceptor, targets: dict, secret: str) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app):
        _app.state.client = httpx.AsyncClient(timeout=httpx.Timeout(None, connect=10))
        yield
        await _app.state.client.aclose()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    def secret_ok(given: str) -> bool:
        return hmac.compare_digest(given.encode(), secret.encode())

    async def forward(request: Request, target: dict, body: Optional[bytes] = None):
        headers = {h: request.headers[h] for h in REQ_HEADERS if h in request.headers}
        headers.update(target.get("headers") or {})
        client: httpx.AsyncClient = request.app.state.client
        try:
            up = await client.send(
                client.build_request(request.method, target["url"], headers=headers, content=body),
                stream=True,
            )
        except httpx.HTTPError as e:
            log.warning("Upstream error: %s", e)
            return PlainTextResponse("Upstream unreachable", 502)
        out = {h: up.headers[h] for h in RES_HEADERS if h in up.headers}
        return StreamingResponse(  # streams SSE as it arrives
            up.aiter_bytes(), status_code=up.status_code, headers=out,
            background=BackgroundTask(up.aclose),
        )

    @app.api_route("/remote/{name}/{key}", methods=["GET", "POST", "DELETE"])
    async def remote(name: str, key: str, request: Request):
        target = targets.get(name)
        # unknown name and wrong secret look identical
        if target is None or not secret_ok(key):
            log.warning("404 %s /remote/%s", request.method, name[:40])  # never log the secret
            return PlainTextResponse("Not found", 404)
        log.info("%s /remote/%s", request.method, name)

        if request.method != "POST":  # GET stream / DELETE session carry no client requests
            return await forward(request, target)

        body = await request.body()
        if len(body) > MAX_BODY_BYTES:
            return PlainTextResponse("Body too large", 413)
        try:
            msg = json.loads(body)
        except ValueError:
            return _rpc_error(-32700, "Parse error")
        if isinstance(msg, list):  # batches are not reviewed: fail closed
            return _rpc_error(-32001, "Batch requests are not supported")

        r = await interceptor.intercept(msg, "claude-web", name)
        if r.action == "deny":
            return JSONResponse(r.response)  # JSON-RPC errors travel in a 200 body
        return await forward(request, target, body)

    return app