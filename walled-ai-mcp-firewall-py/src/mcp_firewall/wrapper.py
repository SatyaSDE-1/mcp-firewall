



# """STDIO wrapper: Claude Desktop <-> MCP Firewall <-> real MCP server.

# Usage:
#     mcp-firewall-wrapper --name <server> -- <command> [args...]

# stdout carries ONLY JSON-RPC messages.
# Logs are written to stderr.
# """

# import json
# import os
# import signal
# import subprocess
# import sys
# import threading
# import urllib.error
# import urllib.request


# FIREWALL_URL = os.environ.get(
#     "FIREWALL_URL",
#     "http://127.0.0.1:5000/mcp/intercept",
# )

# TOKEN = os.environ.get("APPROVAL_TOKEN")

# # Operations that must be denied if the firewall cannot be reached.
# GATED = {
#     "tools/call",
#     "resources/read",
#     "prompts/get",
# }

# _out_lock = threading.Lock()
# _in_lock = threading.Lock()


# def log(message: str) -> None:
#     """Write diagnostic messages to stderr only."""
#     print(f"[firewall-wrapper] {message}", file=sys.stderr, flush=True)


# def emit(obj) -> None:
#     """Write one JSON-RPC message to Claude Desktop."""
#     output = obj if isinstance(obj, str) else json.dumps(obj)

#     with _out_lock:
#         sys.stdout.write(output + "\n")
#         sys.stdout.flush()


# def deny(rpc_id, message: str) -> dict:
#     """Create a JSON-RPC error response."""
#     return {
#         "jsonrpc": "2.0",
#         "id": rpc_id,
#         "error": {
#             "code": -32001,
#             "message": message,
#         },
#     }


# def to_server(child: subprocess.Popen, line: str) -> None:
#     """Forward a JSON-RPC message to the actual MCP server."""
#     if child.stdin is None:
#         raise RuntimeError("MCP server stdin is unavailable")

#     with _in_lock:
#         child.stdin.write(line + "\n")
#         child.stdin.flush()


# def ask_firewall(server_name: str, message: dict) -> dict:
#     """Send the incoming JSON-RPC message to the approval firewall."""
#     headers = {
#         "Content-Type": "application/json",
#     }

#     if TOKEN:
#         headers["x-firewall-token"] = TOKEN

#     payload = {
#         "source": "claude-desktop",
#         "server_name": server_name,
#         "message": message,
#     }

#     body = json.dumps(payload).encode("utf-8")

#     request = urllib.request.Request(
#         FIREWALL_URL,
#         data=body,
#         headers=headers,
#         method="POST",
#     )

#     # Firewall approval timeout is 60 seconds.
#     with urllib.request.urlopen(request, timeout=90) as response:
#         result = json.load(response)

#     if not isinstance(result, dict):
#         raise ValueError("Firewall returned an invalid response")

#     return result


# def main() -> None:
#     argv = sys.argv[1:]

#     # Parse:
#     # --name filesystem -- /path/to/command arg1 arg2
#     if "--" not in argv:
#         log("Missing '--' separator in command arguments")
#         sys.exit(2)

#     separator = argv.index("--")

#     if separator == len(argv) - 1:
#         log("Missing MCP server command after '--'")
#         sys.exit(2)

#     options = argv[:separator]
#     command_args = argv[separator + 1:]

#     command = command_args[0]
#     cmd_args = command_args[1:]

#     if "--name" in options:
#         name_index = options.index("--name")

#         if name_index + 1 < len(options):
#             server_name = options[name_index + 1]
#         else:
#             server_name = command
#     else:
#         server_name = command

#     log(f"Starting MCP server: {server_name}")
#     log(f"Firewall URL: {FIREWALL_URL}")

#     if not TOKEN:
#         log("WARNING: APPROVAL_TOKEN is not configured")

#     # The real MCP server must not inherit the firewall token.
#     child_env = {
#         key: value
#         for key, value in os.environ.items()
#         if key not in ("APPROVAL_TOKEN", "FIREWALL_URL")
#     }

#     try:
#         child = subprocess.Popen(
#             [command, *cmd_args],
#             stdin=subprocess.PIPE,
#             stdout=subprocess.PIPE,
#             stderr=None,
#             env=child_env,
#             text=True,
#             encoding="utf-8",
#             bufsize=1,
#         )
#     except OSError as error:
#         log(f'Failed to start "{command}": {error}')
#         sys.exit(1)

#     log(f"MCP server started with PID: {child.pid}")

#     # Forward server responses to Claude Desktop.
#     def pump_server_output() -> None:
#         try:
#             if child.stdout is None:
#                 return

#             for line in child.stdout:
#                 emit(line.rstrip("\r\n"))

#         except Exception as error:
#             log(f"Error reading MCP server output: {error}")

#         finally:
#             log("MCP server stdout closed")

#     output_thread = threading.Thread(
#         target=pump_server_output,
#         daemon=True,
#     )
#     output_thread.start()

#     # Forward shutdown signals to the real MCP server.
#     def handle_signal(sig, _frame):
#         log(f"Received signal: {sig}")

#         if child.poll() is None:
#             try:
#                 child.send_signal(sig)
#             except OSError as error:
#                 log(f"Could not forward signal: {error}")

#     for sig in (signal.SIGINT, signal.SIGTERM):
#         signal.signal(sig, handle_signal)

#     def handle_message(line: str) -> None:
#         if not line.strip():
#             return

#         try:
#             message = json.loads(line)
#         except json.JSONDecodeError:
#             log("Received invalid JSON from Claude Desktop")
#             emit(
#                 {
#                     "jsonrpc": "2.0",
#                     "id": None,
#                     "error": {
#                         "code": -32700,
#                         "message": "Parse error",
#                     },
#                 }
#             )
#             return

#         if not isinstance(message, dict):
#             log("Unsupported JSON-RPC batch or message format")
#             emit(deny(None, "Unsupported message format"))
#             return

#         method = message.get("method")
#         rpc_id = message.get("id")

#         log(f"Received MCP message: {method or 'response'}")

#         try:
#             decision = ask_firewall(server_name, message)

#             action = decision.get("action")

#             if action == "forward":
#                 log(f"Firewall approved: {method}")
#                 to_server(child, line)

#             elif "response" in decision:
#                 log(f"Firewall returned a response for: {method}")
#                 emit(decision["response"])

#             else:
#                 log(f"Invalid firewall decision for: {method}")

#                 if rpc_id is not None:
#                     emit(deny(rpc_id, "Invalid firewall response"))

#         except urllib.error.HTTPError as error:
#             log(
#                 f"Firewall HTTP error: {error.code} "
#                 f"{error.reason}"
#             )

#             if rpc_id is not None:
#                 if method in GATED:
#                     emit(
#                         deny(
#                             rpc_id,
#                             "Firewall unavailable; request denied",
#                         )
#                     )
#                 else:
#                     log(f"Forwarding non-protected message: {method}")
#                     to_server(child, line)

#         except Exception as error:
#             log(f"Firewall request failed: {error}")

#             if rpc_id is not None and method in GATED:
#                 emit(
#                     deny(
#                         rpc_id,
#                         "Firewall unavailable; request denied",
#                     )
#                 )
#             else:
#                 # Initialization and other non-protected messages
#                 # can still reach the MCP server.
#                 log(f"Forwarding non-protected message: {method}")
#                 to_server(child, line)

#     try:
#         # Process messages sequentially to preserve STDIO order.
#         for incoming_line in sys.stdin:
#             if child.poll() is not None:
#                 log("MCP server process has exited")
#                 break

#             handle_message(incoming_line.rstrip("\r\n"))

#     except KeyboardInterrupt:
#         log("Wrapper interrupted")

#     except Exception as error:
#         log(f"Wrapper error: {error}")

#     finally:
#         log("Shutting down wrapper")

#         if child.stdin is not None:
#             try:
#                 child.stdin.close()
#             except (BrokenPipeError, OSError, ValueError):
#                 pass

#         if child.poll() is None:
#             try:
#                 child.terminate()
#                 child.wait(timeout=5)
#             except subprocess.TimeoutExpired:
#                 log("MCP server did not stop; killing it")
#                 child.kill()
#                 child.wait()
#             except OSError as error:
#                 log(f"Error stopping MCP server: {error}")

#         output_thread.join(timeout=2)
#         log("Wrapper stopped")


# if __name__ == "__main__":
#     main()



"""STDIO wrapper: Claude Desktop <-> MCP Firewall <-> real MCP server.

Usage:
    mcp-firewall-wrapper --name <server> -- <command> [args...]

stdout carries ONLY JSON-RPC messages.
Logs are written to stderr.
"""

import json
import os
import signal
import subprocess
import sys
import threading
import urllib.request


FIREWALL_URL = os.environ.get(
    "FIREWALL_URL",
    "http://127.0.0.1:5000/mcp/intercept",
)

TOKEN = os.environ.get("APPROVAL_TOKEN")

# Operations that require firewall approval.
GATED = {
    "tools/call",
    "resources/read",
    "prompts/get",
}

_out_lock = threading.Lock()
_in_lock = threading.Lock()


def log(message: str) -> None:
    """Write diagnostic messages to stderr only."""
    print(
        f"[firewall-wrapper] {message}",
        file=sys.stderr,
        flush=True,
    )


def emit(obj) -> None:
    """Write one JSON-RPC message to Claude Desktop."""
    output = obj if isinstance(obj, str) else json.dumps(obj)

    with _out_lock:
        sys.stdout.write(output + "\n")
        sys.stdout.flush()


def deny(rpc_id, message: str) -> dict:
    """Create a JSON-RPC error response."""
    return {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "error": {
            "code": -32001,
            "message": message,
        },
    }


def to_server(child: subprocess.Popen, line: str) -> None:
    """Forward a JSON-RPC message to the actual MCP server."""
    if child.stdin is None:
        raise RuntimeError("MCP server stdin is unavailable")

    with _in_lock:
        child.stdin.write(line + "\n")
        child.stdin.flush()


def ask_firewall(server_name: str, message: dict) -> dict:
    """Send the incoming JSON-RPC message to the approval firewall."""

    headers = {
        "Content-Type": "application/json",
    }

    if TOKEN:
        headers["x-firewall-token"] = TOKEN

    payload = {
        "source": "claude-desktop",
        "server_name": server_name,
        "message": message,
    }

    body = json.dumps(payload).encode("utf-8")

    request = urllib.request.Request(
        FIREWALL_URL,
        data=body,
        headers=headers,
        method="POST",
    )

    # The firewall approval timeout is configured separately.
    # This timeout allows time for the user to make a decision.
    with urllib.request.urlopen(request, timeout=90) as response:
        result = json.load(response)

    if not isinstance(result, dict):
        raise ValueError("Firewall returned an invalid response")

    return result


def main() -> None:
    argv = sys.argv[1:]

    # Parse:
    # --name filesystem -- /path/to/command arg1 arg2

    if "--" not in argv:
        log("Missing '--' separator in command arguments")
        sys.exit(2)

    separator = argv.index("--")

    if separator == len(argv) - 1:
        log("Missing MCP server command after '--'")
        sys.exit(2)

    options = argv[:separator]
    command_args = argv[separator + 1:]

    command = command_args[0]
    cmd_args = command_args[1:]

    if "--name" in options:
        name_index = options.index("--name")

        if name_index + 1 < len(options):
            server_name = options[name_index + 1]
        else:
            server_name = command
    else:
        server_name = command

    log(f"Starting MCP server: {server_name}")
    log(f"Firewall URL: {FIREWALL_URL}")

    if not TOKEN:
        log("WARNING: APPROVAL_TOKEN is not configured")

    # The real MCP server must not inherit the firewall token.
    child_env = {
        key: value
        for key, value in os.environ.items()
        if key not in ("APPROVAL_TOKEN", "FIREWALL_URL")
    }

    try:
        child = subprocess.Popen(
            [command, *cmd_args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            env=child_env,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )

    except OSError as error:
        log(f'Failed to start "{command}": {error}')
        sys.exit(1)

    log(f"MCP server started with PID: {child.pid}")

    # Forward server responses to Claude Desktop.
    def pump_server_output() -> None:
        try:
            if child.stdout is None:
                return

            for line in child.stdout:
                emit(line.rstrip("\r\n"))

        except Exception as error:
            log(f"Error reading MCP server output: {error}")

        finally:
            log("MCP server stdout closed")

    output_thread = threading.Thread(
        target=pump_server_output,
        daemon=True,
    )

    output_thread.start()

    # Forward shutdown signals to the real MCP server.
    def handle_signal(sig, _frame):
        log(f"Received signal: {sig}")

        if child.poll() is None:
            try:
                child.send_signal(sig)
            except OSError as error:
                log(f"Could not forward signal: {error}")

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, handle_signal)

    def handle_message(line: str) -> None:
        if not line.strip():
            return

        try:
            message = json.loads(line)

        except json.JSONDecodeError:
            log("Received invalid JSON from Claude Desktop")

            emit({
                "jsonrpc": "2.0",
                "id": None,
                "error": {
                    "code": -32700,
                    "message": "Parse error",
                },
            })
            return

        if not isinstance(message, dict):
            log("Unsupported JSON-RPC batch or message format")
            emit(deny(None, "Unsupported message format"))
            return

        method = message.get("method")
        rpc_id = message.get("id")

        log(f"Received MCP message: {method or 'response'}")

        try:
            decision = ask_firewall(server_name, message)

            action = decision.get("action")

            if action == "forward":
                log(f"Firewall approved: {method}")
                to_server(child, line)

            elif "response" in decision:
                log(f"Firewall returned a response for: {method}")
                emit(decision["response"])

            else:
                log(f"Invalid firewall decision for: {method}")

                if rpc_id is not None:
                    emit(
                        deny(
                            rpc_id,
                            "Invalid firewall response",
                        )
                    )

        except Exception as error:
            log(f"Firewall request failed: {error}")

            # SECURITY: Never forward protected operations
            # when the firewall is unavailable or returns an error.
            if method in GATED:

                if rpc_id is not None:
                    emit(
                        deny(
                            rpc_id,
                            "Firewall unavailable; request denied",
                        )
                    )
                else:
                    # JSON-RPC notifications have no response ID.
                    # Drop them rather than executing without approval.
                    log(
                        "Dropping gated notification; "
                        "firewall unavailable"
                    )

                return

            # Non-protected messages can still reach the MCP server.
            log(f"Forwarding non-protected message: {method}")
            to_server(child, line)

    try:
        # Process messages sequentially to preserve STDIO order.
        for incoming_line in sys.stdin:

            if child.poll() is not None:
                log("MCP server process has exited")
                break

            handle_message(incoming_line.rstrip("\r\n"))

    except KeyboardInterrupt:
        log("Wrapper interrupted")

    except Exception as error:
        log(f"Wrapper error: {error}")

    finally:
        log("Shutting down wrapper")

        if child.stdin is not None:
            try:
                child.stdin.close()

            except (BrokenPipeError, OSError, ValueError):
                pass

        if child.poll() is None:
            try:
                child.terminate()
                child.wait(timeout=5)

            except subprocess.TimeoutExpired:
                log("MCP server did not stop; killing it")
                child.kill()
                child.wait()

            except OSError as error:
                log(f"Error stopping MCP server: {error}")

        output_thread.join(timeout=2)

        log("Wrapper stopped")


if __name__ == "__main__":
    main()
