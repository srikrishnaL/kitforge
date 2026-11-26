# Changelog

## 0.2.0 (2026-11-26)

- `unstring http URL [--host] [--port] [--timeout] [--wrap] [--quiet] [--log FILE]`: a local
  reverse proxy for remote (Streamable HTTP) MCP servers. Forwards every request, header and
  SSE stream, and repairs tool-call arguments on the way. Needs the `http` extra
  (`pip install "unstring[http]"`: httpx, uvicorn).
- `unstring.asgi.Unstring(app)`: the same repair as ASGI middleware for Python servers. Learns
  schemas from `tools/list` responses (JSON or SSE, batches too); when a call names an unknown
  tool, fetches the list from the app itself with the caller's headers (session id,
  authorization, the 2026-07-28 protocol version in `_meta`), following every page and a
  `/mcp` -> `/mcp/` redirect, and asks again for a still-unknown tool only after 30 seconds.
  `tools=` preloads schemas, `fetch=False` turns the fetching off, `path=` limits it to one
  endpoint.

## 0.1.0 (2026-11-24)

First release.

- `repair(arguments, schema)` decodes tool-call arguments sent as JSON strings where the
  tool's `inputSchema` wants an array, object, integer, number, boolean or null, at any depth
  (`$ref`, `anyOf`/`oneOf`/`allOf`, `additionalProperties`, `patternProperties`,
  `prefixItems`). Never touches a value where the schema allows a string. Handles
  double-encoded JSON, arrays sent as index-keyed objects
  (`{"0": x, "1": y}`), Python literals (via `ast.literal_eval`) and a whole `arguments`
  object sent as a string. Returns the repaired value and a list of fixes; `wrap=True` also
  turns a single value into a list where an array is wanted.
- `unstring proxy [--wrap] [--quiet] [--log FILE] -- <server command>`: stdio proxy for any
  MCP server. Learns schemas from `tools/list`, fetches the list itself (all pages) when a
  call names a tool it has not seen, and holds that call until it can repair it.
- `unstring fix --schema FILE|JSON [--tool NAME] [ARGS]`: repair one set of arguments from
  the command line; exit code 2 when something was repaired.
