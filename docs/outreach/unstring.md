# unstring outreach drafts

Drafts for Sri to post, or not. Nothing here has been posted. The three issues below were open
on 2026-11-24 and still open on 2026-11-26. The n8n#33864 and exa#477 drafts use `unstring http`
(new in 0.2.0), so post those two only after 0.2.0 is on PyPI. Check again before posting, and
skip any where the client or server already shipped a fix. Each offers a workaround until the root cause (the client stringifying
arguments) is fixed, not a replacement for fixing it.

## makenotion/notion-mcp-server#215 (`pages` array received as a string, 5 upvotes)

https://github.com/makenotion/notion-mcp-server/issues/215

> Until the client stops stringifying arguments, a workaround that needs no change to this
> server: I wrote [unstring](https://github.com/srikrishnaL/kitforge/tree/main/packages/unstring)
> (MIT, no dependencies), a stdio proxy that decodes JSON-string arguments where the tool's own
> `inputSchema` wants an array or object, and leaves real strings alone:
>
> ```json
> {
>   "mcpServers": {
>     "notion": {
>       "command": "uvx",
>       "args": ["unstring", "proxy", "--", "npx", "-y", "@notionhq/notion-mcp-server"],
>       "env": {"NOTION_TOKEN": "..."}
>     }
>   }
> }
> ```
>
> Each repair is logged on stderr (`unstring: notion-create-pages: pages: string -> array`), so
> it also shows how often the client does this.

## n8n-io/n8n#33864 (typed tool parameters arrive as strings, 11 comments)

https://github.com/n8n-io/n8n/issues/33864

> For anyone blocked by this while it's investigated: the error comes from arguments arriving as
> JSON text (`"[\"a\"]"`, `"5"`) where the schema says array or number. If your MCP client runs
> on your machine (Claude Code, Cursor, VS Code), [unstring](https://github.com/srikrishnaL/kitforge/tree/main/packages/unstring)
> can sit between it and the instance's MCP endpoint and decode those values using each tool's
> `inputSchema` before n8n validates them:
>
> ```
> pip install "unstring[http]"
> unstring http https://<your-n8n>/mcp-server/http --port 8080
> ```
>
> Then point the client at `http://127.0.0.1:8080/mcp-server/http` with the same
> `Authorization` header. It only touches values where the schema does not allow a string, and
> logs each repair, so it also shows how often the client does this. (A claude.ai web
> connector can't reach localhost, so this doesn't help there.)

## exa-labs/exa-mcp-server#477 (`includeDomains`/`excludeDomains` sent as a string)

https://github.com/exa-labs/exa-mcp-server/issues/477

> A client-agnostic workaround while this is open: run the server behind
> [unstring](https://github.com/srikrishnaL/kitforge/tree/main/packages/unstring), which decodes
> stringified array and object arguments using the tool's schema. For the local server:
> `uvx unstring proxy -- npx -y exa-mcp-server`. For the hosted one, run
> `uvx --from "unstring[http]" unstring http https://mcp.exa.ai/mcp --port 8080` and point the
> client at `http://127.0.0.1:8080/mcp`. If the client sends a bare `"example.com"` instead of
> a JSON list, add `--wrap` to turn it into a one-item list.
