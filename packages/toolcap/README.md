# toolcap

Keep MCP tool results under the client's token limit, with pages the model can follow.

A tool that returns 200 flow runs, a long log or a big page snapshot can blow past the client's
limit (Claude Code rejects results over 25,000 tokens) or fill the context in one call. toolcap
cuts any result into pages of whole items that fit a token budget, each with a `next_cursor`
and a note telling the model how to get the next page, in one call or one decorator.

```bash
pip install toolcap
```

Python 3.9+, no dependencies. Works with any MCP framework (the official MCP Python SDK 1.x
`FastMCP` and 2.x `MCPServer`, the standalone `fastmcp`) or none, because it only turns a value
into text.

## Example

```python
from mcp.server.fastmcp import FastMCP     # SDK 2.x: from mcp.server.mcpserver import MCPServer
from toolcap import Cap

mcp = FastMCP("prefect")
cap = Cap(max_tokens=20_000)

@mcp.tool()
@cap.paginate                      # adds a `cursor: str = ""` parameter for the model
def get_flow_runs(state: str = "COMPLETED", limit: int = 200) -> list:
    """List flow runs."""
    return api.flow_runs(state=state, limit=limit)    # any JSON-able value
```

If the 200 runs fit in 20,000 tokens the model gets them unchanged. If not, it gets as many
whole runs as fit, plus a `page` object:

```json
{"items": [{"id": "...", "name": "..."}, ...],
 "page": {"items": "1-37 of 200", "total": 200, "next_cursor": "MTozNzo1YzBmYTE5Mw",
          "note": "Showing items 1-37 of 200. Call this tool again with the same arguments and cursor=\"MTozNzo1YzBmYTE5Mw\" for the next page."}}
```

The model calls `get_flow_runs(state="COMPLETED", limit=200, cursor="MTozNzo1YzBmYTE5Mw")` and
gets items 38 onward. The decorator also adds one sentence about `next_cursor` to the tool's
description. It works on sync and async tools, keeps `Context` parameters hidden from the model
as before, and resolves string annotations (`from __future__ import annotations`).

Without the decorator, call it yourself. This is the way to go when you want the cursor in your
own signature, or to page only some of the result:

```python
@mcp.tool(structured_output=False)
def read_log(run_id: str, cursor: str = "") -> str:
    """Read a flow run's log."""
    return cap.page(api.log_text(run_id), cursor)
```

## What it does with each kind of result

| Result | What the model gets when it is too big |
| --- | --- |
| A list | A page of whole items, wrapped as `{"items": [...], "page": {...}}` |
| A dict with a list inside (`{"count": 500, "results": [...]}`) | The same dict with the largest list paged, the other fields kept, and a `page` key next to it (`_page` if the dict already has a `page`). Pick the list with `key="data.items"` |
| Text | A chunk cut at a line break (or a space), then `[Output cut: showing characters 1-5850 of 65000. Call this tool again ... cursor="..."]` |
| One item bigger than the whole budget | That item with its long strings (then long lists) cut, marked `... [N more characters cut]`, and paging moves on to the next item |
| Anything else (a dict without lists, a huge string field) | Every key kept, long strings and lists cut until it fits |

`cap.trim(data)` does the same without cursors, for tools where paging makes no sense: the
model gets the first part and a note saying how many items were left out and to narrow the
request. Pydantic models, dataclasses, tuples and sets are converted to JSON first.

## Settings

```python
Cap(
    max_tokens=None,        # default: the TOOLCAP_MAX_TOKENS env var, else 20,000
    count=None,             # text -> token count; default is a high estimate (UTF-8 bytes / 3)
    indent=None,            # JSON indent; None (compact) uses the fewest tokens
    cursor_param="cursor",  # name of the parameter the decorator adds
)
```

For an exact fit, pass a real tokenizer: `Cap(count=lambda s: len(enc.encode(s)))`. The default
estimate counts one token per 3 bytes of UTF-8, which is above what real tokenizers give for
English, JSON, code and CJK text, so pages come out a little smaller than they could be but
never over the limit. Users of your server can change the budget with `TOOLCAP_MAX_TOKENS`
without code changes (match it to `MAX_MCP_OUTPUT_TOKENS` if they raised that in Claude Code).

Module-level shortcuts with default settings: `toolcap.page(data, cursor)`,
`toolcap.trim(data)` and `@toolcap.paginate`.

## How paging works, and its limits

- Cursors are stateless: an offset plus a short fingerprint of the full result. The server runs
  the tool again on every page and slices it, so nothing is stored between calls and it works
  across processes and restarts. The cost is that each page re-runs the query; for an expensive
  backend, cache the result yourself or pass the cursor's offset on to the backend.
- If the result changed between pages (new rows came in), the page's note says so and that
  items may be repeated or missed. Give results a stable sort order.
- A cursor that toolcap did not issue is rejected with an error the model can act on ("call the
  tool without a cursor to start from the beginning"). It is the SDK's own `ToolError`, so the
  text reaches the model on SDK 2.x too.
- The decorated tool returns text and declares no output schema, so the SDKs don't also copy
  the page into `structuredContent` (which would double its size, and some clients skip their
  truncation for structured content). Use `structured_output=False` on hand-written `-> str`
  tools for the same reason.
- The budget covers one result. It does not know how full the model's context already is.
- Lists are paged at one level (the largest list reached through dict keys). Lists inside list
  items are cut, not paged.

## Why this exists

MCP servers keep running into the client's result-size limit, and each one fixes it by hand
with ad-hoc `limit` parameters or a second "minimal" tool:

- [strands-agents/harness-sdk#5862](https://github.com/strands-agents/harness-sdk/issues/5862):
  "enforce a response size cap with truncation and continuation"
- [microsoft/playwright-mcp#395](https://github.com/microsoft/playwright-mcp/issues/395) (14 👍):
  "the snapshot length is too long, add pagination to it"
- [OpsLevel/opslevel-mcp#47](https://github.com/OpsLevel/opslevel-mcp/issues/47) (13 👍):
  filters to avoid response size errors
- [PrefectHQ/prefect-mcp-server#125](https://github.com/PrefectHQ/prefect-mcp-server/issues/125):
  `get_flow_runs` returning ~261k tokens
- [eyaltoledano/claude-task-master#1137](https://github.com/eyaltoledano/claude-task-master/issues/1137):
  pagination for `get_tasks`
- [mui/material-ui#46778](https://github.com/mui/material-ui/issues/46778): MCP tool "returning
  too many tokens for Claude Code"

## License

MIT
