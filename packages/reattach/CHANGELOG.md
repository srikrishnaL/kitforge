# Changelog

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
