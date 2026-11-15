# briefed

MCP servers can send `instructions` in their `initialize` result: how their tools fit together,
which one to call first, what the fields mean. Many clients drop that text (claude.ai custom
connectors, Kiro, Google ADK, agno and others) or cut it short, so the model never reads it.

briefed puts the instructions where every client's model looks: the tool list. Use it as a
stdio proxy around any server, as ASGI middleware for a remote server, or as one function.

```bash
pip install briefed
```

## Quick start: any stdio server, no code changes

Put `briefed proxy --` in front of the server command in your client's config:

```json
{
  "mcpServers": {
    "catalog": {
      "command": "briefed",
      "args": ["proxy", "--", "npx", "-y", "@acme/catalog-mcp"]
    }
  }
}
```

The model now sees one more tool, `server_instructions`, that returns the server's
instructions, and each tool description ends with
`(Call server_instructions once before using this server's tools.)`. The proxy answers that
tool itself; everything else passes through unchanged.

Check what a server sends first:

```console
$ briefed show -- npx -y @acme/catalog-mcp
catalog sends 412 characters of instructions:
...
```

`briefed show` exits 1 when the server sends no instructions (handy in CI).

### Proxy options

| Option | What it does |
|---|---|
| `--mode tool` | Default. Adds the `server_instructions` tool plus a one-line hint per tool. Long instructions stay whole. |
| `--mode inline` | Puts the instructions at the top of the first tool's description (or `--target TOOL`). No extra call, but clients that cut long descriptions cut them too. |
| `--instructions TEXT` or `@FILE` | Use your own instructions instead of the server's (for servers that send none, or send wrong ones). |
| `--add TEXT` or `@FILE` | Add your own text after the server's instructions (team rules, workspace notes). |
| `--tool-name NAME` | Name of the added tool (default `server_instructions`). |
| `--no-hint` | Don't add the hint line to each description. |
| `--move` | Also remove `instructions` from `initialize`, so clients that do read them don't get them twice. |

## Remote servers (claude.ai custom connectors): ASGI middleware

claude.ai reaches your server over Streamable HTTP, so a local proxy can't help. Wrap the
server's ASGI app instead. With the MCP Python SDK:

```python
from mcp.server.fastmcp import FastMCP   # SDK 2.x: from mcp.server.mcpserver import MCPServer
from briefed.asgi import Briefed

INSTRUCTIONS = "Call search before get. All dates are UTC. Use has_price to filter tradeable bonds."
mcp = FastMCP("catalog", instructions=INSTRUCTIONS)

@mcp.tool()
def search(q: str) -> list:
    """Search the catalog by keyword."""
    ...

app = Briefed(mcp.streamable_http_app(), INSTRUCTIONS)   # run with: uvicorn server:app
```

The middleware works with any ASGI framework (Starlette, FastAPI, the SDK's app). It reads
each POST body to see which JSON-RPC requests it holds, answers `server_instructions` calls
itself, and rewrites `initialize` and `tools/list` responses in both `application/json` and
SSE (`text/event-stream`) form. GET streams, DELETE, other status codes and compressed bodies
pass through untouched. Options are the same as the proxy's: `mode`, `tool_name`, `target`,
`hint`, `extra`, `keep=False` (the proxy's `--move`) and `path="/mcp"` to limit it to one route.

Pass the instructions explicitly (as above) for stateless or multi-process servers, where a
`tools/list` request can reach a process that never saw an `initialize`. Without them, the
middleware uses the text from the last `initialize` result it saw.

## In your own code

```python
from briefed import Brief

brief = Brief("Call search before get. All dates are UTC.")
tools = brief.tools(tools)                 # MCP tool dicts -> new list, input not changed

if brief.handles(name):                    # a tools/call for server_instructions
    return brief.call_result()
```

`fold(tools, instructions, **options)` is the same as `Brief(instructions, **options).tools(tools)`.
`Brief(...).initialize_result(result, keep=True)` learns the server's instructions from an
`initialize` result (and with `keep=False` returns it without them).

## Limits

- The model has to call `server_instructions` before it reads them. The hint line on every
  tool makes that likely, not certain. Inline mode avoids the call but is cut by clients that
  cut descriptions (about 1,024 characters is common; `briefed show` warns above that).
- Only the first page of a paged `tools/list` gets the extra tool.
- If the server already has a tool with the same name, briefed adds nothing in tool mode;
  pick another `--tool-name`.
- The ASGI middleware does not answer a `server_instructions` call inside a JSON-RPC batch
  (batches were removed from MCP in 2025-06-18); the server gets it and answers "unknown tool".
- Instructions are learned from `initialize` (and from `server/discover` results that carry an
  `instructions` field). Changes the server makes later reach the model on the next listing.

## Why this exists

Server authors keep finding that the model ignores guidance they put in `instructions`:

- [claude-ai-mcp#93](https://github.com/anthropics/claude-ai-mcp/issues/93): claude.ai does not
  pass server instructions from the initialize response to the model (31 upvotes). The author
  works around it by copying them into tool descriptions, "which are themselves truncated".
- [kirodotdev/Kiro#11096](https://github.com/kirodotdev/Kiro/issues/11096): support the MCP
  server `instructions` field in agent context.
- [google/adk-python#7696](https://github.com/google/adk-python/issues/7696): McpToolset,
  opt-in support for MCP server instructions.
- [agno-agi/agno#7049](https://github.com/agno-agi/agno/issues/7049): MCPTools should load
  MCP server instructions during initialization.
- [anthropics/claude-code#43474](https://github.com/anthropics/claude-code/issues/43474):
  server instructions silently truncated when several servers are configured.
- [colbymchenry/codegraph#765](https://github.com/colbymchenry/codegraph/issues/765): users
  want to override a server's built-in instructions (`--instructions` / `--add`).

Part of [kitforge](https://github.com/srikrishnaL/kitforge). Related:
[toolcap](https://github.com/srikrishnaL/kitforge/tree/main/packages/toolcap) (results over the
token limit), [schemashim](https://github.com/srikrishnaL/kitforge/tree/main/packages/schemashim)
(schemas Gemini or OpenAI strict reject), [cleanpipe](https://github.com/srikrishnaL/kitforge/tree/main/packages/cleanpipe)
(servers that print to stdout).

## License

MIT
