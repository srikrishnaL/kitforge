"""End-to-end with real MCP Python SDK servers and clients (whichever SDK is installed).

The MCP SDK needs Python 3.10+, so on 3.9 only the SDK-free tests run.
"""

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

mcp = pytest.importorskip("mcp")
httpx = pytest.importorskip("httpx")

from reattach import Reattach  # noqa: E402

HERE = Path(__file__).parent
SERVER = str(HERE / "servers" / "sdk_server.py")
sys.path.insert(0, str(HERE / "servers"))
import sdk_server  # noqa: E402

HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}


def parse(resp):
    if resp.headers["content-type"].startswith("text/event-stream"):
        for line in resp.text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:])
    return resp.json()


@pytest.mark.parametrize("json_response", [False, True])
def test_sdk_session_survives_restart_and_idle_expiry(json_response):
    async def boot():
        inner = sdk_server.make_app(json_response)
        return inner, Reattach(inner, "k")

    async def run():
        inner1, app1 = await boot()
        async with inner1.router.lifespan_context(inner1):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app1),
                                         base_url="http://127.0.0.1:8000",
                                         follow_redirects=True) as c:
                r = await c.post("/mcp", headers=HEADERS, json={
                    "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                        "protocolVersion": "2025-06-18", "capabilities": {},
                        "clientInfo": {"name": "t", "version": "0"}}})
                assert r.status_code == 200, r.text
                sid = r.headers["mcp-session-id"]
                h = dict(HEADERS, **{"mcp-session-id": sid,
                                     "mcp-protocol-version": parse(r)["result"]["protocolVersion"]})
                r = await c.post("/mcp", headers=h,
                                 json={"jsonrpc": "2.0", "method": "notifications/initialized"})
                assert r.status_code == 202, r.text
                first = parse(await c.post("/mcp", headers=h, json={
                    "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                    "params": {"name": "search", "arguments": {"q": "a"}}}))
        # "restart": a new server process (fresh session manager) and a new middleware
        inner2, app2 = await boot()
        async with inner2.router.lifespan_context(inner2):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app2),
                                         base_url="http://127.0.0.1:8000",
                                         follow_redirects=True) as c:
                r = await c.post("/mcp", headers=h, json={
                    "jsonrpc": "2.0", "id": 3, "method": "tools/call",
                    "params": {"name": "search", "arguments": {"q": "b"}}})
                assert r.status_code == 200, r.text
                assert r.headers["mcp-session-id"] == sid
                second = parse(r)
                # the server forgets the session (idle timeout): the next call reattaches too
                inner2.mcp_server.session_manager._server_instances.clear()
                r = await c.post("/mcp", headers=h, json={
                    "jsonrpc": "2.0", "id": 4, "method": "tools/call",
                    "params": {"name": "search", "arguments": {"q": "c"}}})
                third = parse(r)
        return first, second, third, app2.reattached

    first, second, third, reattached = asyncio.run(asyncio.wait_for(run(), 60))
    assert "found a" in first["result"]["content"][0]["text"]
    assert "found b" in second["result"]["content"][0]["text"]
    assert "found c" in third["result"]["content"][0]["text"]
    assert reattached == 2


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start(cmd, port, boot):
    env = dict(os.environ, BOOT=str(boot), REATTACH_SECRET="test-secret")
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return proc
        except OSError:
            if proc.poll() is not None:
                raise RuntimeError(proc.stderr.read().decode()) from None
            time.sleep(0.1)
    proc.kill()
    raise RuntimeError("server did not start")


def _stop(proc):
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


async def _client_across_restart(url, restart):
    from mcp import ClientSession

    try:  # SDK 2.x
        from mcp.client.streamable_http import streamable_http_client as connect
    except ImportError:  # SDK 1.x
        from mcp.client.streamable_http import streamablehttp_client as connect
    texts = []
    async with connect(url) as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            texts.append((await session.call_tool("search", {"q": "a"})).content[0].text)
            await asyncio.get_running_loop().run_in_executor(None, restart)
            texts.append((await session.call_tool("search", {"q": "b"})).content[0].text)
    return texts


def test_sdk_client_keeps_working_when_the_server_restarts():
    port = _free_port()
    cmd = [sys.executable, SERVER, str(port)]
    procs = [_start(cmd, port, 1)]

    def restart():
        _stop(procs[0])
        procs[0] = _start(cmd, port, 2)

    try:
        texts = asyncio.run(asyncio.wait_for(
            _client_across_restart(f"http://127.0.0.1:{port}/mcp", restart), 90))
    finally:
        _stop(procs[0])
    assert texts == ["found a (boot 1)", "found b (boot 2)"]


def test_proxy_keeps_sdk_client_working_when_the_upstream_restarts():
    up, front = _free_port(), _free_port()
    upstream_cmd = [sys.executable, SERVER, str(up), "--bare"]
    procs = [_start(upstream_cmd, up, 1)]
    proxy = _start([sys.executable, "-m", "reattach", "proxy", f"http://127.0.0.1:{up}",
                    "--port", str(front)], front, 0)

    def restart():
        _stop(procs[0])
        procs[0] = _start(upstream_cmd, up, 2)

    try:
        texts = asyncio.run(asyncio.wait_for(
            _client_across_restart(f"http://127.0.0.1:{front}/mcp", restart), 90))
    finally:
        _stop(procs[0])
        _stop(proxy)
    assert texts == ["found a (boot 1)", "found b (boot 2)"]
    assert b"reattached session" in proxy.stderr.read()
