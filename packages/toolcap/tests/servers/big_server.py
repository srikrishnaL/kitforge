"""A minimal stdio MCP server for the proxy tests, with tools that return too much.

``rows`` returns N JSON rows, ``log`` returns long text, ``own`` has its own cursor
parameter, and ``calls`` says how many tools/call requests the server has seen.
"""

import json
import sys

TOOLS = [
    {"name": "rows", "description": "List rows",
     "inputSchema": {"type": "object", "properties": {"n": {"type": "integer"}}},
     "outputSchema": {"type": "object"}},
    {"name": "log", "inputSchema": {"type": "object"}},
    {"name": "own", "description": "Pages itself",
     "inputSchema": {"type": "object", "properties": {"cursor": {"type": "string"}}}},
    {"name": "calls", "description": "Count calls", "inputSchema": {"type": "object"}},
]
calls = 0

for line in sys.stdin:
    msg = json.loads(line)
    if "id" not in msg:
        continue
    method = msg.get("method")
    if method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        calls += 1
        name, args = msg["params"]["name"], msg["params"].get("arguments") or {}
        if name == "rows":
            rows = [{"id": i, "text": "x" * 50} for i in range(args.get("n", 100))]
            result = {"content": [{"type": "text", "text": json.dumps(rows, indent=2)}],
                      "structuredContent": {"rows": rows}}
        elif name == "log":
            text = "\n".join(f"line {i}: " + "y" * 40 for i in range(300))
            result = {"content": [{"type": "image", "data": "AAAA", "mimeType": "image/png"},
                                  {"type": "text", "text": text}]}
        elif name == "own":
            result = {"content": [{"type": "text", "text": "z" * 5000}]}
        else:
            result = {"content": [{"type": "text", "text": json.dumps(
                {"calls": calls, "args": args})}]}
    else:
        result = {}
    print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}), flush=True)
