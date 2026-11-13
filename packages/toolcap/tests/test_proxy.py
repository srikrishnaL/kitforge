import json
import subprocess
import sys
from pathlib import Path

import pytest

from toolcap import Cap
from toolcap.cli import main
from toolcap.proxy import CapProxy

SERVER = str(Path(__file__).parent / "servers" / "big_server.py")


class Session:
    """The proxy around the test server, driven one request at a time."""

    def __init__(self, *flags):
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "toolcap", "proxy", *flags, "--", sys.executable, SERVER],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.next_id = 0

    def send(self, method, params=None):
        self.next_id += 1
        msg = {"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params or {}}
        self.proc.stdin.write((json.dumps(msg) + "\n").encode())
        self.proc.stdin.flush()
        reply = json.loads(self.proc.stdout.readline())
        assert reply["id"] == self.next_id
        return reply["result"]

    def call(self, name, **args):
        return self.send("tools/call", {"name": name, "arguments": args})

    def close(self):
        self.proc.stdin.close()
        code = self.proc.wait(timeout=30)
        log = self.proc.stderr.read().decode()
        assert code == 0, log
        return log


@pytest.fixture
def session():
    s = Session("--max-tokens", "1000")
    yield s
    if s.proc.poll() is None:
        s.proc.kill()


def text(result):
    return result["content"][-1]["text"]


def test_tools_get_a_cursor_parameter(session):
    tools = {t["name"]: t for t in session.send("tools/list")["tools"]}
    assert tools["rows"]["inputSchema"]["properties"]["cursor"]["type"] == "string"
    assert "next_cursor" in tools["rows"]["description"]
    assert "outputSchema" not in tools["rows"]
    assert tools["log"]["inputSchema"]["properties"] == {"cursor": tools["rows"]["inputSchema"]["properties"]["cursor"]}
    assert tools["own"]["inputSchema"]["properties"] == {"cursor": {"type": "string"}}  # untouched
    session.close()


def test_pages_come_from_memory(session):
    session.send("tools/list")
    first = session.call("rows", n=100)
    assert "structuredContent" not in first
    doc = json.loads(text(first))
    assert doc["page"]["total"] == 100 and doc["items"][0]["id"] == 0
    seen = len(doc["items"])
    cursor = doc["page"]["next_cursor"]
    while cursor:
        doc = json.loads(text(session.call("rows", n=100, cursor=cursor)))
        assert doc["items"][0]["id"] == seen
        seen += len(doc["items"])
        cursor = doc["page"]["next_cursor"]
    assert seen == 100
    # The server ran `rows` once; every later page came from the proxy.
    assert json.loads(text(session.call("calls")))["calls"] == 2
    log = session.close()
    assert "rows: result of about" in log and "paged to fit 1,000" in log


def test_small_results_and_empty_cursor(session):
    session.send("tools/list")
    small = session.call("rows", n=2, cursor="")
    assert "structuredContent" in small  # passed through unchanged
    assert json.loads(text(session.call("calls", cursor="")))["args"] == {}  # cursor not forwarded
    session.close()


def test_text_is_paged_and_other_blocks_kept(session):
    session.send("tools/list")
    first = session.call("log")
    assert first["content"][0]["type"] == "image"
    assert "Output cut: showing characters 1-" in text(first)
    cursor = text(first).split('cursor="')[1].split('"')[0]
    second = session.call("log", cursor=cursor)
    assert [b["type"] for b in second["content"]] == ["text"]
    assert text(second).startswith("line ")
    session.close()


def test_bad_cursor_is_a_tool_error(session):
    session.send("tools/list")
    result = session.call("rows", cursor="nonsense")
    assert result["isError"] is True and "Invalid cursor" in text(result)
    session.close()


def test_tools_with_their_own_cursor_are_trimmed(session):
    session.send("tools/list")
    result = session.call("own", cursor="abc")
    assert "more characters cut" in text(result) or "Output cut" in text(result)
    assert json.loads(text(session.call("calls")))["calls"] == 2  # forwarded with its cursor
    session.close()


def test_trim_mode_adds_nothing():
    s = Session("--max-tokens", "1000", "--trim", "--quiet")
    tools = {t["name"]: t for t in s.send("tools/list")["tools"]}
    assert "cursor" not in tools["rows"]["inputSchema"]["properties"]
    doc = json.loads(text(s.call("rows", n=100)))
    assert "next_cursor" not in doc["page"] and "left out" in doc["page"]["note"]
    assert s.close() == ""


def test_cache_miss_calls_the_server_again():
    proxy = CapProxy(Cap(max_tokens=200), quiet=True)
    proxy.to_server({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    proxy.to_client({"jsonrpc": "2.0", "id": 1, "result": {"tools": [
        {"name": "t", "inputSchema": {"type": "object"}}]}})
    rows = [{"i": i} for i in range(100)]
    big = {"jsonrpc": "2.0", "id": 2, "result": {"content": [{"type": "text", "text": json.dumps(rows)}]}}
    proxy.to_server({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "t"}})
    cursor = json.loads(proxy.to_client(big)[0]["result"]["content"][0]["text"])["page"]["next_cursor"]
    proxy._cache.clear()
    call = {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "t", "arguments": {"cursor": cursor}}}
    forwarded, replies = proxy.to_server(call)
    assert forwarded["params"]["arguments"] == {} and replies == []
    page = json.loads(proxy.to_client(dict(big, id=3))[0]["result"]["content"][0]["text"])
    start = int(page["page"]["items"].split("-")[0])
    assert start > 1 and page["items"][0] == {"i": start - 1}


def test_proxy_needs_a_command(capsys):
    with pytest.raises(SystemExit):
        main(["proxy"])
    assert "needs the server command" in capsys.readouterr().err
