"""End-to-end tests with real MCP Python SDK servers and clients (whichever SDK is installed).

The MCP SDK needs Python 3.10+, so on 3.9 only the SDK-free tests run.
"""

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

mcp = pytest.importorskip("mcp")

from briefed.asgi import Briefed  # noqa: E402

HERE = Path(__file__).parent
SERVER = str(HERE / "servers" / "sdk_server.py")
sys.path.insert(0, str(HERE / "servers"))
import sdk_server  # noqa: E402

PROXY = [sys.executable, "-m", "briefed", "proxy", "--", sys.executable, SERVER]


def test_raw_jsonrpc_through_proxy():
    proc = subprocess.Popen(PROXY, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    try:
        def send(msg):
            proc.stdin.write((json.dumps(msg) + "\n").encode())
            proc.stdin.flush()

        def reply(id_):
            while True:
                msg = json.loads(proc.stdout.readline())
                if msg.get("id") == id_ and "method" not in msg:
                    return msg

        send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "t", "version": "0"}}})
        assert reply(1)["result"]["instructions"] == sdk_server.INSTRUCTIONS
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        tools = reply(2)["result"]["tools"]
        assert [t["name"] for t in tools] == ["search", "get", "server_instructions"]
        send({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
              "params": {"name": "server_instructions", "arguments": {}}})
        assert reply(3)["result"]["content"][0]["text"] == sdk_server.INSTRUCTIONS
        send({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
              "params": {"name": "search", "arguments": {"q": "ACME"}}})
        assert "found ACME" in reply(4)["result"]["content"][0]["text"]
    finally:
        proc.stdin.close()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()


async def _sdk_client_session():
    from mcp import StdioServerParameters

    params = StdioServerParameters(command=PROXY[0], args=PROXY[1:])
    try:  # SDK 2.x
        from mcp import Client

        async with Client(params) as client:
            tools = await client.list_tools()
            result = await client.call_tool("server_instructions", {})
            return [t.name for t in tools.tools], result.content[0].text
    except ImportError:
        pass
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            result = await session.call_tool("server_instructions", {})
            return [t.name for t in tools.tools], result.content[0].text


def test_sdk_client_through_proxy():
    names, text = asyncio.run(asyncio.wait_for(_sdk_client_session(), 60))
    assert names[-1] == "server_instructions" and "search" in names
    assert text == sdk_server.INSTRUCTIONS


@pytest.mark.parametrize("json_response", [False, True])
def test_asgi_with_sdk_streamable_http_app(json_response):
    try:
        import httpx  # what SDK 1.x uses
    except ImportError:
        httpx = pytest.importorskip("httpx2")  # what SDK 2.x uses
    try:  # SDK 2.x
        inner = sdk_server.srv.streamable_http_app(json_response=json_response)
    except TypeError:  # SDK 1.x keeps it in settings, and makes one session manager per server
        sdk_server.srv.settings.json_response = json_response
        sdk_server.srv._session_manager = None
        inner = sdk_server.srv.streamable_http_app()
    app = Briefed(inner, keep=False)
    headers = {"accept": "application/json, text/event-stream",
               "content-type": "application/json"}

    def parse(resp):
        if resp.headers["content-type"].startswith("text/event-stream"):
            for line in resp.text.splitlines():
                if line.startswith("data:"):
                    return json.loads(line[5:])
        return resp.json()

    async def run():
        async with inner.router.lifespan_context(inner):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000",
                                         follow_redirects=True) as c:
                r = await c.post("/mcp", headers=headers, json={
                    "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                        "protocolVersion": "2025-06-18", "capabilities": {},
                        "clientInfo": {"name": "t", "version": "0"}}})
                assert r.status_code == 200, r.text
                init = parse(r)
                h = dict(headers)
                if "mcp-session-id" in r.headers:
                    h["mcp-session-id"] = r.headers["mcp-session-id"]
                h["mcp-protocol-version"] = init["result"]["protocolVersion"]
                await c.post("/mcp", headers=h,
                             json={"jsonrpc": "2.0", "method": "notifications/initialized"})
                tools = parse(await c.post("/mcp", headers=h, json={
                    "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}))
                call = parse(await c.post("/mcp", headers=h, json={
                    "jsonrpc": "2.0", "id": 3, "method": "tools/call",
                    "params": {"name": "server_instructions", "arguments": {}}}))
                return init, tools, call

    init, tools, call = asyncio.run(run())
    assert "instructions" not in init["result"]
    assert [t["name"] for t in tools["result"]["tools"]][-1] == "server_instructions"
    assert call["result"]["content"][0]["text"] == sdk_server.INSTRUCTIONS
