# /Users/anurag/task/walled-ai-mcp-firewall-py/src/mcp_firewall/approval_socket.py
"""Socket.IO bridge between approval decisions and the Electron popup."""

import asyncio
import hmac
import logging
from typing import Optional

import socketio

from .approval import ApprovalService
from .models import ApprovalRecord, RequestContext

log = logging.getLogger("approval_socket")


def context_payload(ctx: RequestContext) -> dict:
    return {
        "id": ctx.id,
        "rpcId": ctx.rpc_id,
        "source": ctx.source,
        "serverName": ctx.server_name,
        "method": ctx.method,
        "toolName": ctx.tool_name,
        "params": ctx.params,
    }


class ApprovalSocketGateway:
    def __init__(self, service: ApprovalService, token: Optional[str], loop: asyncio.AbstractEventLoop):
        self.service = service
        self.token = token
        self.loop = loop
        self.sio = socketio.AsyncServer(
            async_mode="asgi",
            cors_allowed_origins=[],
            ping_interval=10,
            ping_timeout=20,
        )

        @self.sio.event
        async def connect(sid, environ, auth):
            auth = auth or {}
            provided = auth.get("token")
            if self.token:
                if not isinstance(provided, str) or not hmac.compare_digest(provided, self.token):
                    return False
            for ctx in self.service.list_pending():
                await self.sio.emit("approval:request", context_payload(ctx), to=sid)
            return True

        @self.sio.on("approval:decision")
        async def decide(sid, payload):
            if not isinstance(payload, dict):
                return
            request_id = payload.get("requestId")
            decision = payload.get("decision")
            if not isinstance(request_id, str) or decision not in ("allow", "block"):
                return
            self.loop.call_soon_threadsafe(
                self.service.decide,
                request_id,
                decision == "allow",
            )

    def mount(self, app):
        return socketio.ASGIApp(
            self.sio,
            other_asgi_app=app,
            socketio_path="approvals/socket.io",
        )

    def on_created(self, ctx: RequestContext) -> None:
        payload = context_payload(ctx)
        self.loop.call_soon_threadsafe(
            asyncio.create_task,
            self.sio.emit("approval:request", payload),
        )

    def on_resolved(self, rec: ApprovalRecord) -> None:
        payload = {"requestId": rec.request.id, "status": rec.status.value}
        self.loop.call_soon_threadsafe(
            asyncio.create_task,
            self.sio.emit("approval:resolved", payload),
        )
