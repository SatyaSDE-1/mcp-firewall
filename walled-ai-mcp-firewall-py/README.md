# Walled AI MCP Firewall

A local approval gateway for Claude Desktop and Claude Web MCP requests.

## Architecture

Claude Desktop launches `mcp-firewall-wrapper`, which sends gated JSON-RPC requests to `POST /mcp/intercept`. The approval service creates a unique pending request and publishes it over Socket.IO. Electron displays the request and sends Allow/Block back to Python. Allowed requests are written to the real MCP server stdin; blocked or timed-out requests receive a JSON-RPC tool error.

Two credentials are intentionally separate:

- `MCP_REQUEST_TOKEN` authenticates the Claude wrapper to FastAPI.
- `APPROVAL_TOKEN` authenticates Electron to Socket.IO.

Never place `APPROVAL_TOKEN` in Claude Desktop configuration.

## Install

```bash
cd /Users/anurag/task/walled-ai-mcp-firewall-py
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

Create `~/.config/walled-ai-mcp-firewall/secrets.env`:

```bash
export MCP_REQUEST_TOKEN='replace-with-a-long-random-value'
export APPROVAL_TOKEN='replace-with-a-different-long-random-value'
```

Source it before starting each component.

## Run

Firewall:

```bash
cd /Users/anurag/task/walled-ai-mcp-firewall-py
source .venv/bin/activate
source ~/.config/walled-ai-mcp-firewall/secrets.env
export PORT=5000
mcp-firewall
```

Electron:

```bash
cd /Users/anurag/task/walled-ai-mcp-popup
source ~/.config/walled-ai-mcp-firewall/secrets.env
FIREWALL_WS_URL=http://127.0.0.1:5000 npm start
```

Health check:

```bash
curl http://127.0.0.1:5000/health
```

## Claude Desktop

Configure the absolute wrapper executable and MCP server command. Include only `MCP_REQUEST_TOKEN` in Claude's environment:

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
        "MCP_REQUEST_TOKEN": "replace-with-the-same-value-used-by-the-firewall"
      }
    }
  }
}
```

Fully quit Claude Desktop and reopen it after changing configuration.

## Verification

Run tests:

```bash
PYTHONPATH=src python -m pytest -q
```

Send a gated test request:

```bash
source ~/.config/walled-ai-mcp-firewall/secrets.env
curl -i http://127.0.0.1:5000/mcp/intercept \
  -H 'content-type: application/json' \
  -H "x-firewall-token: $MCP_REQUEST_TOKEN" \
  -d '{"source":"claude-desktop","server_name":"demo","message":{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"read_note","arguments":{"name":"a"}}}}'
```

Allow or block the Electron approval. Decisions are written to `logs/audit.jsonl`.

## Troubleshooting

- `403`: Claude Desktop's `MCP_REQUEST_TOKEN` differs from the firewall environment.
- Electron does not connect: verify `APPROVAL_TOKEN` and `FIREWALL_WS_URL` match the running firewall.
- Address already in use: another firewall instance owns port 5000; stop it before starting another.
- No approval popup: keep Electron running and verify Socket.IO polling returns `200`; check wrapper logs in `~/Library/Logs/Claude/mcp-server-<server>.log`.
- Wrapper unavailable: restart Claude Desktop so it starts a fresh wrapper process.
