"""The ASGI middleware against a fake Streamable HTTP MCP app (no network, no SDK)."""

import asyncio
import json

import pytest

from briefed.asgi import Briefed

INSTRUCTIONS = "Call search before get."
TOOLS = [{"name": "search", "description": "Search the catalog by keyword",
          "inputSchema": {"type": "object", "properties": {}}}]


def answer(msg):
    method = msg.get("method")
    if method == "initialize":
        result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                  "serverInfo": {"name": "fake", "version": "1"}, "instructions": INSTRUCTIONS}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "ping":
        result = {}
    else:
        result = {"content": [{"type": "text", "text": f"server ran {msg['params']['name']}"}]}
    return {"jsonrpc": "2.0", "id": msg["id"], "result": result}


class FakeApp:
    """Answers POSTed JSON-RPC as JSON, or as SSE split into awkward chunks."""

    def __init__(self, style="json", sep=b"\r\n", status=200, encoding=None):
        self.style, self.sep, self.status, self.encoding = style, sep, status, encoding
        self.seen = []

    async def __call__(self, scope, receive, send):
        body = b""
        while True:
            m = await receive()
            body += m.get("body", b"")
            if not m.get("more_body"):
                break
        try:
            msg = json.loads(body) if body else None
        except ValueError:
            msg = None
        self.seen.append(msg)
        headers = [(b"mcp-session-id", b"abc")]
        if msg is None or (isinstance(msg, dict) and "id" not in msg):
            await send({"type": "http.response.start", "status": 202, "headers": headers})
            await send({"type": "http.response.body", "body": b""})
            return
        reply = [answer(m) for m in msg] if isinstance(msg, list) else answer(msg)
        if self.style == "json":
            data = json.dumps(reply).encode()
            headers += [(b"content-type", b"application/json"),
                        (b"content-length", str(len(data)).encode())]
            if self.encoding:
                headers.append((b"content-encoding", self.encoding))
            await send({"type": "http.response.start", "status": self.status, "headers": headers})
            half = len(data) // 2
            await send({"type": "http.response.body", "body": data[:half], "more_body": True})
            await send({"type": "http.response.body", "body": data[half:]})
            return
        s = self.sep
        stream = (b": ping" + s + s + b"event: message" + s + b"id: 7" + s + b"data: "
                  + json.dumps(reply).encode() + s + s)
        headers.append((b"content-type", b"text/event-stream"))
        await send({"type": "http.response.start", "status": 200, "headers": headers})
        for i in range(0, len(stream), 13):
            await send({"type": "http.response.body", "body": stream[i:i + 13], "more_body": True})
        await send({"type": "http.response.body", "body": b"", "more_body": False})


def request(app, body, method="POST", path="/mcp", chunks=2):
    """Run one request through ``app`` and return (status, headers, body, messages sent)."""
    raw = json.dumps(body).encode() if not isinstance(body, bytes) else body
    parts = [raw[i * len(raw) // chunks:(i + 1) * len(raw) // chunks] for i in range(chunks)]
    incoming = [{"type": "http.request", "body": p, "more_body": i < chunks - 1}
                for i, p in enumerate(parts)]
    sent = []

    async def receive():
        return incoming.pop(0) if incoming else {"type": "http.disconnect"}

    async def send(m):
        sent.append(m)

    scope = {"type": "http", "method": method, "path": path, "headers": []}
    asyncio.run(app(scope, receive, send))
    start = sent[0]
    data = b"".join(m.get("body", b"") for m in sent[1:])
    return start["status"], dict(start["headers"]), data, sent


def sse_messages(data):
    out = []
    for event in data.replace(b"\r\n", b"\n").split(b"\n\n"):
        lines = [ln[6:] for ln in event.split(b"\n") if ln.startswith(b"data: ")]
        if lines:
            out.append(json.loads(b"\n".join(lines)))
    return out


INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
LIST = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
CALL = {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "server_instructions", "arguments": {}}}


def test_json_responses_learn_and_fold():
    inner = FakeApp("json")
    app = Briefed(inner)
    status, headers, data, _ = request(app, INIT)
    assert json.loads(data)["result"]["instructions"] == INSTRUCTIONS
    status, headers, data, _ = request(app, LIST)
    tools = json.loads(data)["result"]["tools"]
    assert [t["name"] for t in tools] == ["search", "server_instructions"]
    assert headers[b"content-length"] == str(len(data)).encode()
    assert headers[b"mcp-session-id"] == b"abc"
    status, headers, data, _ = request(app, CALL)
    assert status == 200
    assert json.loads(data)["result"]["content"][0]["text"] == INSTRUCTIONS
    assert len(inner.seen) == 2  # the call never reached the server


@pytest.mark.parametrize("sep", [b"\r\n", b"\n"])
def test_sse_responses_are_rewritten_in_stream(sep):
    app = Briefed(FakeApp("sse", sep=sep), "Given text.", keep=False)
    _, headers, data, sent = request(app, INIT)
    assert b"content-length" not in headers
    init = sse_messages(data)
    assert len(init) == 1 and "instructions" not in init[0]["result"]
    assert b": ping" + sep + sep in data and b"event: message" + sep + b"id: 7" in data
    _, _, data, sent = request(app, LIST)
    tools = sse_messages(data)[0]["result"]["tools"]
    assert tools[-1]["name"] == "server_instructions"
    assert sent[-1]["more_body"] is False
    _, _, data, _ = request(app, CALL)
    assert json.loads(data)["result"]["content"][0]["text"] == "Given text."


def test_unrelated_requests_pass_through_untouched():
    inner = FakeApp("json")
    app = Briefed(inner, INSTRUCTIONS)
    ping = {"jsonrpc": "2.0", "id": 4, "method": "ping"}
    _, _, direct, _ = request(inner, ping)
    _, _, data, _ = request(app, ping)
    assert data == direct
    status, _, _, _ = request(app, {"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert status == 202
    status, _, _, _ = request(app, b"not json")
    assert status == 202 and inner.seen[-1] is None  # passed on for the server to reject


def test_get_and_other_paths_are_not_touched():
    inner = FakeApp("json")
    app = Briefed(inner, INSTRUCTIONS, path="/mcp")
    _, _, data, _ = request(app, LIST, path="/other")
    assert [t["name"] for t in json.loads(data)["result"]["tools"]] == ["search"]
    _, _, data, _ = request(app, LIST, path="/mcp/")
    assert json.loads(data)["result"]["tools"][-1]["name"] == "server_instructions"
    _, _, data, _ = request(app, LIST, method="GET")
    assert [t["name"] for t in json.loads(data)["result"]["tools"]] == ["search"]


def test_compressed_and_error_responses_pass_through():
    for inner in (FakeApp("json", encoding=b"gzip"), FakeApp("json", status=500)):
        _, _, data, _ = request(Briefed(inner, INSTRUCTIONS), LIST)
        assert [t["name"] for t in json.loads(data)["result"]["tools"]] == ["search"]


def test_batch_is_folded_but_call_inside_batch_reaches_server():
    app = Briefed(FakeApp("json"), INSTRUCTIONS)
    _, _, data, _ = request(app, [LIST, CALL])
    by_id = {m["id"]: m for m in json.loads(data)}
    assert by_id[2]["result"]["tools"][-1]["name"] == "server_instructions"
    assert by_id[3]["result"]["content"][0]["text"] == "server ran server_instructions"


def test_server_tool_with_same_name_is_left_to_server():
    class Own(FakeApp):
        pass

    inner = Own("json")
    global TOOLS
    saved = TOOLS
    TOOLS = saved + [{"name": "server_instructions", "description": "the server's own",
                      "inputSchema": {"type": "object"}}]
    try:
        app = Briefed(inner, INSTRUCTIONS)
        _, _, data, _ = request(app, LIST)
        assert len(json.loads(data)["result"]["tools"]) == 2
        _, _, data, _ = request(app, CALL)
        assert json.loads(data)["result"]["content"][0]["text"] == "server ran server_instructions"
    finally:
        TOOLS = saved


def test_without_instructions_nothing_changes():
    app = Briefed(FakeApp("json"))  # never saw initialize, given none
    _, _, data, _ = request(app, LIST)
    assert [t["name"] for t in json.loads(data)["result"]["tools"]] == ["search"]


def test_websocket_and_lifespan_scopes_go_straight_to_app():
    seen = []

    async def inner(scope, receive, send):
        seen.append(scope["type"])

    app = Briefed(inner, INSTRUCTIONS)
    asyncio.run(app({"type": "lifespan"}, None, None))
    assert seen == ["lifespan"]
