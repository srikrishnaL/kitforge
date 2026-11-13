# skillhop outreach drafts

Drafts for Sri to post, or not. Nothing here has been posted. All three issues were open on
2026-11-13; check again before posting, and skip any that the host has since fixed.

## anthropics/claude-ai-mcp#1160 (Claude chat, Desktop and Cowork can't load skills served over MCP)

https://github.com/anthropics/claude-ai-mcp/issues/1160

> Until Claude chat supports the extension, a stopgap that needs no server changes: I wrote
> [skillhop](https://github.com/srikrishnaL/kitforge/tree/main/packages/skillhop)
> (`pip install skillhop`, MIT), which reads a server's skills over SEP-2640, verifies every
> file against the published size and SHA-256 digest (and SKILL.md against the entry's
> frontmatter), and writes a zip per skill that can be uploaded in Claude's Skills settings:
> `skillhop add https://your-gateway/mcp --all --zip ./skills -H "Authorization: Bearer $TOKEN"`.
> `skillhop check` later tells you which skills the gateway changed. It is a copy, not live
> loading, so it doesn't replace what this issue asks for.

## openai/codex#48000 (support SEP-2640: Skills Extension)

https://github.com/openai/codex/issues/48000

> For anyone who wants served skills in Codex today: [skillhop](https://github.com/srikrishnaL/kitforge/tree/main/packages/skillhop)
> installs them into `.agents/skills` (or `~/.agents/skills` with `--global`):
> `uvx skillhop add <server-url-or-config-name> <skill> -a codex`. Every file is checked
> against the server's digests before it is written, `allowed-tools`/`hooks` are dropped,
> and `skillhop check` exits 1 when the server changes a skill, so it can run in CI.

## anthropics/claude-code#106550 (add support for skills over MCP)

https://github.com/anthropics/claude-code/issues/106550

> A workaround while this is open: [skillhop](https://github.com/srikrishnaL/kitforge/tree/main/packages/skillhop)
> (`pip install skillhop`) copies the skills an MCP server serves into `.claude/skills`,
> verified against the server's SHA-256 digests: `skillhop add <server> <skill>` (the server
> can be a name from your `.mcp.json`). `skillhop check` / `skillhop update` keep the copies in
> sync with the server.
