# /Users/anurag/task/walled-ai-mcp-firewall-py/src/mcp_firewall/audit.py
"""One JSON line per settled request -> logs/audit.jsonl."""
import json
import logging
import os
import threading
from datetime import datetime, timezone

from .models import ApprovalRecord

log = logging.getLogger("audit")


class AuditLog:
    def __init__(self, directory: str, max_params_chars: int):
        os.makedirs(directory, exist_ok=True)
        self._file = os.path.join(directory, "audit.jsonl")
        self._max = max_params_chars
        self._lock = threading.Lock()
        log.info("Audit log: %s", self._file)

    def write(self, rec: ApprovalRecord) -> None:
        r = rec.request
        try:
            s = json.dumps(r.params, default=str)
            params = {"truncated": True, "preview": s[: self._max]} if len(s) > self._max else r.params
        except Exception:  # noqa: BLE001
            params = "[unserializable]"
        decided = rec.decided_at or r.received_at
        line = json.dumps({
            "time": datetime.fromtimestamp(decided, timezone.utc).isoformat(),
            "requestId": r.id, "source": r.source, "server": r.server_name,
            "method": r.method, "tool": r.tool_name,
            "status": rec.status.value, "reason": rec.reason,
            "waitedMs": int((decided - r.received_at) * 1000),
            "params": params,
        }, default=str)
        try:
            with self._lock, open(self._file, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError as e:  # audit failure never changes a decision
            log.error("Audit write failed: %s", e)