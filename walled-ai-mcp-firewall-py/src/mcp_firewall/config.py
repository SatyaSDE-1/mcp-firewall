# /Users/anurag/task/walled-ai-mcp-firewall-py/src/mcp_firewall/config.py
"""Environment-driven configuration."""
import json
import logging
import os

log = logging.getLogger("config")

PORT = int(os.environ.get("PORT", 5000))
REMOTE_PORT = int(os.environ.get("REMOTE_PORT", 5001))
TOKEN = os.environ.get("APPROVAL_TOKEN")  # shared secret: wrapper -> firewall
APPROVAL_TIMEOUT_S = float(os.environ.get("APPROVAL_TIMEOUT_MS", 60_000)) / 1000
AUDIT_DIR = os.environ.get("AUDIT_LOG_DIR", os.path.join(os.getcwd(), "logs"))
AUDIT_MAX_PARAMS_CHARS = int(os.environ.get("AUDIT_MAX_PARAMS_CHARS", 2000))
GATED_METHODS = {
    m.strip()
    for m in os.environ.get("GATED_METHODS", "tools/call,resources/read,prompts/get").split(",")
    if m.strip()
}


def remote_targets() -> tuple[dict, str]:
    """Returns ({name: {url, headers?}}, secret). Empty dict = Claude Web proxy disabled."""
    raw = os.environ.get("REMOTE_SERVERS")
    secret = os.environ.get("REMOTE_PATH_SECRET", "")
    if not raw:
        log.info("REMOTE_SERVERS not set: Claude Web proxy disabled")
        return {}, ""
    if len(secret) < 16:
        log.error("REMOTE_PATH_SECRET must be >= 16 chars: Claude Web proxy disabled")
        return {}, ""
    try:
        targets = json.loads(raw)
        for t in targets.values():
            if not str(t["url"]).startswith(("http://", "https://")):
                raise ValueError("bad url")
        return targets, secret
    except Exception as e:  # noqa: BLE001
        log.error("Invalid REMOTE_SERVERS: %s", e)
        return {}, ""