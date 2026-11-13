# Changelog

## 0.2.0 (2026-11-13)

- `toolcap proxy [--max-tokens N] [--trim] -- <server command>`: runs any stdio MCP server
  behind toolcap, so its oversized results are paged without changing the server. Adds an
  optional `cursor` parameter to each tool, pages text and JSON results that are over the
  budget, keeps the last 16 full results in memory so later pages don't re-run the tool, and
  trims the results of tools that already have their own `cursor` parameter.
- New `toolcap` command (also `python -m toolcap`).

## 0.1.0 (2026-11-11)

First release.

- `Cap(max_tokens).page(data, cursor)`: returns any tool result as text that fits a token
  budget. Lists (or the largest list inside a dict, or the one at `key=`) are split into pages
  of whole items with a `page` object (`items`, `total`, `next_cursor`, `note`); text is split
  at line breaks with a footer; one item bigger than the budget is cut and paging moves on;
  values without lists keep every key and get long strings and lists cut.
- Stateless cursors (offset + fingerprint of the full result). Results that changed between
  pages are flagged in the note; cursors toolcap did not issue raise `CursorError`, an MCP SDK
  `ToolError` whose text reaches the model.
- `Cap.trim(data)`: the same fitting without cursors, with a note on what was left out.
- `@cap.paginate` decorator for sync and async tools: adds a `cursor` parameter (resolving
  string annotations, keeping `Context` parameters), mentions it in the description, and
  declares no output schema so pages are not duplicated as `structuredContent`.
- `TOOLCAP_MAX_TOKENS` environment variable, custom token counters, JSON indent setting.
- Tested with the MCP Python SDK 1.10, 1.x and 2.x.
