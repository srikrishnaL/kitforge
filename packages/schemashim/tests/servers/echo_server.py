"""A minimal stdio MCP server for the proxy tests: one tool whose schema Gemini rejects.

tools/call answers with the arguments it received, so the tests can see what the proxy sent.
"""

import json
import sys

TOOLS = [
    {"name": "search", "description": "Search issues",
     "inputSchema": {
         "type": "object",
         "$defs": {"Range": {"type": "object", "properties": {"from": {"type": "integer"},
                                                               "to": {"type": "integer"}}}},
         "properties": {
             "query": {"type": "string"},
             "limit": {"anyOf": [{"type": "integer"}, {"type": "null"}], "default": None},
             "labels": {"type": "array"},
             "range": {"$ref": "#/$defs/Range"},
         },
         "required": ["query"]}},
    {"name": "ping", "description": "Ping",
     "inputSchema": {"type": "object", "properties": {}}},
]

for line in sys.stdin:
    msg = json.loads(line)
    if "id" not in msg:
        continue
    method = msg.get("method")
    if method == "initialize":
        result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                  "serverInfo": {"name": "echo", "version": "1"}}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        result = {"content": [{"type": "text", "text": json.dumps(msg["params"])}]}
    else:
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"],
                          "error": {"code": -32601, "message": "no such method"}}), flush=True)
        continue
    print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}), flush=True)
