# flatresult

MCP tools can return more than text: `structuredContent`, `resource_link` blocks, embedded
resources and audio. Many clients only handle text and image blocks, so the result shows up
empty, gets dropped, prints as a raw struct, or fails the whole call with
`Unsupported response item type: resource_link`.

flatresult rewrites tool results into text and image blocks every client reads. It puts the
JSON of `structuredContent` in a text block when the server sent none, reads linked resources
from the server and puts their contents inline, and turns embedded resources into text (or an
image). Use it as a stdio proxy around any MCP server (no code changes, any language) or as one
function. No dependencies.

```bash
pip install flatresult
```

## Quick start: any stdio server

Put `flatresult proxy --` in front of the server command in your client's config:

```json
{
  "mcpServers": {
    "my-server": {
      "command": "flatresult",
      "args": ["proxy", "--", "npx", "-y", "@acme/mcp-server"]
    }
  }
}
```

Or with `uvx`, nothing to install first: `"command": "uvx", "args": ["flatresult", "proxy", "--", ...]`.

For a remote (HTTP) server, wrap a stdio bridge such as `mcp-remote`:

```json
{
  "mcpServers": {
    "figma": {
      "command": "uvx",
      "args": ["flatresult", "proxy", "--", "npx", "-y", "mcp-remote", "https://mcp.figma.com/mcp"]
    }
  }
}
```

Each rewrite is reported on stderr (most clients show it in their MCP log):

```text
flatresult: content[0]: resource_link -> inline contents: text block
flatresult: structuredContent: structuredContent -> text block (412 chars)
```

### Proxy options

| Option | What it does |
|---|---|
| `--keep` | Keep the original blocks and add the text after them, for clients that skip unknown blocks instead of failing on them. |
| `--no-inline` | Don't read linked resources; turn each link into a text line naming it. |
| `--max-links N` | Read at most N linked resources per result (default 5). The rest become text lines. |
| `--max-text CHARS` | Cut resource text put inline after CHARS characters (default 100,000), with a note saying how much was left out. |
| `--timeout SECONDS` | If the server has not answered `resources/read` by then, send the result with the links as text (default 10). |
| `--quiet` / `--log FILE` | Don't report rewrites, or append them to a file instead of stderr. |

Only the results of the client's own `tools/call` requests are touched. Everything else passes
through byte for byte. Linked resources are read with the server's own `resources/read`, so
the proxy needs no credentials of its own; the client never sees those requests. If the server
says it has no `resources` capability, links become text lines.

## What gets rewritten

| The server sends | The client gets |
|---|---|
| `structuredContent` with no text block | a text block with its JSON (`structuredContent` is kept), as the spec asks servers to do |
| `resource_link` | `Resource: <name> <uri> (mime type, size)` and its description, plus the resource's contents when the server can read it |
| embedded text resource (`type: "resource"`, `text`) | a text block: `<uri> (<mime type>):` and the text |
| embedded image blob | an `image` block |
| other embedded blobs, `audio` | a short note: `[binary resource file:///r.pdf: application/pdf, 52,113 bytes, not shown]` |
| `text`, `image`, unknown block types | unchanged |

## In your own client or server: one function

```python
from flatresult import flatten

result = {"content": [], "structuredContent": {"temp": 21.5, "unit": "C"}}
flat = flatten(result)
flat.result["content"]   # [{"type": "text", "text": '{"temp": 21.5, "unit": "C"}'}]
[str(c) for c in flat.changes]
# ['structuredContent: structuredContent -> text block (27 chars)']
```

`flatten(result, keep=False, read=None, max_text=100_000)` takes a `tools/call` result (a
dict) and returns `Flattened(result, changes)`. It never changes its input, and returns the same
object when nothing needs rewriting. Pass `read`, a function from a uri to the `contents` list
of a `resources/read` result (or `None`), to put linked resources inline. `links(result)` lists
the uris to read.

To see what a result would look like, there's a command too (exit code 2 when it rewrote
something):

```bash
echo '{"content": [{"type": "resource_link", "uri": "file:///r.txt", "name": "report"}]}' \
  | flatresult check
```

## Limits

- It fixes what the client is given, not how the client shows it. A client that already reads
  `structuredContent` or resource links gets the same data twice with `--keep`, and as text
  instead of its own UI without it. Use it for clients that show nothing.
- Links are read only from the server behind the proxy. An `https://` link the server can't
  read stays a text line; the proxy never fetches URLs itself.
- Binary resources other than images are not decoded (a PDF stays a note with its size).
- The proxy speaks stdio. For a remote server, put a stdio bridge such as `mcp-remote` behind it.

## Why this exists

The spec says a tool that returns `structuredContent` SHOULD also return the JSON as text, and
that clients should handle every content type. In practice both sides skip it, and users of
one client lose the data another client shows:

- [microsoft/work-iq#240](https://github.com/microsoft/work-iq/issues/240): the hosted Work IQ
  server returns `fetch` results only in `structuredContent`; Cursor shows them blank.
- [kirodotdev/Kiro#9439](https://github.com/kirodotdev/Kiro/issues/9439): every Figma MCP tool
  failed with `Unsupported response item type: resource_link` (4 upvotes).
- [langgenius/dify#43451](https://github.com/langgenius/dify/issues/43451): resource_link-only
  tool results are discarded.
- [anthropics/claude-ai-mcp#1086](https://github.com/anthropics/claude-ai-mcp/issues/1086) and
  [anthropics/claude-code#93946](https://github.com/anthropics/claude-code/issues/93946): blobs in
  embedded-resource results are dropped or rejected.
- [charmbracelet/crush#3846](https://github.com/charmbracelet/crush/issues/3846): embedded
  resources render as a raw Go struct pointer.
- [microsoft/copilot-sdk#2816](https://github.com/microsoft/copilot-sdk/issues/2816),
  [agentscope-ai/agentscope#2798](https://github.com/agentscope-ai/agentscope/issues/2798),
  [tobi/qmd#959](https://github.com/tobi/qmd/issues/959): more clients that drop or garble
  resource blocks.

Some clients have closed their reports one by one (LibreChat#15198, goose#13182, Cherry
Studio#23290), each on its own schedule. flatresult works in front of any client today, with
servers you don't control.

## License

MIT
