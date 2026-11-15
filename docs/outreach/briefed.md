# briefed outreach drafts

Drafts for Sri to post, or not. Nothing here has been posted. All three issues were open on
2026-11-15; check again before posting, and don't post where the client already shipped
support for `instructions`. Each offers a workaround until the client reads the field, not a
replacement for fixing it.

## anthropics/claude-ai-mcp#93 (claude.ai drops server instructions, 31 upvotes)

https://github.com/anthropics/claude-ai-mcp/issues/93

> A workaround for server authors until this lands: I wrote
> [briefed](https://github.com/srikrishnaL/kitforge/tree/main/packages/briefed) (MIT, no
> dependencies), ASGI middleware that moves your `instructions` into the tool list, the one
> thing every client shows the model. It adds a read-only `server_instructions` tool that
> returns the full text (tool results aren't cut the way descriptions are) and a one-line
> "call it first" hint on each tool:
>
> ```python
> from briefed.asgi import Briefed
> app = Briefed(mcp.streamable_http_app(), INSTRUCTIONS)
> ```
>
> `pip install briefed`. It rewrites both JSON and SSE responses and leaves everything else
> alone. Clients that already read `instructions` still get them.

## kirodotdev/Kiro#11096 (support the instructions field)

https://github.com/kirodotdev/Kiro/issues/11096

> Until Kiro reads `instructions`, users can wrap a stdio server so its instructions reach the
> agent through the tool list:
> `"command": "uvx", "args": ["briefed", "proxy", "--", "<server command>"]`
> ([briefed](https://github.com/srikrishnaL/kitforge/tree/main/packages/briefed), MIT). The
> proxy adds a `server_instructions` tool that returns them, and answers it itself.
> `briefed show -- <server command>` prints what a server sends, to check there is anything to
> miss. (I wrote it after seeing this gap in several clients.)

## google/adk-python#7696 (McpToolset: opt-in support for server instructions)

https://github.com/google/adk-python/issues/7696

> For anyone who needs this before an opt-in ships: running the server behind
> `briefed proxy -- <server command>`
> ([briefed](https://github.com/srikrishnaL/kitforge/tree/main/packages/briefed),
> `pip install briefed`) makes the instructions show up as a `server_instructions` tool, so
> `McpToolset` passes them to the model with no ADK changes. `--mode inline` puts them at the
> top of the first tool's description instead, if you'd rather avoid the extra call.
