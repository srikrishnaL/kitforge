"""The middleware against a fake stateful MCP server: restarts, expiry, replicas, credentials."""

import asyncio
import json

import pytest

from reattach import Reattach, Tokens
from fake import INIT, INITIALIZED, FakeMCP, call, connect, request


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 10))


@pytest.mark.parametrize("style", ["json", "sse"])
def test_client_keeps_a_token_and_calls_reach_the_server_session(style):
    server = FakeMCP(style=style)
    app = Reattach(server)

    async def go():
        sid = await connect(app)
        r = await request(app, msg=call("search"), session=sid)
        return sid, r

    sid, r = run(go())
    assert sid.startswith("ra1.") and sid not in server.sessions
    assert r.status == 200 and r.headers["mcp-session-id"] == sid
    assert r.json()["result"]["content"][0]["text"] == "search ran in s1"
    assert app.reattached == 0


@pytest.mark.parametrize("style", ["json", "sse"])
def test_session_survives_a_restart_of_server_and_middleware(style):
    async def go():
        sid = await connect(Reattach(FakeMCP(style=style), "k"))
        # new process: fresh server sessions, fresh middleware map, same secret
        server = FakeMCP(style=style, prefix="new")
        app = Reattach(server, "k")
        r = await request(app, msg=call("search"), session=sid)
        return sid, server, app, r

    sid, server, app, r = run(go())
    assert r.status == 200, r.body
    assert r.json()["result"]["content"][0]["text"] == "search ran in new1"
    assert r.headers["mcp-session-id"] == sid
    assert [m for _, _, m in server.log] == ["initialize", "notifications/initialized",
                                            "tools/call"]
    replayed = server.sessions["new1"]
    assert replayed["params"]["protocolVersion"] == "2025-06-18"
    assert replayed["params"]["capabilities"] == {"sampling": {}}
    assert replayed["params"]["clientInfo"] == {"name": "claude-ai", "version": "0.1.0"}
    assert replayed["version"] == "2025-06-18"
    assert app.reattached == 1


def test_expired_session_404_is_hidden_and_retried_once():
    server = FakeMCP()
    seen = []
    app = Reattach(server, on_reattach=lambda token, inner: seen.append((token, inner)))

    async def go():
        sid = await connect(app)
        server.sessions.clear()  # idle timeout on the server
        r1 = await request(app, msg=call("a"), session=sid)
        r2 = await request(app, msg=call("b", 3), session=sid)
        return sid, r1, r2

    sid, r1, r2 = run(go())
    assert r1.status == 200 and "a ran in s2" in r1.body.decode()
    assert r2.status == 200 and "b ran in s2" in r2.body.decode()
    assert seen == [(sid, "s2")]
    methods = [m for _, _, m in server.log]
    assert methods.count("initialize") == 2 and methods.count("tools/call") == 3


def test_request_body_in_many_chunks_is_replayed_whole_after_a_retry():
    server = FakeMCP()
    app = Reattach(server)

    async def go():
        sid = await connect(app)
        server.sessions.clear()
        return await request(app, msg=call("chunky"), session=sid, chunks=7)

    r = run(go())
    assert r.status == 200 and "chunky ran in s2" in r.body.decode()


def test_another_replica_reattaches_without_shared_state():
    a, b = FakeMCP(prefix="a"), FakeMCP(prefix="b")
    app_a, app_b = Reattach(a, "k"), Reattach(b, "k")

    async def go():
        sid = await connect(app_a)
        r1 = await request(app_b, msg=call("x"), session=sid)
        r2 = await request(app_a, msg=call("y", 3), session=sid)
        return r1, r2

    r1, r2 = run(go())
    assert "x ran in b1" in r1.body.decode() and "y ran in a1" in r2.body.decode()


def test_session_ids_that_are_not_tokens_pass_through():
    server = FakeMCP()
    app = Reattach(server)
    r = run(request(app, msg=call("x"), session="0f0e9c1f2b"))
    assert r.status == 404 and server.log == [("POST", "0f0e9c1f2b", "tools/call")]


def test_wrong_secret_or_tampered_token_is_not_trusted():
    async def go():
        sid = await connect(Reattach(FakeMCP(), "right"))
        server = FakeMCP()
        wrong = await request(Reattach(server, "wrong"), msg=call("x"), session=sid)
        head, _, sig = sid.split(".")
        forged = Tokens().make({"protocolVersion": "1999-01-01", "capabilities": {},
                                "clientInfo": {"name": "evil", "version": "1"}})
        tampered = f"{head}.{forged.split('.')[1]}.{sig}"
        server2 = FakeMCP()
        bad = await request(Reattach(server2, "right"), msg=call("x"), session=tampered)
        return wrong, server, bad, server2

    wrong, server, bad, server2 = run(go())
    assert wrong.status == 404 and len(server.log) == 1
    assert bad.status == 404 and len(server2.log) == 1


def test_unsigned_tokens_when_no_secret(monkeypatch):
    monkeypatch.delenv("REATTACH_SECRET", raising=False)

    async def go():
        sid = await connect(Reattach(FakeMCP()))
        return sid, await request(Reattach(FakeMCP()), msg=call("x"), session=sid)

    sid, r = run(go())
    assert sid.endswith(".") and r.status == 200


def test_secret_from_environment(monkeypatch):
    monkeypatch.setenv("REATTACH_SECRET", "from-env")

    async def go():
        sid = await connect(Reattach(FakeMCP()))
        ok = await request(Reattach(FakeMCP()), msg=call("x"), session=sid)
        no = await request(Reattach(FakeMCP(), "other"), msg=call("x"), session=sid)
        return sid, ok, no

    sid, ok, no = run(go())
    assert not sid.endswith(".") and ok.status == 200 and no.status == 404


def test_handshake_carries_the_current_credential_and_tokens_never_cross_users():
    server = FakeMCP()
    app = Reattach(server)

    async def go():
        sid = await connect(app, auth="Bearer alice")
        # restart, then the same token arrives with alice's and then mallory's credential
        server2 = FakeMCP()
        app2 = Reattach(server2)
        r_alice = await request(app2, msg=call("x"), session=sid, auth="Bearer alice")
        r_mallory = await request(app2, msg=call("y", 3), session=sid, auth="Bearer mallory")
        r_alice2 = await request(app2, msg=call("z", 4), session=sid, auth="Bearer alice")
        return server2, r_alice, r_mallory, r_alice2

    server2, r_alice, r_mallory, r_alice2 = run(go())
    assert server2.sessions["s1"]["auth"] == "Bearer alice"
    assert server2.sessions["s2"]["auth"] == "Bearer mallory"
    assert "x ran in s1" in r_alice.body.decode()
    assert "y ran in s2" in r_mallory.body.decode()  # mallory gets her own, new session
    assert "z ran in s1" in r_alice2.body.decode()


def test_refused_handshake_lets_the_server_answer_the_request():
    async def go():
        sid = await connect(Reattach(FakeMCP()))
        server = FakeMCP(require_auth=True)
        return server, await request(Reattach(server), msg=call("x"), session=sid)

    server, r = run(go())
    assert r.status == 401 and r.headers["www-authenticate"] == "Bearer"
    assert [m for _, _, m in server.log] == ["initialize", "tools/call"]


def test_concurrent_requests_after_restart_share_one_handshake():
    async def go():
        sid = await connect(Reattach(FakeMCP()))
        server = FakeMCP()
        app = Reattach(server)
        rs = await asyncio.gather(*[request(app, msg=call(f"t{i}", i + 10), session=sid)
                                    for i in range(8)])
        return server, rs

    server, rs = run(go())
    assert all(r.status == 200 for r in rs)
    assert [m for _, _, m in server.log].count("initialize") == 1
    assert len(server.sessions) == 1


def test_get_stream_reattaches_too():
    async def go():
        sid = await connect(Reattach(FakeMCP()))
        server = FakeMCP()
        return await request(Reattach(server), method="GET", session=sid)

    r = run(go())
    assert r.status == 200 and r.body == b": stream for s1\n\n"


def test_delete_ends_the_session_and_unknown_delete_is_a_no_op():
    server = FakeMCP()
    app = Reattach(server)

    async def go():
        sid = await connect(app)
        d1 = await request(app, method="DELETE", session=sid)
        d2 = await request(app, method="DELETE", session=sid)
        return d1, d2

    d1, d2 = run(go())
    assert d1.status == 200 and d2.status == 200
    assert server.sessions == {} and not app.sessions
    assert [m for m, _, _ in server.log].count("DELETE") == 1


def test_requests_without_session_and_other_paths_pass_through():
    server = FakeMCP()
    app = Reattach(server, path="/mcp")

    async def go():
        stateless = await request(app, msg=call("x"))  # e.g. 2026-07-28 or stateless servers
        other = await request(app, msg=INIT, path="/other")
        bad_json = await request(app, msg=None)
        return stateless, other, bad_json

    stateless, other, bad_json = run(go())
    assert stateless.status == 400
    assert other.headers["mcp-session-id"] == "s1"  # path not handled: server id unchanged
    assert bad_json.status == 400


def test_lifespan_and_websocket_scopes_pass_through():
    seen = []

    async def inner(scope, receive, send):
        seen.append(scope["type"])

    app = Reattach(inner)
    run(app({"type": "lifespan"}, None, None))
    run(app({"type": "websocket", "path": "/"}, None, None))
    assert seen == ["lifespan", "websocket"]


def test_failed_initialize_is_not_given_a_token():
    async def inner(scope, receive, send):
        while (await receive()).get("more_body"):
            pass
        await send({"type": "http.response.start", "status": 503,
                    "headers": [(b"mcp-session-id", b"zzz")]})
        await send({"type": "http.response.body", "body": b""})

    r = run(request(Reattach(inner), msg=INIT))
    assert r.status == 503 and r.headers["mcp-session-id"] == "zzz"


def test_map_is_capped_and_a_dropped_token_still_works():
    server = FakeMCP()
    app = Reattach(server, max_sessions=2)

    async def go():
        sids = [await connect(app) for _ in range(3)]
        r = await request(app, msg=call("x"), session=sids[0])
        return sids, r

    sids, r = run(go())
    assert r.status == 200 and "x ran in s4" in r.body.decode()
    assert len(app.sessions) == 2


def test_initialize_reply_with_jsonrpc_error_is_a_refused_handshake():
    class ErrorInit(FakeMCP):
        async def reply(self, send, msg, headers):
            if "result" in msg and "serverInfo" in msg["result"]:
                msg = {"jsonrpc": "2.0", "id": msg["id"],
                       "error": {"code": -32602, "message": "Unsupported protocol version"}}
            await super().reply(send, msg, headers)

    async def go():
        sid = await connect(Reattach(FakeMCP()))
        server = ErrorInit()
        return await request(Reattach(server), msg=call("x"), session=sid)

    r = run(go())
    assert r.status == 404  # the server's own answer to the stale id
    assert json.loads(r.body)["error"]["message"] == "Session not found"


def test_initialized_notification_is_sent_with_the_session(monkeypatch):
    async def go():
        sid = await connect(Reattach(FakeMCP()))
        server = FakeMCP()
        await request(Reattach(server), msg=call("x"), session=sid)
        return server

    server = run(go())
    assert server.log[1] == ("POST", "s1", "notifications/initialized")
    assert server.sessions["s1"]["ready"] is True


def test_initialize_and_initialized_by_the_client_are_not_doubled():
    server = FakeMCP()
    app = Reattach(server)
    run(connect(app))
    assert [m for _, _, m in server.log] == ["initialize", "notifications/initialized"]
    assert INITIALIZED["method"] == "notifications/initialized"


class OldSDK(FakeMCP):
    """MCP Python SDK before 1.11: an unknown session id is a 400, not a 404."""

    async def __call__(self, scope, receive, send):
        async def send400(message):
            if message["type"] == "http.response.start" and message["status"] == 404:
                message = dict(message, status=400)
                self.rewrote = True
            if message["type"] == "http.response.body" and getattr(self, "rewrote", False):
                message = dict(message, body=b"Bad Request: No valid session ID provided")
                self.rewrote = False
            await send(message)

        await super().__call__(scope, receive, send400)


def test_old_sdk_400_for_unknown_session_is_retried_but_other_400s_are_not():
    server = OldSDK()
    app = Reattach(server)

    async def go():
        sid = await connect(app)
        server.sessions.clear()
        healed = await request(app, msg=call("a"), session=sid)
        # a plain 400 from the server (here: a call before notifications/initialized)
        half_open = (await request(app, msg=INIT)).headers["mcp-session-id"]
        other = await request(app, msg=call("b", 3), session=half_open)
        return healed, other

    healed, other = run(go())
    assert healed.status == 200 and "a ran in s2" in healed.body.decode()
    assert other.status == 400 and other.body == b"not initialized"
    assert [m for _, _, m in server.log].count("initialize") == 3


class Disguised(FakeMCP):
    """A server that says "session gone" without the 404 the spec asks for."""

    def __init__(self, status, body, ctype=b"application/json", chunks=1, **kw):
        super().__init__(**kw)
        self.gone = (status, body, ctype, chunks)

    async def __call__(self, scope, receive, send):
        status, body, ctype, chunks = self.gone

        async def disguise(message):
            if message["type"] == "http.response.start" and message["status"] == 404:
                self.rewriting = True
                await send({"type": "http.response.start", "status": status,
                            "headers": [(b"content-type", ctype)]})
                size = max(1, -(-len(body) // chunks))
                for i in range(0, len(body), size):
                    await send({"type": "http.response.body", "body": body[i:i + size],
                                "more_body": i + size < len(body)})
                return
            if message["type"] == "http.response.body" and getattr(self, "rewriting", False):
                self.rewriting = False
                return
            await send(message)

        await super().__call__(scope, receive, disguise)


def _rpc_error(code, message):
    return json.dumps({"jsonrpc": "2.0", "id": 2,
                       "error": {"code": code, "message": message}}).encode()


GONE_REPLIES = [
    # TypeScript SDK example servers and many forks of them (ebay-mcp, ...)
    (400, _rpc_error(-32000, "Bad Request: No valid session ID provided"), b"application/json"),
    (400, b'{"error":"invalid_session","error_description":"Invalid or missing session ID"}',
     b"application/json"),
    (400, b"Session not found", b"text/plain"),
    (410, b"Session expired", b"text/plain"),
    # a JSON-RPC error with HTTP 200 (IONe) or 500 (Java servlet transports)
    (200, _rpc_error(-32002, "MCP session not found"), b"application/json"),
    (500, _rpc_error(-32603, "Session not found: 3f2a9c1e-0d4b"), b"application/json"),
    (200, _rpc_error(-32001, "Session abc123 not found or has expired"),
     b"application/json; charset=utf-8"),
]


@pytest.mark.parametrize("status,body,ctype", GONE_REPLIES)
@pytest.mark.parametrize("chunks", [1, 3])
def test_session_gone_without_a_404_is_also_reattached(status, body, ctype, chunks):
    server = Disguised(status, body, ctype, chunks)
    app = Reattach(server)

    async def go():
        sid = await connect(app)
        server.sessions.clear()  # restart or idle timeout on the server
        return await request(app, msg=call("a"), session=sid)

    r = run(go())
    assert r.status == 200, r.body
    assert "a ran in s2" in r.body.decode()
    assert app.reattached == 1


@pytest.mark.parametrize("status,body,ctype", GONE_REPLIES[1:])
def test_strict_mode_only_trusts_404(status, body, ctype):
    server = Disguised(status, body, ctype)
    app = Reattach(server, strict=True)

    async def go():
        sid = await connect(app)
        server.sessions.clear()
        return await request(app, msg=call("a"), session=sid)

    r = run(go())
    assert r.status == status and r.body == body
    assert app.reattached == 0


class Replies(FakeMCP):
    """Answers tools/call with a fixed reply, to check what is not mistaken for "gone"."""

    def __init__(self, status, body, ctype=b"application/json"):
        super().__init__()
        self.fixed = (status, body, ctype)
        self.calling = False

    async def __call__(self, scope, receive, send):
        async def fixed(message):
            if message["type"] == "http.response.start" and self.calling:
                status, body, ctype = self.fixed
                await send({"type": "http.response.start", "status": status,
                            "headers": [(b"content-type", ctype)]})
                await send({"type": "http.response.body", "body": body, "more_body": False})
                return
            if message["type"] == "http.response.body" and self.calling:
                return
            await send(message)

        await super().__call__(scope, receive, fixed)


NOT_GONE = [
    # a tool result that talks about sessions is still a result
    (200, json.dumps({"jsonrpc": "2.0", "id": 2, "result": {"content": [
        {"type": "text", "text": "Session not found in the booking system"}]}}).encode(),
     b"application/json"),
    # other errors, and errors whose text is about something else
    (200, _rpc_error(-32602, "Unknown tool: a"), b"application/json"),
    (500, _rpc_error(-32603, "Internal error"), b"application/json"),
    (400, b"Server not initialized", b"text/plain"),
    (500, b"Session not found", b"text/plain"),  # not JSON-RPC: a 500 needs the error shape
    (200, b"x" * 10000, b"application/json"),  # longer than anything held
]


@pytest.mark.parametrize("status,body,ctype", NOT_GONE)
def test_other_replies_pass_through_and_are_not_retried(status, body, ctype):
    server = Replies(status, body, ctype)
    app = Reattach(server)

    async def go():
        sid = await connect(app)
        server.calling = True
        try:
            return await request(app, msg=call("a"), session=sid)
        finally:
            server.calling = False

    r = run(go())
    assert r.status == status and r.body == body
    assert app.reattached == 0
    assert [m for _, _, m in server.log].count("initialize") == 1
