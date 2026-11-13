# Changelog

## 0.1.0 (2026-11-13)

First release.

- `skillhop ls / show / add / status / check / update / remove / servers`.
- Talks to MCP servers over stdio and Streamable HTTP, on the 2026-07-28 protocol
  (`server/discover`, per-request `_meta`, `MCP-Protocol-Version` / `Mcp-Method` / `Mcp-Name`
  headers) or the older `initialize` handshake, chosen automatically.
- Implements the client side of the Skills extension (SEP-2640): `skills/list` with paging,
  `skills/get` for unlisted skills, `resources/read` (text and blob),
  `resources/directory/read` for dynamic skills.
- Verifies size, SHA-256 digest and frontmatter of every file before writing; refuses unsafe
  paths, case collisions, over-limit skills and name shadowing.
- Installs into Claude Code, Codex, Cursor, Gemini CLI, GitHub Copilot, goose and OpenCode
  folders, or a zip for claude.ai / Claude Desktop; removes `allowed-tools` and `hooks` and
  tags the description with the server by default.
- `.skillhop.json` lock file without secrets; `check` exits 1 when a server changed a skill.
- Reads server definitions by name from Claude Code, Cursor, VS Code, Gemini CLI, Windsurf,
  Codex and Claude Desktop configs.
