"""The reverse proxy, against fake upstreams through httpx transports (no sockets)."""

import asyncio

import pytest

httpx = pytest.importorskip("httpx")

from reattach import Reattach  # noqa: E402
from reattach.cli import build_app, main  # noqa: E402
from reattach.proxy import Proxy  # noqa: E402
from fake import FakeMCP, call, connect, request  # noqa: E402


class Switch:
    """An upstream that can be "restarted" by swapping the app behind it."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        await self.app(scope, receive, send)


def proxied(upstream_app):
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=upstream_app))
    return Proxy("http://upstream:3000", client=client)


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 10))


@pytest.mark.parametrize("style", ["json", "sse"])
def test_proxy_reattaches_when_the_upstream_restarts(style):
    switch = Switch(FakeMCP(style=style))
    app = Reattach(proxied(switch), "k")

    async def go():
        sid = await connect(app)
        before = await request(app, msg=call("a"), session=sid)
        switch.app = FakeMCP(style=style, prefix="new")  # upstream restarted
        after = await request(app, msg=call("b", 3), session=sid)
        return sid, before, after

    sid, before, after = run(go())
    assert "a ran in s1" in before.body.decode()
    assert after.status == 200 and "b ran in new1" in after.body.decode()
    assert after.headers["mcp-session-id"] == sid
    assert app.reattached == 1


def test_proxy_forwards_path_query_and_headers_but_not_hop_by_hop():
    seen = {}

    async def upstream(scope, receive, send):
        seen["path"], seen["qs"] = scope["path"], scope["query_string"]
        seen["headers"] = {k.decode(): v.decode() for k, v in scope["headers"]}
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"application/json"), (b"x-up", b"1")]})
        await send({"type": "http.response.body", "body": b'{"ok":true}'})

    async def go():
        scope_extra = [(b"upgrade", b"h2c"), (b"x-api-key", b"secret")]
        return await request(proxied(upstream), method="GET",
                             path="/.well-known/oauth-protected-resource", extra=scope_extra)

    r = run(go())
    assert r.status == 200 and r.body == b'{"ok":true}' and r.headers["x-up"] == "1"
    assert seen["path"] == "/.well-known/oauth-protected-resource"
    assert seen["headers"]["x-api-key"] == "secret"
    assert seen["headers"]["x-forwarded-host"] == "example.com"
    assert seen["headers"]["host"] == "upstream:3000"
    assert "upgrade" not in seen["headers"]


def test_upstream_down_is_a_502_and_recovers():
    good = FakeMCP()

    async def handler(request):
        raise httpx.ConnectError("connection refused", request=request)

    down = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    up = httpx.AsyncClient(transport=httpx.ASGITransport(app=good))
    proxy = Proxy("http://upstream:3000", client=up)
    app = Reattach(proxy)

    async def go():
        sid = await connect(app)
        proxy.client = down
        r1 = await request(app, msg=call("a"), session=sid)
        proxy.client = up
        good.sessions.clear()  # it came back empty
        r2 = await request(app, msg=call("b", 3), session=sid)
        return r1, r2

    r1, r2 = run(go())
    assert r1.status == 502 and "upstream unavailable" in r1.body.decode()
    assert r2.status == 200 and "b ran in s2" in r2.body.decode()


def test_lifespan_closes_the_client():
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    proxy = Proxy("http://u", client=client)
    msgs = [{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}]
    sent = []

    async def receive():
        return msgs.pop(0)

    async def send(m):
        sent.append(m["type"])

    run(proxy({"type": "lifespan"}, receive, send))
    assert sent == ["lifespan.startup.complete", "lifespan.shutdown.complete"]
    assert client.is_closed


def test_cli_builds_the_wrapped_proxy_and_has_help(capsys):
    app = build_app("http://127.0.0.1:3000/", secret="s", path="/mcp")
    assert isinstance(app, Reattach) and isinstance(app.app, Proxy)
    assert app.app.upstream == "http://127.0.0.1:3000" and app.path == "/mcp"
    with pytest.raises(SystemExit):
        main(["proxy", "--help"])
    assert "--port" in capsys.readouterr().out
    assert main([]) == 2
