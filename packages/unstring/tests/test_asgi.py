"""The ASGI middleware against a fake Streamable HTTP MCP app (no network, no SDK)."""

import asyncio
import io
import json

import pytest

from unstring.asgi import LIST_ID_PREFIX, Unstring

SCHEMA = {"type": "object", "properties": {
    "pages": {"type": "array", "items": {"type": "object", "properties": {
        "title": {"type": "string"}, "position": {"type": "integer"}}}},
    "query": {"type": "string"}}}
TOOLS = [{"name": "create_pages", "inputSchema": SCHEMA},
         {"name": "count", "inputSchema": {"type": "object", "properties": {
             "n": {"type": "integer"}}}}]


class FakeApp:
    """Answers POSTed JSON-RPC as JSON or as SSE split into awkward chunks; records requests."""

    def __init__(self, style="json", sep=b"\r\n", pages=1, list_status=200):
        self.style, self.sep, self.pages, self.list_status = style, sep, pages, list_status
        self.seen = []      # parsed bodies, in order
        self.headers = []   # request headers, in order

    def answer(self, msg):
        method = msg.get("method")
        if method == "tools/list":
            page = int((msg.get("params") or {}).get("cursor") or 0)
            per = -(-len(TOOLS) // self.pages)
            result = {"tools": TOOLS[page * per:(page + 1) * per]}
            if page + 1 < self.pages:
                result["nextCursor"] = str(page + 1)
        elif method == "tools/call":
            result = {"content": [{"type": "text",
                                   "text": json.dumps(msg["params"]["arguments"])}]}
        else:
            result = {}
        return {"jsonrpc": "2.0", "id": msg["id"], "result": result}

    async def __call__(self, scope, receive, send):
        body = b""
        while True:
            m = await receive()
            body += m.get("body", b"")
            if not m.get("more_body"):
                break
        headers = dict(scope["headers"])
        if b"content-length" in headers:
            assert int(headers[b"content-length"]) == len(body)
        try:
            msg = json.loads(body) if body else None
        except ValueError:
            msg = None
        self.seen.append(msg)
        self.headers.append(headers)
        out_headers = [(b"mcp-session-id", b"abc")]
        if msg is None or (isinstance(msg, dict) and "id" not in msg):
            await send({"type": "http.response.start", "status": 202, "headers": out_headers})
            await send({"type": "http.response.body", "body": b""})
            return
        if (isinstance(msg, dict) and msg.get("method") == "tools/list"
                and self.list_status != 200):
            await send({"type": "http.response.start", "status": self.list_status,
                        "headers": out_headers + [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": b'{"error": "unauthorized"}'})
            return
        if isinstance(msg, list):
            reply = [self.answer(m) for m in msg if "id" in m]
        else:
            reply = self.answer(msg)
        if self.style == "json":
            data = json.dumps(reply).encode()
            out_headers += [(b"content-type", b"application/json"),
                            (b"content-length", str(len(data)).encode())]
            await send({"type": "http.response.start", "status": 200, "headers": out_headers})
            half = len(data) // 2
            await send({"type": "http.response.body", "body": data[:half], "more_body": True})
            await send({"type": "http.response.body", "body": data[half:]})
            return
        s = self.sep
        stream = (b": ping" + s + s + b"event: message" + s + b"id: 7" + s + b"data: "
                  + json.dumps(reply).encode() + s + s)
        out_headers.append((b"content-type", b"text/event-stream"))
        await send({"type": "http.response.start", "status": 200, "headers": out_headers})
        for i in range(0, len(stream), 13):
            await send({"type": "http.response.body", "body": stream[i:i + 13], "more_body": True})
        await send({"type": "http.response.body", "body": b"", "more_body": False})


def request(app, body, method="POST", path="/mcp", headers=None, chunks=2):
    """Run one request through ``app``; return (status, response body, JSON-RPC replies)."""
    raw = json.dumps(body).encode() if not isinstance(body, bytes) else body
    parts = [raw[i * len(raw) // chunks:(i + 1) * len(raw) // chunks] for i in range(chunks)]
    incoming = [{"type": "http.request", "body": p, "more_body": i < chunks - 1}
                for i, p in enumerate(parts)]
    hdrs = [(b"content-type", b"application/json"), (b"content-length", str(len(raw)).encode()),
            (b"accept", b"application/json, text/event-stream")]
    hdrs += [(k.encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {"type": "http", "method": method, "path": path, "headers": hdrs}
    sent = []

    async def receive():
        if incoming:
            return incoming.pop(0)
        await asyncio.sleep(3600)

    async def send(m):
        sent.append(m)

    asyncio.run(app(scope, receive, send))
    status = sent[0]["status"]
    data = b"".join(m.get("body", b"") for m in sent[1:])
    return status, data, replies(data)


def replies(data):
    try:
        parsed = json.loads(data)
        return parsed if isinstance(parsed, list) else [parsed]
    except ValueError:
        pass
    out = []
    for line in data.replace(b"\r\n", b"\n").split(b"\n"):
        if line.startswith(b"data:"):
            parsed = json.loads(line[5:])
            out += parsed if isinstance(parsed, list) else [parsed]
    return out


def call(name, arguments, id_=2, **params):
    return {"jsonrpc": "2.0", "id": id_, "method": "tools/call",
            "params": dict(params, name=name, arguments=arguments)}


LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
BROKEN = {"pages": '[{"title": "A", "position": "2"}]', "query": "[1, 2]"}
FIXED = {"pages": [{"title": "A", "position": 2}], "query": "[1, 2]"}


@pytest.mark.parametrize("style,sep", [("json", b""), ("sse", b"\r\n"), ("sse", b"\n")])
def test_learns_from_tools_list_and_repairs_calls(style, sep):
    inner = FakeApp(style, sep)
    log = io.StringIO()
    app = Unstring(inner, log=log)
    _, _, out = request(app, LIST)
    assert [t["name"] for t in out[0]["result"]["tools"]] == ["create_pages", "count"]
    assert set(app.schemas) == {"create_pages", "count"}
    _, _, out = request(app, call("create_pages", BROKEN))
    assert inner.seen[-1]["params"]["arguments"] == FIXED
    assert json.loads(out[0]["result"]["content"][0]["text"]) == FIXED
    assert len(inner.seen) == 2  # no tools/list of its own: the schema was known
    assert log.getvalue() == ("unstring: create_pages: pages: string -> array; "
                              "pages[0].position: string -> integer\n")


@pytest.mark.parametrize("style", ["json", "sse"])
def test_unknown_tool_fetches_the_list_with_the_callers_headers(style):
    inner = FakeApp(style)
    app = Unstring(inner, quiet=True)
    _, _, out = request(app, call("create_pages", BROKEN),
                        headers={"mcp-session-id": "abc", "authorization": "Bearer t"})
    assert [m["method"] for m in inner.seen] == ["tools/list", "tools/call"]
    assert inner.seen[0]["id"].startswith(LIST_ID_PREFIX)
    assert inner.headers[0][b"mcp-session-id"] == b"abc"
    assert inner.headers[0][b"authorization"] == b"Bearer t"
    assert inner.seen[1]["params"]["arguments"] == FIXED
    assert [m["id"] for m in out] == [2]  # the client sees only its own reply
    assert app.fixed_calls == 1


def test_fetch_follows_every_page():
    inner = FakeApp(pages=2)
    app = Unstring(inner, quiet=True)
    request(app, call("count", {"n": "5"}))
    assert [m["method"] for m in inner.seen] == ["tools/list", "tools/list", "tools/call"]
    assert inner.seen[1]["params"]["cursor"] == "1"
    assert inner.seen[-1]["params"]["arguments"] == {"n": 5}


def test_protocol_version_in_meta_goes_on_the_fetch():
    inner = FakeApp()
    app = Unstring(inner, quiet=True)
    meta = {"io.modelcontextprotocol/protocolVersion": "2026-07-28", "progressToken": 9}
    request(app, call("count", {"n": "5"}, _meta=meta))
    assert inner.seen[0]["params"]["_meta"] == {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28"}


def test_list_refused_passes_the_call_on_and_does_not_ask_again_at_once():
    inner = FakeApp(list_status=401)
    log = io.StringIO()
    app = Unstring(inner, log=log)
    request(app, call("count", {"n": "5"}))
    request(app, call("count", {"n": "6"}, id_=3))
    assert [m["method"] for m in inner.seen] == ["tools/list", "tools/call", "tools/call"]
    assert inner.seen[1]["params"]["arguments"] == {"n": "5"}  # untouched: no schema
    assert "did not list its tools (HTTP 401); passing count on as it is" in log.getvalue()


def test_tool_missing_from_the_list_is_not_fetched_for_every_call():
    inner = FakeApp()
    app = Unstring(inner, quiet=True)
    request(app, call("nope", {"x": "[1]"}))
    request(app, call("nope", {"x": "[1]"}, id_=3))
    assert [m["method"] for m in inner.seen] == ["tools/list", "tools/call", "tools/call"]


def test_preloaded_tools_and_fetch_off():
    inner = FakeApp()
    app = Unstring(inner, tools={"tools": TOOLS}, quiet=True)
    request(app, call("count", {"n": "5"}))
    assert [m["method"] for m in inner.seen] == ["tools/call"]
    assert inner.seen[0]["params"]["arguments"] == {"n": 5}

    inner = FakeApp()
    app = Unstring(inner, tools={"count": TOOLS[1]["inputSchema"]}, fetch=False, quiet=True)
    request(app, call("create_pages", BROKEN))
    assert inner.seen == [call("create_pages", BROKEN)]  # unknown and not fetched: untouched


def test_batch_repairs_each_call():
    inner = FakeApp()
    app = Unstring(inner, tools=TOOLS, quiet=True)
    request(app, [call("count", {"n": "1"}, id_=1), call("count", {"n": 2}, id_=2),
                  {"jsonrpc": "2.0", "method": "notifications/initialized"}])
    assert [m["params"]["arguments"] for m in inner.seen[0][:2]] == [{"n": 1}, {"n": 2}]
    assert inner.seen[0][2] == {"jsonrpc": "2.0", "method": "notifications/initialized"}


def test_batch_with_a_tools_list_learns_it():
    inner = FakeApp("sse")
    app = Unstring(inner, quiet=True)
    request(app, [LIST, {"jsonrpc": "2.0", "id": 9, "method": "ping"}])
    assert set(app.schemas) == {"create_pages", "count"}


def test_real_strings_and_clean_calls_reach_the_app_byte_for_byte():
    inner = FakeApp()
    app = Unstring(inner, tools=TOOLS, quiet=True)
    raw = b'{"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"create_pages",' \
          b'"arguments":{"query":"[1, 2]"}}}'
    seen_bodies = []

    async def spy(scope, receive, send):
        m = await receive()
        seen_bodies.append(m["body"])
        incoming = [m]

        async def again():
            return incoming.pop(0) if incoming else await receive()
        await inner(scope, again, send)

    app.app = spy
    request(app, raw, chunks=3)
    assert seen_bodies == [raw]


def test_other_methods_paths_and_bodies_pass_through():
    inner = FakeApp()
    app = Unstring(inner, tools=TOOLS, path="/mcp", quiet=True)
    request(app, call("count", {"n": "5"}), path="/other")
    assert inner.seen[-1]["params"]["arguments"] == {"n": "5"}
    request(app, b"not json")
    assert inner.seen[-1] is None
    status, _, _ = request(app, b"", method="GET")
    assert status == 202


def test_websocket_and_lifespan_scopes_go_straight_to_app():
    got = []

    async def inner(scope, receive, send):
        got.append(scope["type"])

    app = Unstring(inner)

    async def nothing():
        return {}

    asyncio.run(app({"type": "lifespan"}, nothing, nothing))
    asyncio.run(app({"type": "websocket", "path": "/mcp"}, nothing, nothing))
    assert got == ["lifespan", "websocket"]


def test_concurrent_unknown_calls_fetch_once():
    inner = FakeApp("sse")
    app = Unstring(inner, quiet=True)

    async def one(i):
        raw = json.dumps(call("count", {"n": str(i)}, id_=i)).encode()
        incoming = [{"type": "http.request", "body": raw, "more_body": False}]

        async def receive():
            if incoming:
                return incoming.pop(0)
            await asyncio.sleep(3600)

        async def send(m):
            pass

        await app({"type": "http", "method": "POST", "path": "/mcp", "headers": []},
                  receive, send)

    async def both():
        await asyncio.gather(one(1), one(2))

    asyncio.run(both())
    assert [m["method"] for m in inner.seen].count("tools/list") == 1
    assert sorted(m["params"]["arguments"]["n"] for m in inner.seen
                  if m["method"] == "tools/call") == [1, 2]


def test_fetch_follows_a_trailing_slash_redirect():
    inner = FakeApp()

    async def redirecting(scope, receive, send):
        if scope["path"] == "/mcp":
            await send({"type": "http.response.start", "status": 307,
                        "headers": [(b"location", b"http://127.0.0.1:8000/mcp/")]})
            await send({"type": "http.response.body", "body": b""})
            return
        await inner(scope, receive, send)

    app = Unstring(redirecting, quiet=True)
    status, _, _ = request(app, call("count", {"n": "5"}))
    assert status == 307  # the client follows the redirect itself
    status, _, _ = request(app, call("count", {"n": "5"}), path="/mcp/")
    assert status == 200
    assert [m["method"] for m in inner.seen] == ["tools/list", "tools/call"]
    assert inner.seen[-1]["params"]["arguments"] == {"n": 5}
