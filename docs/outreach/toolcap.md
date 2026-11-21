# toolcap outreach drafts

Drafts for Sri to post, or not. Nothing here has been posted. The first three issues were
still open on 2026-11-21 (harness-sdk#5862 and binary_ninja_mcp#16 with no comments yet,
volcengine#397 with one); check again before posting. Since 0.2.0, people who only *use* one of
these servers can fix it today with `toolcap proxy`, so each draft now mentions that too.

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
> 20,000 tokens, under Claude Code's 25,000 limit. Until the server pages itself, users can
> wrap it: `claude mcp add binja -- uvx toolcap proxy --tool fetch_disassembly=15000 -- <the
> current command>`.

## volcengine/mcp-server#397 (askecho web_search result exceeds maximum allowed tokens)

https://github.com/volcengine/mcp-server/issues/397

> One way to keep `web_search` under Claude Code's limit without dropping results:
> [toolcap](https://github.com/srikrishnaL/kitforge/tree/main/packages/toolcap)
> (`pip install toolcap`, no dependencies) pages the largest list in the response
> (or the one you name with `key="Result.<field>"`) and keeps the other fields,
> adding a `next_cursor` the model can pass back. `@cap.paginate` under `@mcp.tool()` does it
> without changing the tool body. Without changing the server at all:
> `uvx toolcap proxy --tool web_search=15000 -- <the current command>` in the MCP config.

## MysterionRise/pubchem-finder#201 (results larger than the declared 100,000-character limit)

https://github.com/MysterionRise/pubchem-finder/issues/201 (Python MCP server, open with
0 comments on 2026-11-21)

> For the C6 part (substructure/similarity results far above `maxResultSizeChars`, and Claude
> Code showing the model only "exceeds maximum allowed tokens"): I maintain a small helper,
> [toolcap](https://github.com/srikrishnaL/kitforge/tree/main/packages/toolcap)
> (`pip install toolcap`, no dependencies, MIT). `@cap.paginate` under `@mcp.tool()` returns the
> hits in pages of whole records that fit a budget, with a `next_cursor` the model can pass
> back, and declares no output schema so the page isn't sent a second time as
> structuredContent (that copy is about half of the 826 KB here, and part of what pushes the
> response past the 1 MiB SSE limit over HTTP). `Cap(max_tokens=100_000, count=len)` counts
> characters instead of tokens, so pages match the declared 100,000-character limit exactly. For users today, `toolcap proxy -- <command>` does the same
> from outside the server over stdio.
