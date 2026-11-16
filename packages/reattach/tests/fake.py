"""A fake stateful Streamable HTTP MCP server and a tiny ASGI client (no network, no SDK)."""

import asyncio
import json


class FakeMCP:
    """Keeps sessions in memory like the SDK: unknown or foreign session ids answer 404."""

    def __init__(self, style="json", require_auth=False, prefix="s"):
        self.style = style
        self.require_auth = require_auth
        self.prefix = prefix
        self.sessions = {}
        self.log = []
        self.n = 0

    async def __call__(self, scope, receive, send):
        body = b""
        while True:
            m = await receive()
            body += m.get("body", b"")
            if not m.get("more_body"):
                break
        h = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
        sid = h.get("mcp-session-id")
        auth = h.get("authorization")
        method = scope["method"]
        msg = json.loads(body) if body else None
        self.log.append((method, sid, msg.get("method") if isinstance(msg, dict) else None))
        if self.require_auth and not auth:
            return await respond(send, 401, b"", [(b"www-authenticate", b"Bearer")])
        if sid is not None and (sid not in self.sessions or self.sessions[sid]["auth"] != auth):
            return await respond(send, 404, json.dumps({
                "jsonrpc": "2.0", "id": None,
                "error": {"code": -32600, "message": "Session not found"}}).encode(),
                [(b"content-type", b"application/json")])
        if method == "GET":
            return await respond(send, 200, b": stream for " + sid.encode() + b"\n\n",
                                 [(b"content-type", b"text/event-stream")])
        if method == "DELETE":
            del self.sessions[sid]
            return await respond(send, 200, b"")
        if not isinstance(msg, dict):
            return await respond(send, 400, b"bad body")
        if msg.get("method") == "initialize":
            self.n += 1
            new = f"{self.prefix}{self.n}"
            self.sessions[new] = {"params": msg["params"], "auth": auth, "ready": False,
                                  "version": h.get("mcp-protocol-version")}
            result = {"protocolVersion": msg["params"]["protocolVersion"],
                      "capabilities": {"tools": {}}, "serverInfo": {"name": "fake", "version": "1"}}
            return await self.reply(send, {"jsonrpc": "2.0", "id": msg["id"], "result": result},
                                    [(b"mcp-session-id", new.encode())])
        if sid is None:
            return await respond(send, 400, b"no session")
        session = self.sessions[sid]
        if msg.get("method") == "notifications/initialized":
            session["ready"] = True
            session["version"] = h.get("mcp-protocol-version")
            return await respond(send, 202, b"", [(b"mcp-session-id", sid.encode())])
        if not session["ready"]:
            return await respond(send, 400, b"not initialized")
        text = f"{msg['params']['name']} ran in {sid}"
        return await self.reply(send, {"jsonrpc": "2.0", "id": msg["id"], "result": {
            "content": [{"type": "text", "text": text}]}}, [(b"mcp-session-id", sid.encode())])

    async def reply(self, send, msg, headers):
        if self.style == "sse":
            data = b"event: message\r\ndata: " + json.dumps(msg).encode() + b"\r\n\r\n"
            await send({"type": "http.response.start", "status": 200,
                        "headers": headers + [(b"content-type", b"text/event-stream")]})
            half = len(data) // 2
            await send({"type": "http.response.body", "body": data[:half], "more_body": True})
            await send({"type": "http.response.body", "body": data[half:], "more_body": False})
            return
        await respond(send, 200, json.dumps(msg).encode(),
                      headers + [(b"content-type", b"application/json")])


async def respond(send, status, body, headers=()):
    await send({"type": "http.response.start", "status": status,
                "headers": list(headers) + [(b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body, "more_body": False})


class Response:
    def __init__(self, status, headers, body):
        self.status, self.headers, self.body = status, headers, body

    def json(self):
        if self.headers.get("content-type", "").startswith("text/event-stream"):
            for line in self.body.decode().splitlines():
                if line.startswith("data:"):
                    return json.loads(line[5:])
        return json.loads(self.body)


async def request(app, method="POST", msg=None, session=None, auth=None, path="/mcp",
                  chunks=1, extra=()):
    body = json.dumps(msg).encode() if msg is not None else b""
    headers = [(b"content-type", b"application/json"),
               (b"accept", b"application/json, text/event-stream"), (b"host", b"example.com")]
    headers += list(extra)
    if session is not None:
        headers.append((b"mcp-session-id", session.encode()))
        headers.append((b"mcp-protocol-version", b"2025-06-18"))
    if auth is not None:
        headers.append((b"authorization", auth.encode()))
    size = max(1, -(-len(body) // chunks))
    parts = [body[i:i + size] for i in range(0, len(body), size)] or [b""]
    queue = [{"type": "http.request", "body": p, "more_body": i < len(parts) - 1}
             for i, p in enumerate(parts)]
    forever = asyncio.Event()

    async def receive():
        if queue:
            return queue.pop(0)
        await forever.wait()
        return {"type": "http.disconnect"}

    out = {"status": None, "headers": {}, "body": b""}

    async def send(message):
        if message["type"] == "http.response.start":
            assert out["status"] is None, "response started twice"
            out["status"] = message["status"]
            out["headers"] = {bytes(k).decode().lower(): bytes(v).decode()
                              for k, v in message.get("headers", [])}
        else:
            out["body"] += message.get("body", b"")

    scope = {"type": "http", "method": method, "path": path, "raw_path": path.encode(),
             "query_string": b"", "headers": headers, "scheme": "http"}
    await app(scope, receive, send)
    return Response(out["status"], out["headers"], out["body"])


INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
    "protocolVersion": "2025-06-18", "capabilities": {"sampling": {}},
    "clientInfo": {"name": "claude-ai", "version": "0.1.0",
                   "icons": [{"src": "data:image/png;base64," + "A" * 5000}]}}}
INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}


def call(name, id_=2):
    return {"jsonrpc": "2.0", "id": id_, "method": "tools/call",
            "params": {"name": name, "arguments": {}}}


async def connect(app, auth=None):
    """initialize + notifications/initialized like a client; returns the session id it keeps."""
    r = await request(app, msg=INIT, auth=auth)
    assert r.status == 200, r.body
    sid = r.headers["mcp-session-id"]
    r = await request(app, msg=INITIALIZED, session=sid, auth=auth)
    assert r.status == 202, r.body
    return sid
