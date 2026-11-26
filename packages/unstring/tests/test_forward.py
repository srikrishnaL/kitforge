"""``unstring http``: the forwarding proxy, against fake upstreams through httpx (no sockets)."""

import asyncio
import io

import pytest

httpx = pytest.importorskip("httpx")

from unstring.asgi import Unstring  # noqa: E402
from unstring.cli import build_app, main  # noqa: E402
from unstring.forward import Forward, split_url  # noqa: E402
from test_asgi import BROKEN, FIXED, FakeApp, call, request  # noqa: E402


def client_for(upstream_app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=upstream_app))


@pytest.mark.parametrize("style", ["json", "sse"])
def test_http_proxy_fetches_the_list_upstream_and_repairs(style):
    upstream = FakeApp(style)
    log = io.StringIO()
    app = build_app("https://mcp.example.com/mcp", log=log, client=client_for(upstream))
    assert isinstance(app, Unstring) and isinstance(app.app, Forward) and app.path == "/mcp"
    status, _, out = request(app, call("create_pages", BROKEN),
                             headers={"mcp-session-id": "abc", "authorization": "Bearer t"})
    assert status == 200
    assert [m["method"] for m in upstream.seen] == ["tools/list", "tools/call"]
    assert upstream.headers[0][b"authorization"] == b"Bearer t"
    assert upstream.headers[1][b"host"] == b"mcp.example.com"
    assert upstream.seen[1]["params"]["arguments"] == FIXED
    assert [m["id"] for m in out] == [2]
    assert "create_pages: pages: string -> array" in log.getvalue()


def test_forward_keeps_path_query_and_headers_but_not_hop_by_hop():
    seen = {}

    async def upstream(scope, receive, send):
        seen["path"], seen["qs"] = scope["path"], scope["query_string"]
        seen["headers"] = {k.decode(): v.decode() for k, v in scope["headers"]}
        await send({"type": "http.response.start", "status": 307,
                    "headers": [(b"location", b"http://upstream:3000/mcp/"), (b"x-up", b"1")]})
        await send({"type": "http.response.body", "body": b""})

    proxy = Forward("http://upstream:3000", client=client_for(upstream))
    scope = {"type": "http", "method": "GET", "path": "/.well-known/oauth-protected-resource",
             "query_string": b"a=1",
             "headers": [(b"upgrade", b"h2c"), (b"x-api-key", b"secret"),
                         (b"accept-encoding", b"gzip"), (b"host", b"127.0.0.1:8080")]}
    sent = []
    incoming = [{"type": "http.request", "body": b""}]

    async def receive():
        if incoming:
            return incoming.pop(0)
        await asyncio.sleep(3600)

    async def send(m):
        sent.append(m)

    asyncio.run(asyncio.wait_for(proxy(scope, receive, send), 10))
    headers = dict(sent[0]["headers"])
    assert sent[0]["status"] == 307 and headers[b"location"] == b"/mcp/"
    assert headers[b"x-up"] == b"1"
    assert seen["path"] == "/.well-known/oauth-protected-resource" and seen["qs"] == b"a=1"
    assert seen["headers"]["x-api-key"] == "secret"
    assert seen["headers"]["host"] == "upstream:3000"
    assert seen["headers"]["accept-encoding"] == "identity"  # so responses can be read
    assert "upgrade" not in seen["headers"]


def test_upstream_down_is_a_502():
    async def handler(req):
        raise httpx.ConnectError("connection refused", request=req)

    proxy = Forward("http://upstream:3000",
                    client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    app = Unstring(proxy, quiet=True)
    status, data, _ = request(app, call("count", {"n": "5"}))
    assert status == 502 and b"upstream unavailable: ConnectError" in data


def test_split_url_and_cli_errors(capsys):
    assert split_url("https://mcp.example.com/mcp") == ("https://mcp.example.com", "/mcp")
    assert split_url("http://127.0.0.1:3000") == ("http://127.0.0.1:3000", "/")
    with pytest.raises(ValueError):
        split_url("mcp.example.com/mcp")
    with pytest.raises(SystemExit):
        main(["http", "ftp://x/mcp"])
    assert "not an http(s) URL" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main(["http", "--help"])
    assert "--port" in capsys.readouterr().out
