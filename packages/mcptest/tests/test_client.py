import sys

import pytest

from mcptest import MCPClient, MCPError, MCPTimeout, MCPTransportError


def test_handshake_and_list(fake_cmd):
    with MCPClient.stdio(fake_cmd) as c:
        assert c.server_info == {"name": "fake", "version": "1.0"}
        assert c.protocol_version == "2025-06-18"
        assert c.instructions == "Test server."
        assert [t["name"] for t in c.list_tools()] == ["add", "echo"]
        assert c.tool("echo")["inputSchema"]["required"] == ["text"]
        with pytest.raises(KeyError):
            c.tool("nope")


def test_call_tool_structured_and_text(fake_cmd):
    with MCPClient.stdio(fake_cmd) as c:
        r = c.call_tool("add", {"a": 2, "b": 3})
        assert not r.is_error
        assert r.text == "5"
        assert r.structured == {"result": 5}
        assert r.json() == {"result": 5}
        assert c.call_tool("echo", {"text": "hi"}).text == "hi"


def test_unknown_tool_is_tool_error(fake_cmd):
    with MCPClient.stdio(fake_cmd) as c:
        r = c.call_tool("nope")
        assert r.is_error
        assert "Unknown tool" in r.text


def test_unknown_method_raises(fake_cmd):
    with MCPClient.stdio(fake_cmd) as c:
        with pytest.raises(MCPError) as e:
            c.request("bogus/method")
        assert e.value.code == -32601
        assert "bogus/method" in str(e.value)


def test_pagination(fake_cmd):
    with MCPClient.stdio(fake_cmd, env={"FAKE_TOOLS": "many"}) as c:
        names = [t["name"] for t in c.list_tools()]
        assert len(names) == 25
        assert names[0] == "tool_00" and names[-1] == "tool_24"


def test_resources_skipped_without_capability(fake_cmd):
    with MCPClient.stdio(fake_cmd) as c:
        assert c.list_resources() == []
        assert c.list_prompts() == []


def test_stdout_junk_is_reported_not_fatal(fake_cmd):
    with MCPClient.stdio(fake_cmd, env={"FAKE_JUNK": "1"}) as c:
        assert len(c.list_tools()) == 2
        assert "Server starting on stdio" in c._transport.diagnostics()


def test_crash_reports_stderr(fake_cmd):
    with MCPClient.stdio(fake_cmd, env={"FAKE_CRASH": "1"}) as c:
        with pytest.raises(MCPTransportError) as e:
            c.list_tools()
    msg = str(e.value)
    assert "database not configured" in msg


def test_timeout(fake_cmd):
    with MCPClient.stdio(fake_cmd, env={"FAKE_SLOW": "2"}) as c:
        with pytest.raises(MCPTimeout):
            c.call_tool("echo", {"text": "x"}, timeout=0.3)


def test_answers_server_ping(fake_cmd):
    with MCPClient.stdio(fake_cmd, env={"FAKE_PING": "1"}) as c:
        assert len(c.list_tools()) == 2


def test_missing_executable():
    with pytest.raises(MCPTransportError):
        MCPClient.stdio(["definitely-not-a-real-binary-xyz"])


def test_string_command(fake_cmd):
    cmd = f'"{sys.executable}" "{fake_cmd[1]}"'
    with MCPClient.stdio(cmd) as c:
        assert c.server_info["name"] == "fake"


def test_http_transport(http_server):
    url, seen = http_server
    with MCPClient.http(url, headers={"Authorization": "Bearer t"}) as c:
        assert c.server_info["name"] == "fake-http"
        assert [t["name"] for t in c.list_tools()] == ["add", "echo"]
        assert c.call_tool("add", {"a": 1, "b": 41}).text == "42"
        with pytest.raises(MCPError):
            c.request("bogus")
    methods = [m for m, _ in seen]
    assert methods[:2] == ["initialize", "notifications/initialized"]
    assert methods[-1] == "DELETE"
    later = dict(seen[2][1])
    assert later.get("Mcp-Session-Id") == "sess-123"
    assert later.get("Mcp-Protocol-Version") == "2025-06-18"
    assert later.get("Authorization") == "Bearer t"


def test_http_unreachable():
    with pytest.raises(MCPTransportError):
        MCPClient.http("http://127.0.0.1:9/mcp", timeout=2)
