import json
import subprocess
import sys
from pathlib import Path

import pytest

SERVER = str(Path(__file__).parent / "servers" / "strict_server.py")
STRINGIFIED = {"pages": json.dumps([{"title": "A", "tags": '["x", "y"]', "position": "2"}])}


class Session:
    """The proxy around the strict server, driven one request at a time."""

    def __init__(self, *flags, server_args=(), proxy=True):
        cmd = [sys.executable, SERVER, *server_args]
        if proxy:
            cmd = [sys.executable, "-m", "unstring", "proxy", *flags, "--", *cmd]
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE)
        self.next_id = 0

    def write(self, msg):
        self.proc.stdin.write((json.dumps(msg) + "\n").encode())
        self.proc.stdin.flush()

    def read(self):
        return json.loads(self.proc.stdout.readline())

    def send(self, method, params=None):
        self.next_id += 1
        self.write({"jsonrpc": "2.0", "id": self.next_id, "method": method,
                    "params": params or {}})
        reply = self.read()
        assert reply["id"] == self.next_id
        return reply["result"]

    def init(self):
        self.send("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                 "clientInfo": {"name": "t", "version": "0"}})
        self.write({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def call(self, name, arguments):
        result = self.send("tools/call", {"name": name, "arguments": arguments})
        text = result["content"][0]["text"]
        return text if result.get("isError") else json.loads(text)

    def close(self):
        self.proc.stdin.close()
        code = self.proc.wait(timeout=30)
        log = self.proc.stderr.read().decode()
        assert code == 0, log
        return log


@pytest.fixture
def session():
    made = []

    def make(*flags, **kw):
        s = Session(*flags, **kw)
        made.append(s)
        s.init()
        return s

    yield make
    for s in made:
        if s.proc.poll() is None:
            s.proc.kill()
            s.proc.wait()


def test_without_the_proxy_the_server_rejects_stringified_arguments(session):
    s = session(proxy=False)
    assert s.call("create_pages", STRINGIFIED) == \
        "Invalid arguments: args.pages: expected array, received string"
    s.close()


def test_proxy_repairs_nested_strings_after_the_client_lists_tools(session):
    s = session()
    s.send("tools/list")
    s.send("tools/list", {"cursor": "p2"})
    got = s.call("create_pages", STRINGIFIED)
    assert got["args"] == {"pages": [{"title": "A", "tags": ["x", "y"], "position": 2}]}
    assert s.call("tag", {"ids": "[1, 2]"})["args"] == {"ids": [1, 2]}
    log = s.close()
    assert "unstring: create_pages: pages: string -> array; pages[0].tags: string -> array; " \
           "pages[0].position: string -> integer" in log
    assert "unstring: tag: ids: string -> array" in log


def test_real_strings_are_left_alone(session):
    s = session()
    s.send("tools/list")
    args = {"query": '["not", "a", "list"]', "limit": 5}
    assert s.call("search", args)["args"] == args
    assert "unstring:" not in s.close()


def test_unknown_schema_makes_the_proxy_list_tools_itself(session):
    # The client never asks for tools/list (it cached them), and the tool is on page 2.
    s = session(server_args=["--count"])
    got = s.call("tag", {"ids": "[3]"})
    assert got == {"args": {"ids": [3]}, "lists": 2}
    # Once learned, no more lists.
    assert s.call("search", {"limit": "10", "exact": "true"}) == \
        {"args": {"limit": 10, "exact": True}, "lists": 2}
    s.close()


def test_whole_arguments_object_sent_as_a_string(session):
    s = session()
    s.send("tools/list")
    got = s.call("search", json.dumps({"query": "q", "limit": "3"}))
    assert got["args"] == {"query": "q", "limit": 3}
    s.close()


def test_server_that_cannot_list_tools_still_gets_the_call(session):
    s = session(server_args=["--no-list"])
    assert s.call("search", {"query": "q"})["args"] == {"query": "q"}
    assert "did not list its tools" in s.close()


def test_batch_with_a_held_call(session):
    s = session()
    s.write([{"jsonrpc": "2.0", "id": 7, "method": "tools/call",
              "params": {"name": "tag", "arguments": {"ids": "[9]"}}},
             {"jsonrpc": "2.0", "id": 8, "method": "ping"}])
    replies = {}
    for _ in range(2):
        msg = s.read()
        for m in msg if isinstance(msg, list) else [msg]:
            replies[m["id"]] = m
    assert json.loads(replies[7]["result"]["content"][0]["text"])["args"] == {"ids": [9]}
    assert replies[8]["result"] == {}
    s.close()


def test_quiet_and_log_file(session, tmp_path):
    log = tmp_path / "fixes.log"
    s = session("--quiet")
    s.send("tools/list")
    s.call("search", {"limit": "1"})
    assert "unstring:" not in s.close()
    s = session("--log", str(log))
    s.send("tools/list")
    s.call("search", {"limit": "1"})
    assert "unstring:" not in s.close()
    assert log.read_text() == "unstring: search: limit: string -> integer\n"


def test_wrap_flag(session):
    s = session("--wrap")
    s.send("tools/list")
    s.send("tools/list", {"cursor": "p2"})
    assert s.call("tag", {"ids": 4})["args"] == {"ids": [4]}
    s.close()


def test_proxy_needs_a_command():
    res = subprocess.run([sys.executable, "-m", "unstring", "proxy"], capture_output=True,
                         text=True)
    assert res.returncode == 2 and "needs the server command" in res.stderr


def test_missing_server_command():
    res = subprocess.run([sys.executable, "-m", "unstring", "proxy", "--",
                          "no-such-command-unstring"], capture_output=True, text=True,
                         stdin=subprocess.DEVNULL)
    assert res.returncode == 127 and "can't start" in res.stderr
