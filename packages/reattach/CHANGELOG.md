# Changelog

## 0.2.0 (2026-11-25)

- Reattach also when the server says the session is gone without the `404` the spec asks
  for: `400` "Invalid or missing session ID" / `invalid_session` / "Session not found",
  `410`, and a JSON-RPC error "Session not found" (or "expired", "unknown", "invalid")
  sent with `200` or `500`. JSON-RPC results, other errors and SSE replies are never taken
  for it. Before, only `404` and the MCP Python SDK's old `400 No valid session ID provided`
  were.
- `strict=True` / `reattach proxy --strict` keeps the old behaviour (trust `404` only).

## 0.1.0 (2026-11-16)

First release.

- `Reattach(app)`: ASGI middleware for Streamable HTTP MCP servers. Gives clients a session
  token that carries their handshake, and when the server no longer knows the session
  (restart, redeploy, another replica, idle timeout) replays `initialize` and
  `notifications/initialized` and retries the request, so clients never see
  `404 Session not found`. Signed tokens (`secret` or `REATTACH_SECRET`), per-credential
  mapping, one shared handshake for concurrent requests, GET streams and DELETE handled,
  JSON and SSE replies. No dependencies.
- `reattach proxy UPSTREAM`: the same as a reverse proxy for MCP servers in any language
  (needs `reattach[proxy]`: httpx and uvicorn). Streams SSE, forwards every path, answers
  `502` while the upstream is down.
- Tested with the MCP Python SDK 1.x and 2.x.
