"""A stdio MCP server whose tools return every kind of content block, for the proxy tests.

Flags: --no-resources (no resources capability), --slow-read (never answers resources/read),
--read-error (answers resources/read with an error).
"""

import json
import sys

FLAGS = set(sys.argv[1:])
PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
RESOURCES = {
    "file:///design/frame-1.json": {"mimeType": "application/json",
                                    "text": '{"width": 320, "height": 640}'},
    "file:///design/frame-1.png": {"mimeType": "image/png", "blob": PNG},
}

RESULTS = {
    "structured_only": {"content": [], "structuredContent": {"temp": 21.5, "unit": "C"}},
    "links": {"content": [
        {"type": "resource_link", "uri": "file:///design/frame-1.json", "name": "frame-1",
         "mimeType": "application/json"},
        {"type": "resource_link", "uri": "file:///design/frame-1.png", "name": "frame-1.png"},
    ]},
    "embedded": {"content": [
        {"type": "text", "text": "Here is the file:"},
        {"type": "resource", "resource": {"uri": "file:///notes.md", "mimeType": "text/markdown",
                                          "text": "# Notes\nhello"}},
    ]},
    "plain": {"content": [{"type": "text", "text": "ok"}]},
}


def send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def main():
    for line in sys.stdin:
        msg = json.loads(line)
        method, mid = msg.get("method"), msg.get("id")
        if mid is None:
            continue
        if method == "initialize":
            caps = {"tools": {}} if "--no-resources" in FLAGS else {"tools": {}, "resources": {}}
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": "2025-06-18", "capabilities": caps,
                "serverInfo": {"name": "rich", "version": "0"}}})
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": mid, "result": {"tools": [
                {"name": n, "inputSchema": {"type": "object"}} for n in RESULTS]}})
        elif method == "tools/call":
            send({"jsonrpc": "2.0", "id": mid, "result": RESULTS[msg["params"]["name"]]})
        elif method == "resources/read":
            if "--slow-read" in FLAGS:
                continue
            uri = msg["params"]["uri"]
            if "--read-error" in FLAGS or uri not in RESOURCES:
                send({"jsonrpc": "2.0", "id": mid,
                      "error": {"code": -32002, "message": f"Resource not found: {uri}"}})
            else:
                send({"jsonrpc": "2.0", "id": mid, "result": {
                    "contents": [dict(RESOURCES[uri], uri=uri)]}})
        else:
            send({"jsonrpc": "2.0", "id": mid,
                  "error": {"code": -32601, "message": f"Method not found: {method}"}})


if __name__ == "__main__":
    main()
