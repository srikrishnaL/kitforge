# flatresult outreach drafts

Drafts for Sri to post, or not. Nothing here has been posted. Both issues were open on
2026-11-27; check again before posting, and skip any where the client or server already shipped
a fix. Post only after flatresult 0.1.0 is on PyPI. Each offers a workaround until the client
or server is fixed, not a replacement for fixing it.

## microsoft/work-iq#240 (`fetch` results only in `structuredContent`, blank in Cursor)

https://github.com/microsoft/work-iq/issues/240

> Until the server also sends the JSON as a text block, a client-side workaround: I wrote
> [flatresult](https://github.com/srikrishnaL/kitforge/tree/main/packages/flatresult) (MIT, no
> dependencies), a stdio proxy that adds a text block with the `structuredContent` JSON when a
> tool result has none, and leaves everything else alone. For a remote server, put it in front
> of `mcp-remote` in Cursor's `mcp.json`:
>
> ```json
> {
>   "mcpServers": {
>     "workiq": {
>       "command": "uvx",
>       "args": ["flatresult", "proxy", "--", "npx", "-y", "mcp-remote", "https://workiq.svc.cloud.microsoft/mcp"]
>     }
>   }
> }
> ```
>
> Each rewrite is logged on stderr (`flatresult: structuredContent: structuredContent -> text
> block (...)`), so it also shows which tools are affected.

## charmbracelet/crush#3846 (embedded resources render as `&{0x... map[] <nil>}`)

https://github.com/charmbracelet/crush/issues/3846

Also relevant to tobi/qmd#959 (the server side of the same report); one comment here is enough.

> A workaround until the content switch handles `*mcp.EmbeddedResource`: wrap the server in
> [flatresult](https://github.com/srikrishnaL/kitforge/tree/main/packages/flatresult), a stdio
> proxy that turns embedded text resources into plain text blocks (`<uri> (<mime type>):` and
> the text), image blobs into image blocks, and `resource_link`s into text with the linked
> resource's contents read from the server. For qmd in `crush.json`:
>
> ```json
> {
>   "mcp": {
>     "qmd": {
>       "type": "stdio",
>       "command": "uvx",
>       "args": ["flatresult", "proxy", "--", "qmd", "mcp"]
>     }
>   }
> }
> ```
>
> MIT, no dependencies; only `tools/call` results are rewritten.

Before posting: check qmd's real MCP command (`qmd mcp` is a guess from the issue) and Crush's
current config keys.
