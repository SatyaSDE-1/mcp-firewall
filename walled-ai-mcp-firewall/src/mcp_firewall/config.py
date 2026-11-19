"""Environment-driven configuration."""

import json
import logging
import os

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

if load_dotenv:
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
    load_dotenv(os.path.join(project_root, ".env"))

log = logging.getLogger("config")

PORT = int(os.environ.get("PORT", 5000))
REMOTE_PORT = int(os.environ.get("REMOTE_PORT", 5001))

# Separate authentication credentials
MCP_REQUEST_TOKEN = os.environ.get("MCP_REQUEST_TOKEN")
APPROVAL_TOKEN = os.environ.get("APPROVAL_TOKEN")


def validate_credentials() -> None:
    """Ensure both firewall credentials are configured securely."""

    credentials = {
        "MCP_REQUEST_TOKEN": MCP_REQUEST_TOKEN,
        "APPROVAL_TOKEN": APPROVAL_TOKEN,
    }

    for name, value in credentials.items():
        if not value or len(value) < 32:
            raise RuntimeError(
                f"{name} must be configured with at least 32 characters"
            )


APPROVAL_TIMEOUT_S = (
    float(os.environ.get("APPROVAL_TIMEOUT_MS", 60_000)) / 1000
)

AUDIT_DIR = os.environ.get(
    "AUDIT_LOG_DIR",
    os.path.join(os.getcwd(), "logs"),
)

AUDIT_MAX_PARAMS_CHARS = int(
    os.environ.get("AUDIT_MAX_PARAMS_CHARS", 2000)
)

GATED_METHODS = {
    method.strip()
    for method in os.environ.get(
        "GATED_METHODS",
        "tools/call,resources/read,prompts/get",
    ).split(",")
    if method.strip()
}


def remote_targets() -> tuple[dict, str]:
    """
    Returns ({name: {url, headers?}}, secret).

    An empty dictionary means the Claude Web proxy is disabled.
    """

    raw = os.environ.get("REMOTE_SERVERS")
    secret = os.environ.get("REMOTE_PATH_SECRET", "")

    if not raw:
        log.info("REMOTE_SERVERS not set: Claude Web proxy disabled")
        return {}, ""

    if len(secret) < 16:
        log.error(
            "REMOTE_PATH_SECRET must be >= 16 chars: "
            "Claude Web proxy disabled"
        )
        return {}, ""

    try:
        targets = json.loads(raw)

        for target in targets.values():
            if not str(target["url"]).startswith(("http://", "https://")):
                raise ValueError("bad url")

        return targets, secret

    except Exception as e:  # noqa: BLE001
        log.error("Invalid REMOTE_SERVERS: %s", e)
        return {}, ""
