"""A stdio MCP server that validates tool arguments strictly, like a zod-based server.

Tools are listed in two pages. Each tools/call echoes the arguments it got, or returns
an "Invalid arguments" error when a value has the wrong type. ``--no-list`` makes
tools/list fail, ``--count`` adds how many tools/list requests it has answered.
"""

import json
import sys

PAGES = [
    [{"name": "create_pages", "description": "Create pages",
      "inputSchema": {"type": "object", "required": ["pages"], "properties": {
          "pages": {"type": "array", "items": {"$ref": "#/$defs/Page"}},
          "parent": {"type": "string"}},
          "$defs": {"Page": {"type": "object", "required": ["title"], "properties": {
              "title": {"type": "string"}, "tags": {"type": "array", "items": {"type": "string"}},
              "position": {"type": "integer"}}}}}},
     {"name": "search", "description": "Search",
      "inputSchema": {"type": "object", "properties": {
          "query": {"type": "string"}, "limit": {"type": "integer"},
          "exact": {"type": "boolean"}}}}],
    [{"name": "tag", "description": "Tag items",
      "inputSchema": {"type": "object", "properties": {
          "ids": {"type": "array", "items": {"type": "integer"}}}}}],
]
TYPES = {"string": str, "integer": int, "boolean": bool, "array": list, "object": dict}
lists = 0


def check(value, schema, defs, path):
    if "$ref" in schema:
        schema = defs[schema["$ref"].split("/")[-1]]
    t = schema.get("type")
    if t and (not isinstance(value, TYPES[t]) or (t == "integer" and isinstance(value, bool))):
        got = "string" if isinstance(value, str) else type(value).__name__
        return f"{path}: expected {t}, received {got}"
    for key, sub in (schema.get("properties") or {}).items():
        if isinstance(value, dict) and key in value:
            err = check(value[key], sub, defs, f"{path}.{key}")
            if err:
                return err
    if t == "array" and "items" in schema:
        for i, item in enumerate(value):
            err = check(item, schema["items"], defs, f"{path}[{i}]")
            if err:
                return err
    return None


def answer(msg):
    global lists
    method = msg.get("method")
    if method == "initialize":
        return {"result": {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                           "serverInfo": {"name": "strict", "version": "1.0"}}}
    if method == "tools/list":
        if "--no-list" in sys.argv:
            return {"error": {"code": -32601, "message": "Method not found"}}
        lists += 1
        page = 1 if (msg.get("params") or {}).get("cursor") == "p2" else 0
        result = {"tools": PAGES[page]}
        if page == 0:
            result["nextCursor"] = "p2"
        return {"result": result}
    if method == "tools/call":
        name = msg["params"]["name"]
        tool = next((t for p in PAGES for t in p if t["name"] == name), None)
        args = msg["params"].get("arguments")
        err = check(args, tool["inputSchema"], tool["inputSchema"].get("$defs", {}), "args") \
            if tool else f"unknown tool {name}"
        if err:
            return {"result": {"isError": True, "content": [
                {"type": "text", "text": f"Invalid arguments: {err}"}]}}
        out = {"args": args}
        if "--count" in sys.argv:
            out["lists"] = lists
        return {"result": {"content": [{"type": "text", "text": json.dumps(out)}]}}
    return {"result": {}}


for line in sys.stdin:
    msg = json.loads(line)
    for m in (msg if isinstance(msg, list) else [msg]):
        if "id" in m:
            print(json.dumps(dict(answer(m), jsonrpc="2.0", id=m["id"])), flush=True)
