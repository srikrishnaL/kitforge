"""End-to-end tests with a real MCP Python SDK server over Streamable HTTP.

The MCP SDK needs Python 3.10+, so on 3.9 only the SDK-free tests run.
"""

import asyncio
import io
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

mcp = pytest.importorskip("mcp")
httpx = pytest.importorskip("httpx")

from unstring.asgi import Unstring  # noqa: E402

HERE = Path(__file__).parent
SERVER = str(HERE / "servers" / "sdk_http_server.py")
sys.path.insert(0, str(HERE / "servers"))
import sdk_http_server  # noqa: E402

HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
BROKEN = {"titles": '["A", "B"]', "position": "2"}


def parse(resp):
    if resp.headers.get("content-type", "").startswith("text/event-stream"):
        for line in resp.text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:])
    return resp.json()


async def session_call(c, path="/mcp"):
    """initialize, then call create_pages with stringified arguments without a tools/list."""
    r = await c.post(path, headers=HEADERS, json={
        "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "t", "version": "0"}}})
    assert r.status_code == 200, r.text
    init = parse(r)
    h = dict(HEADERS)
    if "mcp-session-id" in r.headers:
        h["mcp-session-id"] = r.headers["mcp-session-id"]
    h["mcp-protocol-version"] = init["result"]["protocolVersion"]
    await c.post(path, headers=h, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    r = await c.post(path, headers=h, json={
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "create_pages", "arguments": BROKEN}})
    assert r.status_code == 200, r.text
    return parse(r)


def text_of(reply):
    assert "result" in reply, reply
    return reply["result"]["content"][0]["text"]


@pytest.mark.parametrize("json_response", [False, True])
def test_middleware_around_sdk_app_fetches_the_list_and_repairs(json_response):
    inner = sdk_http_server.make_app(json_response)
    log = io.StringIO()
    app = Unstring(inner, log=log)

    async def run():
        async with inner.router.lifespan_context(inner):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000",
                                         follow_redirects=True) as c:
                return await session_call(c)

    reply = asyncio.run(asyncio.wait_for(run(), 60))
    assert json.loads(text_of(reply)) == {"titles": ["A", "B"], "position": 2}
    assert "create_pages" in app.schemas, log.getvalue()
    assert "create_pages: titles: string -> array" in log.getvalue()


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start(cmd, port):
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
            return proc
        except OSError:
            if proc.poll() is not None:
                raise RuntimeError(proc.stderr.read().decode())
            time.sleep(0.1)
    proc.kill()
    raise RuntimeError(f"{cmd} did not listen on {port}")


def _stop(proc):
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def test_unstring_http_in_front_of_a_running_sdk_server():
    up, front = _free_port(), _free_port()
    server = _start([sys.executable, SERVER, str(up)], up)
    proxy = _start([sys.executable, "-m", "unstring", "http", f"http://127.0.0.1:{up}/mcp",
                    "--port", str(front)], front)

    async def run():
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{front}", timeout=30,
                                     follow_redirects=True) as c:
            return await session_call(c)

    try:
        reply = asyncio.run(asyncio.wait_for(run(), 60))
    finally:
        _stop(proxy)
        _stop(server)
    assert json.loads(text_of(reply)) == {"titles": ["A", "B"], "position": 2}
    err = proxy.stderr.read().decode()
    assert f"point your client at http://127.0.0.1:{front}/mcp" in err
    assert "create_pages: titles: string -> array" in err
