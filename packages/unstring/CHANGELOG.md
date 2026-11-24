# Changelog

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
