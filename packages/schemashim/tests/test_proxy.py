import json
import subprocess
import sys
from pathlib import Path

import pytest

from schemashim.cli import main
from schemashim.proxy import SchemaProxy

SERVER = str(Path(__file__).parent / "servers" / "echo_server.py")


def talk(target, *messages):
    """Run the proxy around the echo server and send each message, waiting for its reply.

    Returns the replies and what the proxy logged to stderr.
    """
    proc = subprocess.Popen(
        [sys.executable, "-m", "schemashim", "proxy", "--target", target, "--",
         sys.executable, SERVER],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    replies = []
    try:
        for m in messages:
            proc.stdin.write((json.dumps(m) + "\n").encode())
            proc.stdin.flush()
            if isinstance(m, dict) and "id" in m:
                replies.append(json.loads(proc.stdout.readline()))
        proc.stdin.close()
        rest = proc.stdout.read().decode()
        replies += [json.loads(x) for x in rest.splitlines()]
        log = proc.stderr.read().decode()
        assert proc.wait(timeout=30) == 0, log
    finally:
        if proc.poll() is None:
            proc.kill()
    return replies, log


def req(id_, method, params=None):
    return {"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}}


def test_gemini_end_to_end():
    replies, log = talk(
        "gemini",
        req(1, "initialize"),
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        req(2, "tools/list"),
        req(3, "tools/call", {"name": "search", "arguments": {
            "query": "crash", "limit": 5.0, "labels": '["bug", "p1"]',
            "range": {"from": 1, "to": 2}}}),
    )
    by_id = {r["id"]: r for r in replies}
    assert by_id[1]["result"]["serverInfo"]["name"] == "echo"
    search, ping = by_id[2]["result"]["tools"]
    schema = search["inputSchema"]
    assert "$defs" not in schema
    assert schema["properties"]["limit"] == {"type": "integer", "nullable": True}
    assert schema["properties"]["labels"]["type"] == "string"
    assert schema["properties"]["range"]["type"] == "object"
    assert ping["inputSchema"] == {"type": "object", "properties": {}}  # accepted as is
    sent = json.loads(by_id[3]["result"]["content"][0]["text"])
    assert sent["arguments"] == {"query": "crash", "limit": 5, "labels": ["bug", "p1"],
                                 "range": {"from": 1, "to": 2}}
    assert "adapted 1 of 2 tools for gemini" in log


def test_openai_strict_drops_forced_nulls():
    replies, _ = talk(
        "openai-strict",
        req("a", "tools/list"),
        req("b", "tools/call", {"name": "search", "arguments": {
            "query": "x", "limit": None, "labels": None, "range": None}}),
    )
    by_id = {r["id"]: r for r in replies}
    schema = by_id["a"]["result"]["tools"][0]["inputSchema"]
    assert schema["additionalProperties"] is False
    assert sorted(schema["required"]) == ["labels", "limit", "query", "range"]
    sent = json.loads(by_id["b"]["result"]["content"][0]["text"])
    assert sent["arguments"] == {"query": "x", "limit": None}  # limit was nullable already


def test_errors_and_unknown_tools_pass_through():
    replies, _ = talk("gemini", req(1, "nope"),
                      req(2, "tools/call", {"name": "other", "arguments": {"labels": "[1]"}}))
    by_id = {r["id"]: r for r in replies}
    assert by_id[1]["error"]["code"] == -32601
    assert json.loads(by_id[2]["result"]["content"][0]["text"])["arguments"] == {"labels": "[1]"}


def test_ids_are_matched_exactly():
    proxy = SchemaProxy("gemini", quiet=True)
    proxy.to_server(req(1, "tools/list"))
    tools = {"tools": [{"name": "t", "inputSchema": {"type": "object",
                                                     "properties": {"x": {"type": "array"}}}}]}
    other, _ = proxy.to_client({"jsonrpc": "2.0", "id": "1", "result": tools})
    assert other["result"]["tools"][0]["inputSchema"]["properties"]["x"] == {"type": "array"}
    mine, _ = proxy.to_client({"jsonrpc": "2.0", "id": 1, "result": tools})
    assert mine["result"]["tools"][0]["inputSchema"]["properties"]["x"]["type"] == "string"


def test_whole_floats_become_integers_even_when_nothing_else_changes():
    proxy = SchemaProxy("gemini", quiet=True)
    proxy.to_server(req(1, "tools/list"))
    proxy.to_client({"jsonrpc": "2.0", "id": 1, "result": {"tools": [
        {"name": "fetch", "inputSchema": {"type": "object",
                                          "properties": {"max_length": {"type": "integer"}}}}]}})
    sent, _ = proxy.to_server(req(2, "tools/call", {"name": "fetch",
                                                     "arguments": {"max_length": 500.0}}))
    assert json.dumps(sent["params"]["arguments"]) == '{"max_length": 500}'


def test_server_requests_are_not_touched():
    proxy = SchemaProxy("gemini", quiet=True)
    proxy.to_server(req(7, "tools/list"))
    msg = {"jsonrpc": "2.0", "id": 7, "method": "sampling/createMessage", "params": {}}
    assert proxy.to_client(msg) == (msg, [])


def test_batches_are_rewritten_item_by_item():
    import io
    from schemashim._relay import Relay

    proxy = SchemaProxy("gemini", quiet=True)
    proxy.shims["t"] = __import__("schemashim").adapt(
        {"type": "object", "properties": {"x": {"type": "array"}}}, "gemini")
    relay = Relay(["unused"], to_server=proxy.to_server, to_client=proxy.to_client)
    forward, back = io.BytesIO(), io.BytesIO()
    batch = [req(1, "tools/call", {"name": "t", "arguments": {"x": "[1]"}}), req(2, "ping")]
    relay._handle(json.dumps(batch).encode(), proxy.to_server, forward, back)
    sent = json.loads(forward.getvalue())
    assert sent[0]["params"]["arguments"] == {"x": [1]} and sent[1] == batch[1]
    # a line nothing changes is passed on byte for byte
    forward = io.BytesIO()
    line = b'{"jsonrpc":"2.0",  "id":3, "method":"ping"}\n'
    relay._handle(line, proxy.to_server, forward, back)
    assert forward.getvalue() == line and back.getvalue() == b""


def test_proxy_needs_a_command(capsys):
    with pytest.raises(SystemExit):
        main(["proxy", "--target", "gemini"])
    assert "needs the server command" in capsys.readouterr().err


def test_missing_server_command():
    proc = subprocess.run([sys.executable, "-m", "schemashim", "proxy", "--target", "gemini",
                           "--", "no-such-command-xyz"], input=b"", capture_output=True,
                          timeout=30)
    assert proc.returncode == 127
    assert b"can't start" in proc.stderr
