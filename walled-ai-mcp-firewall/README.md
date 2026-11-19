# Walled AI MCP Firewall

A Python MCP approval gateway that intercepts gated JSON-RPC requests from Claude Desktop and Claude Web, displays an Electron approval popup, and forwards only explicitly allowed requests to the original MCP server.

## Features

- MCP interception for Claude Desktop and Claude Web
- Electron popup for Allow/Block decisions
- Concurrent pending-request handling
- 60-second approval timeout with fail-closed behavior
- Separate MCP request and Electron approval credentials
- JSON-RPC denial responses
- Audit logging to `logs/audit.jsonl`
- Loopback-only firewall and remote proxy services
- Claude Web reverse-proxy support through ngrok or Cloudflare

## Architecture

### Claude Desktop

```text
Claude Desktop
  -> mcp-firewall-wrapper
  -> HTTP POST /mcp/intercept
  -> Python ApprovalService
  -> Socket.IO approval:request
  -> Electron popup
  -> Allow/Block decision
  -> Approved request forwarded to real MCP server
  -> MCP result returned through wrapper to Claude Desktop
```

### Claude Web

```text
Claude Web
  -> ngrok or Cloudflare tunnel
  -> remote proxy /remote/<name>/<secret>
  -> Python ApprovalService
  -> Electron popup
  -> Allow/Block decision
  -> Approved request forwarded upstream MCP server
```

The local firewall listens only on `127.0.0.1`. The remote proxy is also loopback-only; a tunnel exposes only the selected proxy route.

## Project Components

- `src/mcp_firewall/main.py`: application entry point
- `src/mcp_firewall/server.py`: HTTP and remote proxy servers
- `src/mcp_firewall/wrapper.py`: Claude Desktop stdio wrapper
- `src/mcp_firewall/interceptor.py`: JSON-RPC request interception
- `src/mcp_firewall/approval.py`: pending requests and decisions
- `src/mcp_firewall/approval_socket.py`: Electron Socket.IO bridge
- `src/mcp_firewall/audit.py`: audit JSONL logging
- `src/mcp_firewall/ui.py`: fallback Tkinter approval interface
- `tests/`: automated approval tests

## Requirements

- macOS or Windows
- Python 3.10 or newer
- Node.js 20 or newer
- Claude Desktop or Claude Web access
- ngrok or Cloudflare Tunnel for Claude Web
- Electron dependencies for the popup project

## 1. Install the Python firewall

```bash
cd /Users/anurag/task/walled-ai-mcp-firewall-py
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]' pytest-asyncio
```

Windows PowerShell:

```powershell
cd C:\path\to\walled-ai-mcp-firewall-py
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]" pytest-asyncio
```

## 2. Configure credentials

Create a secrets file at `~/.config/walled-ai-mcp-firewall/secrets.env`:

```bash
mkdir -p ~/.config/walled-ai-mcp-firewall
cat > ~/.config/walled-ai-mcp-firewall/secrets.env <<'EOF'
export MCP_REQUEST_TOKEN='replace-with-a-random-64-character-token'
export APPROVAL_TOKEN='replace-with-a-different-random-64-character-token'
EOF
chmod 600 ~/.config/walled-ai-mcp-firewall/secrets.env
source ~/.config/walled-ai-mcp-firewall/secrets.env
```

The credentials are intentionally separate:

- `MCP_REQUEST_TOKEN` authenticates Claude Desktop wrappers to FastAPI.
- `APPROVAL_TOKEN` authenticates Electron to Socket.IO.
- Never add `APPROVAL_TOKEN` to Claude Desktop configuration.

Credentials should be at least 32 characters. Do not commit real credentials to Git.

## 3. Install and start Electron

```bash
cd /Users/anurag/task/walled-ai-mcp-popup
npm install
source ~/.config/walled-ai-mcp-firewall/secrets.env
export FIREWALL_WS_URL='http://127.0.0.1:5000'
npm start
```

Keep the Electron process running. It reconnects automatically and shows pending approvals after reconnecting.

## 4. Start the local firewall

```bash
cd /Users/anurag/task/walled-ai-mcp-firewall-py
source .venv/bin/activate
source ~/.config/walled-ai-mcp-firewall/secrets.env
export PORT=5000
export FIREWALL_URL='http://127.0.0.1:5000/mcp/intercept'
mcp-firewall
```

Verify:

```bash
curl http://127.0.0.1:5000/health
```

Expected response:

```json
{ "ok": true }
```

If port `5000` is occupied, find and stop the old process:

```bash
lsof -nP -iTCP:5000 -sTCP:LISTEN
kill <PID>
```

## 5. Claude Desktop setup

Edit Claude Desktop MCP configuration:

```text
~/Library/Application Support/Claude/claude_desktop_config.json
```

Example filesystem configuration:

```json
{
  "mcpServers": {
    "firewall-filesystem": {
      "command": "/Users/anurag/task/walled-ai-mcp-firewall-py/.venv/bin/mcp-firewall-wrapper",
      "args": [
        "--name",
        "filesystem",
        "--",
        "/Users/anurag/.nvm/versions/node/v22.22.2/bin/npx",
        "-y",
        "@modelcontextprotocol/server-filesystem",
        "/Users/anurag/mcp-firewall-test"
      ],
      "env": {
        "MCP_REQUEST_TOKEN": "replace-with-the-MCP_REQUEST_TOKEN-value",
        "FIREWALL_URL": "http://127.0.0.1:5000/mcp/intercept",
        "PATH": "/Users/anurag/.nvm/versions/node/v22.22.2/bin:/usr/bin:/bin"
      }
    }
  }
}
```

Use absolute paths. Do not include `APPROVAL_TOKEN` here.

Fully quit Claude Desktop and reopen it after changing the MCP configuration.

To test, ask Claude:

```text
Read /Users/anurag/mcp-firewall-test/note.txt using the filesystem tool
```

## 6. Stage test: firewall and popup

Start the firewall and Electron, then run:

```bash
source ~/.config/walled-ai-mcp-firewall/secrets.env
curl --max-time 90 \
  -H 'content-type: application/json' \
  -H "x-firewall-token: $MCP_REQUEST_TOKEN" \
  -d '{"source":"claude-desktop","server_name":"demo","message":{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"read_note","arguments":{"name":"a"}}}}' \
  http://127.0.0.1:5000/mcp/intercept
```

Expected decisions:

- `Allow`: `{"action":"forward"}`
- `Block`: JSON-RPC tool result with `isError: true`
- No decision: timed out with `TIMED_OUT`
- Wrong or missing MCP token: HTTP `403`

Multiple simultaneous requests should create separate pending popups/queue entries.

## 7. Claude Web setup

Start the real filesystem server over HTTP using Supergateway:

```bash
npx -y supergateway \
  --stdio "/Users/anurag/.nvm/versions/node/v22.22.2/bin/npx -y @modelcontextprotocol/server-filesystem /Users/anurag/mcp-firewall-test" \
  --outputTransport streamableHttp \
  --stateful \
  --port 8000 \
  --host 127.0.0.1
```

Start the firewall with remote proxy enabled:

```bash
cd /Users/anurag/task/walled-ai-mcp-firewall-py
source .venv/bin/activate
source ~/.config/walled-ai-mcp-firewall/secrets.env

export PORT=5000
export REMOTE_PORT=5001
export REMOTE_SERVERS='{"filesystem":{"url":"http://127.0.0.1:8000/mcp"}}'
export REMOTE_PATH_SECRET='replace-with-at-least-16-random-characters'

mcp-firewall
```

Start ngrok:

```bash
ngrok http 5001
```

Copy the generated HTTPS URL and construct:

```text
https://YOUR-NGROK-DOMAIN/remote/filesystem/YOUR_REMOTE_PATH_SECRET
```

Then add that URL in Claude Web:

```text
Settings -> Connectors -> Add custom connector
```

Reconnect or re-add the connector after restarting ngrok, because the public URL may change.

## 8. Claude Web verification

Test initialize through the public URL:

```bash
curl -i \
  -H 'ngrok-skip-browser-warning: true' \
  -H 'content-type: application/json' \
  -H 'accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"remote-check","version":"1.0"}}}' \
  'https://YOUR-NGROK-DOMAIN/remote/filesystem/YOUR_REMOTE_PATH_SECRET'
```

A successful response should include an `mcp-session-id` and the upstream server information.

## Tests

Run the Python tests:

```bash
cd /Users/anurag/task/walled-ai-mcp-firewall-py
source .venv/bin/activate
PYTHONPATH=src python -m pytest -q
```

Test coverage includes:

- Valid, invalid, and missing MCP request tokens
- Allow and Block decisions
- Approval timeout
- Simultaneous pending requests
- Firewall shutdown
- JSON-RPC interceptor behavior

## Audit log

Decisions are appended to:

```text
logs/audit.jsonl
```

View recent entries:

```bash
cd /Users/anurag/task/walled-ai-mcp-firewall-py
tail -n 20 logs/audit.jsonl
```

## Security notes

- MCP request authentication and Electron approval authentication are separate.
- The firewall and remote proxy bind only to `127.0.0.1`.
- The wrapper removes firewall credentials from the real MCP server environment.
- Gated requests fail closed if the firewall is unavailable.
- Tokens and secret path values are never included in audit or popup parameter logs.
- Batches are rejected rather than partially approved.
- Malformed JSON-RPC requests are denied.

## Troubleshooting

### Electron popup does not appear

- Confirm Electron is running with `APPROVAL_TOKEN` sourced.
- Confirm `FIREWALL_WS_URL=http://127.0.0.1:5000`.
- Confirm the firewall logs report `Authenticated Electron approval UI connected`.
- Restart Electron and Claude Desktop after changing credentials.

### HTTP 403 in Claude Desktop logs

- Compare `MCP_REQUEST_TOKEN` in Claude configuration with the firewall environment.
- Fully quit and reopen Claude Desktop after changing the token.
- Do not use `APPROVAL_TOKEN` in Claude configuration.

### Address already in use

```bash
lsof -nP -iTCP:5000 -iTCP:5001 -iTCP:8000 -iTCP:4040 -sTCP:LISTEN
```

Stop the intended stale process by PID:

```bash
kill <PID>
```

Do not start duplicate firewall instances.

### Claude Web 502

- Confirm port `8000` Supergateway is running.
- Confirm port `5001` is listening.
- Confirm `REMOTE_SERVERS` points to `http://127.0.0.1:8000/mcp`.
- Confirm the name and path secret match the Claude Web connector URL.
- Restart Supergateway, firewall, Electron, and ngrok in that order.

### Claude Web tool times out

- Confirm Electron is connected to port `5000`.
- Approve or block before the configured approval timeout.
- Increase timeout only for testing:

```bash
export APPROVAL_TIMEOUT_MS=300000
```

## Demonstration checklist

Record a short video showing:

1. Firewall and Electron startup.
2. Claude Desktop or Claude Web MCP tool request.
3. Popup displaying server, method, tool, and parameters.
4. Allow forwarding and successful file content.
5. Block preventing execution and a denied response.
6. Audit log entries for both decisions.
