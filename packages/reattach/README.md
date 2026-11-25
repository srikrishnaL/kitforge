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

`Reattach(app, secret=None, *, path=None, max_sessions=10000, on_reattach=None, strict=False)`

- `secret` signs session tokens with HMAC-SHA256. Default: the `REATTACH_SECRET` env var.
  Without one, tokens are unsigned (see Limits). Use the same secret on every replica and
  across deploys, or old tokens stop being recognised (they then pass through as before).
- `path` handles only that URL path (for example `"/mcp"`); other paths pass through.
- `max_sessions` caps the in-memory map from tokens to server sessions. The oldest entries
  are dropped first; a dropped token just reattaches on its next request.
- `on_reattach(token, session_id)` is called after each replayed handshake (logs, metrics).
  The `reattached` attribute counts them.
- `strict=True` reattaches only on `404` (see "Servers that don't answer 404" below).

`reattach proxy UPSTREAM` takes `--host` (default 127.0.0.1), `--port` (default 8080),
`--path`, `--secret`, `--timeout` (upstream read timeout; default none, for SSE streams),
`--strict` and `--quiet`.

### Servers that don't answer 404

The spec says a server must answer `404` for a session it no longer has, and that `404` is
the only thing that tells a client to start over. Many servers say it differently, so even
clients that do handle the `404` stay stuck. reattach also recognises these, from the first
request after the session was lost:

| Server answers | Seen in |
|---|---|
| `400` "Bad Request: No valid session ID provided" | MCP Python SDK before 1.11, TypeScript SDK example servers and their many copies |
| `400` "Invalid or missing session ID", `invalid_session` | hand-written Express/Node transports |
| `410` "Session expired" | servers that use Gone for expiry |
| `200` or `500` with a JSON-RPC error "Session not found", "MCP session not found: &lt;id&gt;" | Rust and Java servlet transports |

So putting reattach in front of such a server fixes "Session not found" for every client,
without touching the server. A JSON-RPC *result* is never mistaken for this (a tool may well
say "session not found" about something else), nor is a `200` or `500` error whose message is
about something else, nor any streamed (SSE) reply. If you'd rather trust only `404`, pass
`strict=True` (or `--strict`).

## How it works

1. On `initialize`, the server's reply is passed through, but its `Mcp-Session-Id` is
   swapped for a token: `ra1.<base64url JSON>.<signature>`. The JSON holds a random 128-bit
   nonce and the client's `protocolVersion`, `capabilities` and `clientInfo` name/version.
2. Later requests carry the token. reattach maps it to the server's session id and back on
   the way out.
3. If the token is unknown here (restart, other replica) or the server answers `404`
   (expired session, or one of the replies above), reattach sends `initialize` and `notifications/initialized` from the
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
- [mcp-use/mcp-use#3154](https://github.com/mcp-use/mcp-use/issues/3154) and [slopus/happy-agent#121](https://github.com/slopus/happy-agent/issues/121): clients that keep a dead session after a 404
- [apify/actor-templates#1174](https://github.com/apify/actor-templates/issues/1174): "Session not found" once a hosted server scales to more than one instance

And servers that never send the `404` in the first place, so no client can recover:

- [YosefHayim/ebay-mcp#228](https://github.com/YosefHayim/ebay-mcp/issues/228): `400` instead of `404` for an unknown session after a restart
- [mortonanalytics/ione#166](https://github.com/mortonanalytics/ione/issues/166): `200` with JSON-RPC error "MCP session not found"
- [gradusnikov/eclipse-chatgpt-plugin#189](https://github.com/gradusnikov/eclipse-chatgpt-plugin/issues/189): JSON-RPC error "Session not found: &lt;id&gt;" after a restart or idle sweep

It also removes the need for sticky sessions behind a load balancer: any replica with the same
secret can serve any token.

Part of [kitforge](https://github.com/srikrishnaL/kitforge). MIT licensed.
