import io
import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from flatresult.proxy import READ_ID_PREFIX, ResultProxy

SERVER = str(Path(__file__).parent / "servers" / "rich_server.py")


class Session:
    """The proxy around the rich server, driven one request at a time."""

    def __init__(self, *flags, server_args=(), proxy=True):
        cmd = [sys.executable, SERVER, *server_args]
        if proxy:
            cmd = [sys.executable, "-m", "flatresult", "proxy", *flags, "--", *cmd]
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE)
        self.next_id = 0

    def send(self, method, params=None):
        self.next_id += 1
        self.proc.stdin.write((json.dumps({"jsonrpc": "2.0", "id": self.next_id,
                                           "method": method, "params": params or {}}) + "\n")
                              .encode())
        self.proc.stdin.flush()
        reply = json.loads(self.proc.stdout.readline())
        assert reply["id"] == self.next_id
        return reply["result"]

    def init(self):
        self.send("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                 "clientInfo": {"name": "t", "version": "0"}})

    def call(self, name):
        return self.send("tools/call", {"name": name, "arguments": {}})

    def close(self):
        self.proc.stdin.close()
        code = self.proc.wait(timeout=30)
        log = self.proc.stderr.read().decode()
        assert code == 0, log
        return log


@pytest.fixture
def session():
    made = []

    def make(*flags, init=True, **kw):
        s = Session(*flags, **kw)
        made.append(s)
        if init:
            s.init()
        return s

    yield make
    for s in made:
        if s.proc.poll() is None:
            s.proc.kill()
            s.proc.wait()


def test_without_the_proxy_results_have_no_text(session):
    s = session(proxy=False)
    assert s.call("structured_only")["content"] == []
    assert {b["type"] for b in s.call("links")["content"]} == {"resource_link"}
    s.close()


def test_structured_content_gets_text(session):
    s = session()
    result = s.call("structured_only")
    assert json.loads(result["content"][0]["text"]) == {"temp": 21.5, "unit": "C"}
    assert result["structuredContent"] == {"temp": 21.5, "unit": "C"}
    assert "structuredContent -> text block" in s.close()


def test_links_are_read_from_the_server_and_put_inline(session):
    s = session()
    content = s.call("links")["content"]
    assert content[0] == {"type": "text", "text":
                          "Resource: frame-1 <file:///design/frame-1.json> (application/json)"
                          '\n\n{"width": 320, "height": 640}'}
    assert content[1]["text"] == "Resource: frame-1.png <file:///design/frame-1.png>"
    assert content[2]["type"] == "image" and content[2]["mimeType"] == "image/png"
    # the proxy's own reads never reach the client: the next reply is the next request's
    assert s.call("plain") == {"content": [{"type": "text", "text": "ok"}]}
    s.close()


def test_no_inline_and_servers_without_resources_give_link_lines(session):
    for s in (session("--no-inline"), session(server_args=["--no-resources"])):
        content = s.call("links")["content"]
        assert [b["type"] for b in content] == ["text", "text"]
        assert content[0]["text"].startswith("Resource: frame-1 <file:///design/frame-1.json>")
        s.close()


def test_read_errors_fall_back_to_link_lines(session):
    s = session(server_args=["--read-error"])
    assert [b["type"] for b in s.call("links")["content"]] == ["text", "text"]
    assert "can't read file:///design/frame-1.json" in s.close()


def test_a_server_that_never_answers_reads_times_out(session):
    s = session("--timeout", "0.3", server_args=["--slow-read"])
    content = s.call("links")["content"]
    assert [b["text"].split(" <")[0] for b in content] == ["Resource: frame-1",
                                                           "Resource: frame-1.png"]
    assert "no answer to resources/read within 0.3s" in s.close()


def test_keep_and_embedded_resources(session):
    s = session("--keep", "--quiet")
    content = s.call("embedded")["content"]
    assert [b["type"] for b in content] == ["text", "resource", "text"]
    assert content[2]["text"] == "file:///notes.md (text/markdown):\n# Notes\nhello"
    assert s.close() == ""


def test_plain_results_and_other_messages_pass_unchanged(session):
    s = session()
    assert s.call("plain") == {"content": [{"type": "text", "text": "ok"}]}
    assert len(s.send("tools/list")["tools"]) == 4
    assert s.close() == ""


# -- the hooks on their own --------------------------------------------------------------

def link_result(*uris):
    return {"content": [{"type": "resource_link", "uri": u, "name": u} for u in uris]}


def test_hooks_read_each_uri_once_and_copy_the_request_envelope():
    p = ResultProxy(quiet=True)
    meta = {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
            "io.modelcontextprotocol/clientCapabilities": {}, "progressToken": 1}
    p.to_server({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                 "params": {"name": "t", "_meta": meta}})
    out, reads = p.to_client({"jsonrpc": "2.0", "id": 7,
                              "result": link_result("file:///a", "file:///a", "file:///b")})
    assert out is None
    assert [r["params"]["uri"] for r in reads] == ["file:///a", "file:///b"]
    assert reads[0]["params"]["_meta"] == {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
                                           "io.modelcontextprotocol/clientCapabilities": {}}
    assert all(r["id"].startswith(READ_ID_PREFIX) for r in reads)
    assert p.to_client({"jsonrpc": "2.0", "id": reads[1]["id"], "result": {"contents": [
        {"uri": "file:///b", "text": "B"}]}}) == (None, [])
    done, more = p.to_client({"jsonrpc": "2.0", "id": reads[0]["id"],
                              "result": {"contents": [{"uri": "file:///a", "text": "A"}]}})
    assert more == [] and done["id"] == 7
    assert [b["text"] for b in done["result"]["content"]] == [
        "Resource: file:///a <file:///a>\n\nA", "Resource: file:///a <file:///a>\n\nA",
        "Resource: file:///b <file:///b>\n\nB"]


def test_hooks_respect_max_links_and_leave_unasked_responses_alone():
    p = ResultProxy(quiet=True, max_links=1)
    p.to_server({"jsonrpc": "2.0", "id": "x", "method": "tools/call", "params": {}})
    _, reads = p.to_client({"jsonrpc": "2.0", "id": "x",
                            "result": link_result("file:///a", "file:///b")})
    assert len(reads) == 1
    other = {"jsonrpc": "2.0", "id": 99, "result": link_result("file:///a")}
    assert p.to_client(other) == (other, [])          # not a tools/call the client sent
    stray = {"jsonrpc": "2.0", "id": READ_ID_PREFIX + "404", "result": {}}
    assert p.to_client(stray) == (None, [])           # our own read, already given up on


def test_numeric_and_string_ids_are_different_calls():
    p = ResultProxy(quiet=True, inline=False)
    p.to_server({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {}})
    msg = {"jsonrpc": "2.0", "id": "1", "result": {"content": [], "structuredContent": {}}}
    assert p.to_client(msg) == (msg, [])


def test_timeout_emits_the_result_once():
    sent = []
    fired = threading.Event()

    def emit(m):
        sent.append(m)
        fired.set()

    p = ResultProxy(log=io.StringIO(), timeout=0.05, emit=emit)
    p.to_server({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {}})
    _, reads = p.to_client({"jsonrpc": "2.0", "id": 3, "result": link_result("file:///a")})
    assert fired.wait(5)
    assert sent[0]["result"]["content"] == [{"type": "text",
                                             "text": "Resource: file:///a <file:///a>"}]
    late = {"jsonrpc": "2.0", "id": reads[0]["id"], "result": {"contents": []}}
    assert p.to_client(late) == (None, [])
    assert len(sent) == 1
