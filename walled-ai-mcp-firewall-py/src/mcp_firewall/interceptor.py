
"""Decides whether to forward or deny a JSON-RPC message."""

import json
import logging
import time
import uuid
from typing import Any, Optional

from .approval import ApprovalService
from .models import (
    InterceptResult,
    RequestContext,
    Status,
    deny_response,
)

log = logging.getLogger("interceptor")


def _to_object(raw: Any) -> Optional[dict]:
    """Convert JSON strings or bytes into a dictionary."""
    if isinstance(raw, (str, bytes)):
        try:
            raw = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            return None

    return raw if isinstance(raw, dict) else None


def _parse_request(raw: Any) -> Optional[dict]:
    """Validate an incoming JSON-RPC request."""
    message = _to_object(raw)

    if (
        not message
        or message.get("jsonrpc") != "2.0"
        or not isinstance(message.get("method"), str)
    ):
        return None

    return message


def _is_response(raw: Any) -> bool:
    """Identify JSON-RPC responses to server-initiated requests."""
    message = _to_object(raw)

    return (
        bool(message)
        and message.get("jsonrpc") == "2.0"
        and "method" not in message
        and ("result" in message or "error" in message)
    )


def _deny_message(status: Status, reason: Optional[str]) -> str:
    """Build a readable denial message."""
    if status == Status.BLOCKED:
        return f"Blocked by user: {reason}" if reason else "Blocked by user"

    if status == Status.TIMED_OUT:
        return "Request denied: no response from user in time"

    if reason:
        return f"Request denied by firewall: {reason}"

    return "Request denied by firewall"


class Interceptor:
    """Handles approval decisions for incoming MCP messages."""

    def __init__(
        self,
        approvals: ApprovalService,
        gated_methods: set[str],
    ):
        self._approvals = approvals
        self._gated = gated_methods

    async def intercept(
        self,
        raw: Any,
        source: str,
        server_name: str,
    ) -> InterceptResult:

        message = _parse_request(raw)

        # Invalid requests fail closed.
        if message is None:

            # Responses to server-initiated requests should pass through.
            if _is_response(raw):
                return InterceptResult(
                    "forward",
                    request=raw,
                )

            return InterceptResult(
                "deny",
                status=Status.ERRORED,
                response=deny_response(
                    None,
                    "Malformed JSON-RPC message",
                ),
            )

        method = message["method"]

        # Notifications have no request ID and cannot receive a response.
        # Non-protected methods also pass through without approval.
        if "id" not in message or method not in self._gated:
            return InterceptResult(
                "forward",
                request=message,
            )

        params = message.get("params")

        tool_name = None

        if (
            method == "tools/call"
            and isinstance(params, dict)
            and isinstance(params.get("name"), str)
        ):
            tool_name = params["name"]

        # Build approval context.
        context = RequestContext(
            id=str(uuid.uuid4()),
            rpc_id=message["id"],
            source=source,
            server_name=server_name,
            method=method,
            tool_name=tool_name,
            params=params,
            received_at=time.time(),
        )

        # Ask the user for approval.
        try:
            decision = await self._approvals.request_approval(context)

        except Exception:
            log.exception("Approval flow failed")

            return InterceptResult(
                "deny",
                status=Status.ERRORED,
                response=deny_response(
                    message["id"],
                    "Firewall error; request denied",
                    method,
                ),
            )

        # Forward only when the user explicitly allows the request.
        if decision.allowed:
            log.info(
                "Request approved: method=%s server=%s",
                method,
                server_name,
            )

            return InterceptResult(
                "forward",
                request=message,
            )

        # Deny the request.
        denial_message = _deny_message(
            decision.status,
            decision.reason,
        )

        log.info(
            "Request denied: method=%s status=%s server=%s",
            method,
            decision.status.value,
            server_name,
        )

        return InterceptResult(
            "deny",
            status=decision.status,
            response=deny_response(
                message["id"],
                denial_message,
                method,
            ),
        )
