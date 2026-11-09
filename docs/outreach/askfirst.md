# askfirst outreach drafts

Drafts for Sri to post, or not. Nothing here has been posted.

## MemPalace/mempalace#1981 (opt-in read-only MCP mode and guarded writes)

https://github.com/MemPalace/mempalace/issues/1981

> Both halves of this are generic enough that I put them in a small package,
> [askfirst](https://github.com/srikrishnaL/kitforge/tree/main/packages/askfirst)
> (`pip install askfirst`, no dependencies of its own, MCP Python SDK 1.x and 2.x).
> `restrict_from_env(server)` gives a server `MCP_READ_ONLY=true` / `MCP_ALLOW_TOOLS` /
> `MCP_DENY_TOOLS`: it removes the tools from the server, so they are gone from `tools/list`
> and fail if called by name, and a tool without `readOnlyHint=True` counts as a write tool.
> For guarded writes, `await ask(ctx, "Delete drawer X?", action=..., args=...)` shows an
> elicitation prompt when the client supports it, and otherwise returns a "confirm with the
> user, then call again with this token" error whose token is signed, single-use and bound to
> the exact arguments. If that fits what you had in mind, I'm happy to open a PR wiring it in.

## PrefectHQ/fastmcp#4929 (declined and cancelled elicitation results are truthy)

https://github.com/PrefectHQ/fastmcp/issues/4929

> Until there's a fix here, one way to avoid the footgun for confirmation guards:
> [askfirst](https://github.com/srikrishnaL/kitforge/tree/main/packages/askfirst)
> (`pip install askfirst`). `await ask(ctx, "Create candidate?", action="create", args=...)`
> only returns when the result is `accept` *and* the confirm box is checked; decline, cancel,
> an unchecked box and a timeout all raise a `ToolError` telling the model nothing was done.
> On connections where the prompt can't be sent (e.g. 2026-07-28 without a back-channel) it
> falls back to a signed one-time token instead of silently skipping the check.

## pulsemcp/mcp-servers#384 (elicitation fallback for clients without native support)

https://github.com/pulsemcp/mcp-servers/issues/384

> In case a reference helps: I implemented this fallback for Python servers in
> [askfirst](https://github.com/srikrishnaL/kitforge/tree/main/packages/askfirst). The parts
> that took care: the fallback token is an HMAC over the tool name and canonical arguments
> (so a token for one call can't be reused on another), it expires and works once, an
> expired/reused/mismatched token gets a fresh one with the reason, and on clients that *can*
> prompt a token never replaces the prompt. The repo is TypeScript, so this is just the
> design; a TS port is planned if there's interest.
