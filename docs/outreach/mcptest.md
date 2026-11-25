# mcptest (pytest-mcp-server) outreach drafts

Drafts for Sri to post, or not. Nothing here has been posted. Each issue asks a server to add
MCP tool annotations; all four were open with 0 comments on 2026-11-23. Post only after
pytest-mcp-server 0.3.0 (the annotation rules) is on PyPI, and check each issue again first.
Each comment is useful on its own: it says how to keep the hints from going missing again.

## penpot/penpot#13142 (MCP server: publish tool annotations)

https://github.com/penpot/penpot/issues/13142

> Once the hints are in, one way to keep them from drifting as tools are added: I maintain
> [pytest-mcp-server](https://github.com/srikrishnaL/kitforge/tree/main/packages/mcptest), a
> dependency-free linter for MCP servers in any language. From 0.3.0,
> `mcptest lint --fail-on info -- <server command>` lists every tool
> without `readOnlyHint`/`destructiveHint` (MCP016) and fails on `readOnlyHint: true` for a tool
> named like a write, such as `execute_code` (MCP019). There is also a GitHub Action that puts
> each finding on the PR.

## hyperbrowserai/mcp#24 (tools register without annotations)

https://github.com/hyperbrowserai/mcp/issues/24

> For a CI check after the fix: `pipx run --spec pytest-mcp-server mcptest lint --fail-on info
> -- <server command>` (pytest-mcp-server 0.3.0, no dependencies) reports any tool that still
> has no `readOnlyHint`/`destructiveHint`, and errors on hints sent as strings instead of
> booleans. It would also catch `delete_profile` if it were ever marked read-only.

## aws-samples/sample-spec-driven-presentation-maker#670 (add annotations to every tool)

https://github.com/aws-samples/sample-spec-driven-presentation-maker/issues/670

> If it helps verify "every tool", the linter I maintain checks exactly this:
> `mcptest lint --fail-on info -- <server command>` from
> [pytest-mcp-server](https://github.com/srikrishnaL/kitforge/tree/main/packages/mcptest) 0.3.0
> lists unannotated tools, contradictory hints (read-only and destructive) and read-only claims
> on write-named tools. In a pytest suite it is
> `assert_lint_clean(mcp, fail_on="info")`.

## ergut/mcp-logseq#107 (declare readOnlyHint on the read-only tools)

https://github.com/ergut/mcp-logseq/issues/107

> A small way to keep this working for `--read-only` users after the change: a test with
> [pytest-mcp-server](https://github.com/srikrishnaL/kitforge/tree/main/packages/mcptest)
> (`pip install pytest-mcp-server`), `mcp_command = "mcp-logseq --read-only"` in pytest config,
> and `assert_lint_clean(mcp, fail_on="info")` fails if a tool loses its `readOnlyHint`
> (MCP016), and `mcptest lint` warns if a tool named like a write (`delete_*`, `update_*`)
> claims to be read-only (MCP019).
