
"""Shared data models and JSON-RPC response helpers."""

import enum
from dataclasses import dataclass
from typing import Any, Optional


# JSON-RPC error code used when a request is denied.
DENIED_CODE = -32001


class Status(str, enum.Enum):
    """Approval request status."""

    PENDING = "PENDING"
    ALLOWED = "ALLOWED"
    BLOCKED = "BLOCKED"
    TIMED_OUT = "TIMED_OUT"
    ERRORED = "ERRORED"


@dataclass
class RequestContext:
    """Information about an intercepted MCP request."""

    id: str
    rpc_id: Any
    source: str
    server_name: str
    method: str
    tool_name: Optional[str]
    params: Any
    received_at: float


@dataclass
class Decision:
    """Result of a user's approval decision."""

    allowed: bool
    status: Status
    reason: Optional[str] = None


@dataclass
class ApprovalRecord:
    """Stored approval request and its current state."""

    request: RequestContext
    status: Status = Status.PENDING
    decided_at: Optional[float] = None
    reason: Optional[str] = None


@dataclass
class InterceptResult:
    """Result returned by the MCP interceptor."""

    action: str
    request: Any = None
    response: Optional[dict] = None
    status: Optional[Status] = None


def deny_response(
    rpc_id: Any,
    message: str = "Request blocked by user",
    method: Optional[str] = None,
) -> dict:
    """
    Build a denial response for an intercepted MCP request.

    For tools/call, return a valid MCP tool result with isError=True.
    For other methods, return a JSON-RPC error response.
    """

    if method == "tools/call":
        return {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"{message}. "
                            "Do not retry this action unless the user asks."
                        ),
                    }
                ],
                "isError": True,
            },
        }

    return {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "error": {
            "code": DENIED_CODE,
            "message": message,
        },
    }
