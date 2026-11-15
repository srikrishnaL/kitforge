"""A minimal stdio MCP server for the proxy tests.

It sends instructions (unless --none), pages its tool list in two pages, and answers
``count`` with the number of tools/call requests it has seen.
"""

import json
import sys

INSTRUCTIONS = "Call search before get. All dates are UTC."
PAGES = [
    [{"name": "search", "description": "Search the catalog by keyword",
      "inputSchema": {"type": "object", "properties": {"q": {"type": "string", "description": "Keywords"}}}},
     {"name": "get", "description": "Get one catalog item by its id",
      "inputSchema": {"type": "object", "properties": {"id": {"type": "string", "description": "Item id"}}}}],
    [{"name": "count", "description": "Count the tool calls this server has seen",
      "inputSchema": {"type": "object", "properties": {}}}],
]
none = "--none" in sys.argv
calls = 0


def answer(msg):
    global calls
    method = msg.get("method")
    if method == "initialize":
        result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                  "serverInfo": {"name": "catalog", "version": "1.0"}}
        if not none:
            result["instructions"] = INSTRUCTIONS
    elif method == "tools/list":
        page = 1 if (msg.get("params") or {}).get("cursor") == "p2" else 0
        result = {"tools": PAGES[page]}
        if page == 0:
            result["nextCursor"] = "p2"
    elif method == "tools/call":
        calls += 1
        result = {"content": [{"type": "text", "text": json.dumps(
            {"calls": calls, "name": msg["params"]["name"]})}]}
    else:
        result = {}
    return {"jsonrpc": "2.0", "id": msg["id"], "result": result}


for line in sys.stdin:
    msg = json.loads(line)
    if isinstance(msg, list):
        replies = [answer(m) for m in msg if "id" in m]
        if replies:
            print(json.dumps(replies), flush=True)
    elif "id" in msg:
        print(json.dumps(answer(msg)), flush=True)
