# Changelog

## 0.2.1 (2026-11-13)

- PyPI description and keywords say what the package does (pytest plugin, linter, tool
  poisoning checks, GitHub Action), so it shows up in searches for MCP server testing.
- README links the kitforge proxies for fixing servers you don't own.

## 0.2.0 (2026-11-07)

- GitHub Actions output: `mcptest lint --format github` and `mcptest diff --format github`
  print each finding as an annotation on the run and the pull request, and add a table to the
  job summary. Text from the server is escaped, so a tool description cannot inject workflow
  commands.
- A reusable GitHub Action (`uses: srikrishnaL/kitforge/packages/mcptest@<ref>`) that lints a
  server and, given a committed snapshot, fails on breaking tool changes.

## 0.1.0 (2026-11-06)

First release. Published on PyPI as `pytest-mcp-server` (the name `mcptest` was refused as too
similar to `mcp-test`); the command and import are `mcptest`.

- Dependency-free MCP client for stdio and Streamable HTTP: handshake, paginated tool,
  resource and prompt listing, tool calls, server ping replies, and clear errors that include
  the server's stderr and any non-protocol output on stdout.
- Linter with 15 rules for tool names, descriptions and schemas, including tool-poisoning
  phrases and hidden characters.
- Snapshot format and diff that classifies each change as breaking or compatible.
- pytest plugin: `mcp`, `mcp_server` and `mcp_snapshot` fixtures, `mcp_server` marker,
  `mcp_command` / `mcp_url` settings, `--mcp-update-snapshots`.
- CLI: `mcptest lint`, `tools`, `call`, `snapshot`, `diff`.
