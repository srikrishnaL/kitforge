# reattach

After a Streamable HTTP MCP server restarts, redeploys or times out an idle session, clients
keep sending the old `Mcp-Session-Id` and get `404 Session not found`. Many clients never
re-initialize, so every tool call fails until the user reloads or reconnects by hand.

reattach fixes it on the server side, for every client at once. Clients get a session id that
carries their handshake; when the server no longer knows it, reattach replays `initialize`
and `notifications/initialized` and sends the request on. The client never sees the 404.

## Install

```bash
pip install reattach            # the ASGI middleware, no dependencies
pip install "reattach[proxy]"   # plus the reverse proxy for servers in any language
```

## Use

Python servers (official MCP SDK, FastMCP, or any ASGI app): wrap the app.

```python
from mcp.server.fastmcp import FastMCP   # SDK 2.x: from mcp.server.mcpserver import MCPServer
from reattach import Reattach

mcp = FastMCP("notes")

@mcp.tool()
def search(q: str):
    """Search the notes."""
    return f"found {q}"

app = Reattach(mcp.streamable_http_app())   # run with: uvicorn server:app
```

Servers in TypeScript, Go or anything else: put the proxy in front.

```bash
export REATTACH_SECRET="$(openssl rand -hex 32)"   # same value on every replica
reattach proxy http://127.0.0.1:3000 --port 8080
```

Point clients at `http://<host>:8080/mcp` (every path is forwarded, so OAuth discovery under
`/.well-known/` keeps working). The proxy can restart the upstream behind it as often as you
like; while the upstream is down, requests get a `502`, and the first request after it is back
reattaches.

### Options

`Reattach(app, secret=None, *, path=None, max_sessions=10000, on_reattach=None)`

- `secret` signs session tokens with HMAC-SHA256. Default: the `REATTACH_SECRET` env var.
  Without one, tokens are unsigned (see Limits). Use the same secret on every replica and
  across deploys, or old tokens stop being recognised (they then pass through as before).
- `path` handles only that URL path (for example `"/mcp"`); other paths pass through.
- `max_sessions` caps the in-memory map from tokens to server sessions. The oldest entries
  are dropped first; a dropped token just reattaches on its next request.
- `on_reattach(token, session_id)` is called after each replayed handshake (logs, metrics).
  The `reattached` attribute counts them.

`reattach proxy UPSTREAM` takes `--host` (default 127.0.0.1), `--port` (default 8080),
`--path`, `--secret`, `--timeout` (upstream read timeout; default none, for SSE streams) and
`--quiet`.

## How it works

1. On `initialize`, the server's reply is passed through, but its `Mcp-Session-Id` is
   swapped for a token: `ra1.<base64url JSON>.<signature>`. The JSON holds a random 128-bit
   nonce and the client's `protocolVersion`, `capabilities` and `clientInfo` name/version.
2. Later requests carry the token. reattach maps it to the server's session id and back on
   the way out.
3. If the token is unknown here (restart, other replica) or the server answers `404`
   (expired session), reattach sends `initialize` and `notifications/initialized` from the
   token, with the request's own `Authorization`, and retries the request once on the new
   session. Concurrent requests for the same token share one handshake.

Tokens are mapped per credential (`Authorization`, `X-Api-Key`, `Cookie`), so a token used
with another user's credential gets a new session of its own, never the first user's.
Session ids that are not reattach tokens, requests without a session id (stateless servers,
the 2026-07-28 protocol), and every other path pass through untouched. A server that refuses
the replayed handshake (for example `401` because the token expired) answers the client's
request itself, so OAuth re-auth still starts normally.

## Limits

- State the server kept in memory for the old session is gone: resource subscriptions,
  `logging/setLevel`, in-flight elicitation or sampling requests. Tool calls, resources and
  prompts work as before. If your server keeps no per-session state, `stateless_http=True`
  in the MCP Python SDK avoids sessions altogether and you may not need reattach.
- A GET stream that was open when the server died ends; the client's reconnect reattaches,
  but events sent in between are lost unless the server has a persistent event store.
- Without a secret anyone can make a token, which only means they can pick the handshake
  params replayed for their own requests (the same as calling `initialize` themselves). A
  session id is not a credential: authenticate every request as the spec requires.
- The middleware uses `asyncio` (uvicorn, hypercorn, granian and Starlette all do), not trio.
- Python 3.9+. Tested with the MCP Python SDK 1.x and 2.x.

## Why this exists

Clients that do not recover from `Session not found` are reported again and again, and the
server owner cannot fix the client:

- [anthropics/claude-code#62008](https://github.com/anthropics/claude-code/issues/62008): Claude Code still does not re-initialize after 404 session-not-found
- [anomalyco/opencode#25137](https://github.com/anomalyco/opencode/issues/25137) and [#37626](https://github.com/anomalyco/opencode/issues/37626): Session not found permanently breaks tool calls
- [openclaw/openclaw#68187](https://github.com/openclaw/openclaw/issues/68187): sessions stay stale after a server restart
- [zerodha/kite-mcp-server#83](https://github.com/zerodha/kite-mcp-server/issues/83) and [datagouv/datagouv-mcp#25](https://github.com/datagouv/datagouv-mcp/issues/25): users of hosted servers hitting it
- [zai-org/feedback#1374](https://github.com/zai-org/feedback/issues/1374): client never re-initializes an expired session after a server restart

It also removes the need for sticky sessions behind a load balancer: any replica with the same
secret can serve any token.

Part of [kitforge](https://github.com/srikrishnaL/kitforge). MIT licensed.
