# Changelog

## 0.1.0 (2026-11-27)

First release.

- `flatten(result, keep=False, read=None, max_text=100_000)` rewrites a `tools/call` result for
  clients that only handle text and image blocks: `structuredContent` without a text block gets
  one with its JSON; `resource_link` becomes a text line naming it, plus the resource's contents
  when `read` can fetch them; embedded text resources become text; image blobs become `image`
  blocks; other blobs and `audio` become a short note. `keep=True` keeps the original blocks.
  Returns the same object when nothing needs rewriting. `links(result)` lists the link uris.
- `flatresult proxy [--keep] [--no-inline] [--max-links N] [--max-text CHARS]
  [--timeout SECONDS] [--quiet] [--log FILE] -- <server command>`: stdio proxy for any MCP
  server. Rewrites only the results of the client's `tools/call` requests, reads linked
  resources with the server's own `resources/read` (copying the 2026-07-28 request envelope
  from the client's call), and holds the result until they arrive or the timeout passes.
- `flatresult check [FILE]`: rewrite one result or JSON-RPC response from the command line;
  exit code 2 when something was rewritten.
