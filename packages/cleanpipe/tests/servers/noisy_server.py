"""A minimal stdio MCP server that pollutes its stdout the way real servers do.

Flags: --clean (no noise), --silent (never answers), --exit N (exit with code N on EOF),
--crash N (exit with code N right after answering initialize).
"""

import json
import sys

args = sys.argv[1:]
clean = "--clean" in args
out = sys.stdout.buffer


def flag(name, default=None):
    return int(args[args.index(name) + 1]) if name in args else default


def noise(data: bytes) -> None:
    if not clean:
        out.write(data)
        out.flush()


def send(msg) -> None:
    out.write(json.dumps(msg).encode() + b"\n")
    out.flush()


noise(b"Starting noisy-server v1.0 on stdio\n")
noise(b'{"event": "startup", "level": "info", "tools": {"count": 1}}\n')
noise(b"\x1b[32mINFO\x1b[0m cache warmed\r\n")
noise(b"bad bytes \xff\xfe here\n")

if "--silent" in args:
    for _ in sys.stdin.buffer:
        pass
    sys.exit(0)

for line in sys.stdin.buffer:
    if not line.strip():
        continue
    msg = json.loads(line)
    method, id_ = msg.get("method"), msg.get("id")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": id_, "result": {
            "protocolVersion": msg["params"]["protocolVersion"],
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "noisy", "version": "1.0"}}})
        if flag("--crash") is not None:
            sys.exit(flag("--crash"))
        noise(b"[INFO] client connected\n")
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": id_, "result": {"tools": [{
            "name": "echo", "description": "Echo the text back.",
            "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}},
                            "required": ["text"]}}]}})
    elif method == "tools/call":
        noise(b"calling echo... ")  # no newline: the reply lands on the same line
        text = msg["params"]["arguments"]["text"]
        send({"jsonrpc": "2.0", "id": id_, "result": {
            "content": [{"type": "text", "text": text}], "isError": False}})
    elif id_ is not None:
        send({"jsonrpc": "2.0", "id": id_, "error": {"code": -32601, "message": "no such method"}})

sys.exit(flag("--exit", 0))
