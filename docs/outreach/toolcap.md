# toolcap outreach drafts

Drafts for Sri to post, or not. Nothing here has been posted. All three issues were open on
2026-11-11 and the projects are Python MCP servers; check again before posting.

## strands-agents/harness-sdk#5862 (enforce a response size cap with truncation and continuation)

https://github.com/strands-agents/harness-sdk/issues/5862

> The cap-plus-continuation behaviour proposed here is what I packaged as
> [toolcap](https://github.com/srikrishnaL/kitforge/tree/main/packages/toolcap)
> (`pip install toolcap`, no dependencies, MIT). `Cap(max_tokens=...).page(result, cursor)`
> returns the result unchanged when it fits, otherwise the first part plus a `next_cursor` and a
> note telling the model how to continue; text is cut at line breaks, and the budget can be
> set per deployment with `TOOLCAP_MAX_TOKENS`. Cursors are stateless (offset + fingerprint),
> so nothing is stored between calls. Happy to adjust it if strands-mcp needs a different
> continuation format.

## fosdickio/binary_ninja_mcp#16 (pagination for disassembly longer than 25,000 tokens)

https://github.com/fosdickio/binary_ninja_mcp/issues/16

> In case it's useful for `fetch_disassembly` / `decompile_function`: I wrote a small helper,
> [toolcap](https://github.com/srikrishnaL/kitforge/tree/main/packages/toolcap)
> (`pip install toolcap`, no dependencies). Returning `cap.page(text, cursor)` from the tool
> (with a `cursor: str = ""` parameter) sends at most N tokens, cut at a line break, plus
> "call again with cursor=..." so Claude can fetch the rest in parts. Default budget is
> 20,000 tokens, under Claude Code's 25,000 limit.

## volcengine/mcp-server#397 (askecho web_search result exceeds maximum allowed tokens)

https://github.com/volcengine/mcp-server/issues/397

> One way to keep `web_search` under Claude Code's limit without dropping results:
> [toolcap](https://github.com/srikrishnaL/kitforge/tree/main/packages/toolcap)
> (`pip install toolcap`, no dependencies) pages the largest list in the response
> (or the one you name with `key="Result.<field>"`) and keeps the other fields,
> adding a `next_cursor` the model can pass back. `@cap.paginate` under `@mcp.tool()` does it
> without changing the tool body.
