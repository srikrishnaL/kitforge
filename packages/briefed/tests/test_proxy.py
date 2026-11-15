import json
import subprocess
import sys
from pathlib import Path

import pytest

SERVER = str(Path(__file__).parent / "servers" / "plain_server.py")


class Session:
    """The proxy around the test server, driven one request at a time."""

    def __init__(self, *flags, server_args=()):
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "briefed", "proxy", *flags, "--", sys.executable, SERVER,
             *server_args],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.next_id = 0

    def raw(self, data: bytes) -> bytes:
        self.proc.stdin.write(data)
        self.proc.stdin.flush()
        return self.proc.stdout.readline()

    def send(self, method, params=None):
        self.next_id += 1
        msg = {"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params or {}}
        reply = json.loads(self.raw((json.dumps(msg) + "\n").encode()))
        assert reply["id"] == self.next_id
        return reply["result"]

    def init(self):
        result = self.send("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                          "clientInfo": {"name": "t", "version": "0"}})
        self.proc.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        self.proc.stdin.flush()
        return result

    def tools(self, cursor=None):
        return self.send("tools/list", {"cursor": cursor} if cursor else {})

    def call(self, name):
        result = self.send("tools/call", {"name": name, "arguments": {}})
        return result["content"][0]["text"]

    def close(self):
        self.proc.stdin.close()
        code = self.proc.wait(timeout=30)
        log = self.proc.stderr.read().decode()
        assert code == 0, log
        return log


@pytest.fixture
def sessions():
    made = []

    def make(*flags, **kw):
        s = Session(*flags, **kw)
        made.append(s)
        return s

    yield make
    for s in made:
        if s.proc.poll() is None:
            s.proc.kill()
            s.proc.wait()


def test_tool_mode_end_to_end(sessions):
    s = sessions()
    assert s.init()["instructions"] == "Call search before get. All dates are UTC."
    page = s.tools()
    names = [t["name"] for t in page["tools"]]
    assert names == ["search", "get", "server_instructions"]
    assert page["nextCursor"] == "p2"
    assert "(Call server_instructions once" in page["tools"][0]["description"]
    second = s.tools("p2")  # later pages are passed on unchanged
    assert [t["name"] for t in second["tools"]] == ["count"]
    assert second["tools"][0]["description"] == "Count the tool calls this server has seen"
    assert s.call("server_instructions") == "Call search before get. All dates are UTC."
    # the proxy answered that call itself; the server saw only this one
    assert json.loads(s.call("count")) == {"calls": 1, "name": "count"}
    s.close()


def test_move_removes_instructions_from_initialize(sessions):
    s = sessions("--move")
    assert "instructions" not in s.init()
    assert s.call("server_instructions").startswith("Call search")
    s.close()


def test_override_and_add_from_file(sessions, tmp_path):
    f = tmp_path / "rules.md"
    f.write_text("Never delete items.\n", encoding="utf-8")
    s = sessions("--instructions", "Only read.", "--add", f"@{f}")
    s.init()
    s.tools()
    assert s.call("server_instructions") == "Only read.\n\nNever delete items."
    s.close()


def test_server_without_instructions_passes_through(sessions):
    s = sessions(server_args=["--none"])
    line = json.dumps({"jsonrpc": "2.0", "id": 9, "method": "tools/list", "params": {}}) + "\n"
    s.init()
    reply = s.raw(line.encode())
    assert [t["name"] for t in json.loads(reply)["result"]["tools"]] == ["search", "get"]
    # nothing to answer, so the call reaches the server
    assert json.loads(s.call("server_instructions"))["name"] == "server_instructions"
    s.close()


def test_add_alone_works_for_servers_without_instructions(sessions):
    s = sessions("--add", "Dates are UTC.", server_args=["--none"])
    s.init()
    assert [t["name"] for t in s.tools()["tools"]][-1] == "server_instructions"
    assert s.call("server_instructions") == "Dates are UTC."
    s.close()


def test_inline_mode(sessions):
    s = sessions("--mode", "inline", "--target", "get")
    s.init()
    tools = s.tools()["tools"]
    assert [t["name"] for t in tools] == ["search", "get"]
    assert tools[0]["description"] == "Search the catalog by keyword"
    assert "All dates are UTC." in tools[1]["description"]
    s.close()


def test_unchanged_lines_pass_byte_for_byte(sessions):
    s = sessions()
    line = b'{"id": 5,   "jsonrpc": "2.0", "method": "ping"}\n'
    assert s.raw(line) == b'{"jsonrpc": "2.0", "id": 5, "result": {}}\n'
    s.close()


def test_batch_requests(sessions):
    s = sessions()
    s.init()
    batch = [{"jsonrpc": "2.0", "id": "a", "method": "tools/list"},
             {"jsonrpc": "2.0", "id": "b", "method": "tools/call",
              "params": {"name": "server_instructions"}}]
    s.proc.stdin.write((json.dumps(batch) + "\n").encode())
    s.proc.stdin.flush()
    got = [json.loads(s.proc.stdout.readline()) for _ in range(2)]
    by_id = {}
    for g in got:
        for m in g if isinstance(g, list) else [g]:
            by_id[m["id"]] = m
    assert by_id["b"]["result"]["content"][0]["text"].startswith("Call search")
    assert by_id["a"]["result"]["tools"][-1]["name"] == "server_instructions"
    s.close()


def run_cli(*args):
    return subprocess.run([sys.executable, "-m", "briefed", *args], capture_output=True,
                          text=True, timeout=60)


def test_show():
    res = run_cli("show", "--", sys.executable, SERVER)
    assert res.returncode == 0, res.stderr
    assert "catalog sends 42 characters of instructions" in res.stdout
    assert "Call search before get." in res.stdout
    res = run_cli("show", "--json", "--", sys.executable, SERVER)
    assert json.loads(res.stdout)["characters"] == 42


def test_show_without_instructions():
    res = run_cli("show", "--", sys.executable, SERVER, "--none")
    assert res.returncode == 1
    assert "sends no instructions" in res.stdout


def test_show_server_that_never_answers():
    res = run_cli("show", "--timeout", "1", "--", sys.executable, "-c", "import time; time.sleep(5)")
    assert res.returncode == 2
    assert "did not answer" in res.stderr


def test_cli_errors():
    assert run_cli("proxy").returncode == 2
    res = run_cli("proxy", "--instructions", "@/no/such/file", "--", "true")
    assert res.returncode == 2 and "can't read" in res.stderr
    res = run_cli("proxy", "--", "/no/such/program")
    assert res.returncode == 127
