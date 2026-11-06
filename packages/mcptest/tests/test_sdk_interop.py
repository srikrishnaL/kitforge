"""Run mcptest against servers built with the official Python SDK, if installed."""

import socket
import subprocess
import sys
import textwrap
import time

import pytest

from mcptest import MCPClient, lint_tools

mcpserver = pytest.importorskip("mcp.server.mcpserver", reason="official MCP SDK 2.x not installed")

SERVER = textwrap.dedent(
    """
    import sys
    from mcp.server.mcpserver import MCPServer

    app = MCPServer("sdk-demo")

    @app.tool()
    def add(a: int, b: int) -> int:
        \"\"\"Add two integers and return the sum.\"\"\"
        return a + b

    @app.tool()
    def fail() -> str:
        raise ValueError("boom")

    if __name__ == "__main__":
        if len(sys.argv) > 1:
            app.run(transport="streamable-http", host="127.0.0.1", port=int(sys.argv[1]))
        else:
            app.run()
    """
)


@pytest.fixture
def server_file(tmp_path):
    p = tmp_path / "sdk_server.py"
    p.write_text(SERVER)
    return p


def test_sdk_stdio(server_file):
    with MCPClient.stdio([sys.executable, str(server_file)]) as c:
        assert c.server_info["name"] == "sdk-demo"
        names = [t["name"] for t in c.list_tools()]
        assert names == ["add", "fail"]
        r = c.call_tool("add", {"a": 20, "b": 22})
        assert r.text == "42" and r.structured == {"result": 42}
        assert c.call_tool("fail").is_error
        # SDK-generated schemas have no parameter descriptions: mcptest says so.
        codes = {f.code for f in lint_tools(c.list_tools())}
        assert {"MCP004", "MCP007"} <= codes


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_sdk_streamable_http(server_file):
    port = _free_port()
    proc = subprocess.Popen([sys.executable, str(server_file), str(port)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        url = f"http://127.0.0.1:{port}/mcp"
        deadline = time.time() + 20
        last = None
        while time.time() < deadline:
            try:
                client = MCPClient.http(url, timeout=5)
                break
            except Exception as exc:  # server still starting
                last = exc
                time.sleep(0.3)
        else:
            proc.terminate()
            pytest.fail(f"SDK HTTP server did not start: {last}\n{proc.stderr.read().decode()[-2000:]}")
        with client:
            assert client.call_tool("add", {"a": 1, "b": 1}).text == "2"
    finally:
        proc.terminate()
        proc.wait(timeout=10)
