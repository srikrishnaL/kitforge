# Changelog

## 0.1.0 (2026-11-15)

First release.

- `Brief(instructions)` folds MCP server instructions into a tool list: `mode="tool"` adds a
  read-only `server_instructions` tool (plus a one-line hint in each description) that returns
  them whole; `mode="inline"` puts them at the top of one tool's description. Options for the
  tool name, target tool, hint, replacing or adding to the server's text.
- `briefed proxy [--mode] [--instructions|--add TEXT|@FILE] [--move] -- <server command>`:
  stdio proxy for any MCP server. Learns the instructions from `initialize`, adds them to the
  first page of `tools/list`, answers the instructions tool itself.
- `briefed show -- <server command>`: prints the instructions a server sends and their size;
  exits 1 when there are none.
- `briefed.asgi.Briefed(app)`: the same for Streamable HTTP servers (claude.ai custom
  connectors), rewriting JSON and SSE responses; no dependencies.
- Tested with the MCP Python SDK 1.x and 2.x.
