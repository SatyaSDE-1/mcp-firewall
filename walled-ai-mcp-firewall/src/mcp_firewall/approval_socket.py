
"""Socket.IO bridge between approval decisions and the Electron popup."""

import asyncio
import hmac
import logging

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
    def __init__(
        self,
        service: ApprovalService,
        token: str,
        loop: asyncio.AbstractEventLoop,
    ):
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
            # Reject connections if the approval token is missing.
            if not isinstance(self.token, str) or len(self.token) < 32:
                log.warning("Socket connection rejected: server token unavailable")
                return False

            # Validate the client's authentication payload.
            if not isinstance(auth, dict):
                log.warning("Socket connection rejected: invalid authentication payload")
                return False

            provided = auth.get("token")

            if not isinstance(provided, str) or not hmac.compare_digest(
                provided,
                self.token,
            ):
                log.warning("Socket connection rejected: invalid approval token")
                return False

            log.info("Authenticated Electron approval UI connected: sid=%s", sid)

            pending = self.service.list_pending()
            log.info("Replaying %d pending approval(s) to Electron", len(pending))

            # Send existing pending approvals to the newly connected client.
            for ctx in pending:
                await self.sio.emit(
                    "approval:request",
                    context_payload(ctx),
                    to=sid,
                )

            return True

        @self.sio.on("approval:decision")
        async def decide(sid, payload):
            if not isinstance(payload, dict):
                log.warning("Ignoring invalid approval decision from sid=%s", sid)
                return

            request_id = payload.get("requestId")
            decision = payload.get("decision")

            if not isinstance(request_id, str):
                log.warning("Ignoring approval decision without requestId: sid=%s", sid)
                return

            if decision not in ("allow", "block"):
                log.warning("Ignoring invalid approval decision: sid=%s requestId=%s", sid, request_id)
                return

            log.info(
                "Electron decision received: sid=%s requestId=%s decision=%s",
                sid,
                request_id,
                decision,
            )

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
        log.info("Approval request created: requestId=%s method=%s tool=%s", ctx.id, ctx.method, ctx.tool_name or "-")
        coroutine = self.sio.emit("approval:request", payload)
        asyncio.run_coroutine_threadsafe(coroutine, self.loop)

    def on_resolved(self, rec: ApprovalRecord) -> None:
        payload = {
            "requestId": rec.request.id,
            "status": rec.status.value,
        }
        log.info("Approval resolved: requestId=%s status=%s", rec.request.id, rec.status.value)
        coroutine = self.sio.emit("approval:resolved", payload)
        asyncio.run_coroutine_threadsafe(coroutine, self.loop)
