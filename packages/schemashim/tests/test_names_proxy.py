"""Tool names: --max-name-length in the proxy, and max_length in the library."""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from schemashim import adapt_tools, tool_name
from schemashim.cli import main
from schemashim.proxy import SchemaProxy

SERVER = str(Path(__file__).parent / "servers" / "echo_server.py")
LONG = "get_cloudformation_pre_deploy_validation_instructions"
DOTTED = "billing.cost_management.get_cost_and_usage_with_resources"
PORTABLE = re.compile(r"^[A-Za-z0-9_-]+$")


def req(id_, method, params=None):
    return {"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}}


def talk(options, *messages):
    proc = subprocess.Popen(
        [sys.executable, "-m", "schemashim", "proxy", *options, "--",
         sys.executable, SERVER, "--long-names"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    replies = []
    try:
        for m in messages:  # wait for each reply, as a client waits for tools/list
            proc.stdin.write((json.dumps(m) + "\n").encode())
            proc.stdin.flush()
            replies.append(json.loads(proc.stdout.readline()))
        out, err = proc.communicate(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
    assert proc.returncode == 0, err.decode()
    return {r["id"]: r for r in replies}, err.decode()


def test_end_to_end_names_only():
    by_id, log = talk(["--max-name-length", "30"], req(1, "tools/list"))
    tools = by_id[1]["result"]["tools"]
    names = [t["name"] for t in tools]
    assert names[:2] == ["search", "ping"]  # short names are left alone
    for name in names[2:]:
        assert len(name) <= 30 and PORTABLE.match(name), name
    assert names[2].startswith("get_cloudformation_p")
    assert tools[0]["inputSchema"]["$defs"]  # no --target: schemas are not touched
    assert "renamed to" in log and "names of up to 30 characters" in log

    by_id, _ = talk(["--max-name-length", "30"], req(1, "tools/list"),
                    req(2, "tools/call", {"name": names[3], "arguments": {"days": 7}}),
                    req(3, "tools/call", {"name": "ping", "arguments": {}}))
    sent = json.loads(by_id[2]["result"]["content"][0]["text"])
    assert sent == {"name": DOTTED, "arguments": {"days": 7}}
    assert json.loads(by_id[3]["result"]["content"][0]["text"])["name"] == "ping"


def test_end_to_end_with_target():
    by_id, log = talk(["--target", "openai-strict"], req(1, "tools/list"),
                      req(2, "tools/call", {"name": DOTTED.replace(".", "_"),
                                            "arguments": {"days": 3}}))
    names = [t["name"] for t in by_id[1]["result"]["tools"]]
    assert names == ["search", "ping", LONG, DOTTED.replace(".", "_")]  # all under 64
    sent = json.loads(by_id[2]["result"]["content"][0]["text"])
    assert sent["name"] == DOTTED and sent["arguments"] == {"days": 3}
    assert "names of up to" not in log


def listing(proxy, id_, tools, cursor=None):
    proxy.to_server(req(id_, "tools/list", {"cursor": cursor} if cursor else {}))
    out, _ = proxy.to_client({"jsonrpc": "2.0", "id": id_, "result": {"tools": tools}})
    return [t["name"] for t in out["result"]["tools"]]


def tool(name):
    return {"name": name, "inputSchema": {"type": "object"}}


def test_a_shortened_name_never_takes_an_existing_one():
    proxy = SchemaProxy(max_name_length=64, quiet=True)
    first, second = listing(proxy, 1, [tool("a.b"), tool("a_b")])
    assert second == "a_b" and first != "a_b" and PORTABLE.match(first)
    sent, _ = proxy.to_server(req(2, "tools/call", {"name": first, "arguments": {}}))
    assert sent["params"]["name"] == "a.b"
    sent, _ = proxy.to_server(req(3, "tools/call", {"name": "a_b", "arguments": {}}))
    assert sent["params"]["name"] == "a_b"


def test_names_are_stable_across_lists_and_pages():
    proxy = SchemaProxy(max_name_length=20, quiet=True)
    once = listing(proxy, 1, [tool(LONG), tool(LONG + "_v2")])
    again = listing(proxy, 2, [tool(LONG), tool(LONG + "_v2")])  # e.g. after list_changed
    assert once == again and len(set(once)) == 2
    assert all(len(n) <= 20 for n in once)
    page2 = listing(proxy, 3, [tool(DOTTED)], cursor="next")
    sent, _ = proxy.to_server(req(4, "tools/call", {"name": once[0], "arguments": {}}))
    assert sent["params"]["name"] == LONG  # page 1's names still work after page 2
    sent, _ = proxy.to_server(req(5, "tools/call", {"name": page2[0], "arguments": {}}))
    assert sent["params"]["name"] == DOTTED


def test_unknown_names_and_untouched_lists_pass_through():
    proxy = SchemaProxy(max_name_length=40, quiet=True)
    proxy.to_server(req(1, "tools/list"))
    msg = {"jsonrpc": "2.0", "id": 1, "result": {"tools": [tool("ping")]}}
    assert proxy.to_client(msg) == (msg, [])
    call = req(2, "tools/call", {"name": "nope", "arguments": {}})
    assert proxy.to_server(call) == (call, [])


@pytest.mark.parametrize("argv, message", [
    (["proxy", "--", "x"], "needs --target, --max-name-length, or both"),
    (["proxy", "--max-name-length", "8", "--", "x"], "must be from 12 to 64"),
    (["proxy", "--max-name-length", "65", "--", "x"], "must be from 12 to 64"),
])
def test_cli_errors(argv, message, capsys):
    with pytest.raises(SystemExit):
        main(argv)
    assert message in capsys.readouterr().err


def test_proxy_needs_an_option():
    with pytest.raises(ValueError):
        SchemaProxy()
    with pytest.raises(ValueError):
        SchemaProxy("gemini", max_name_length=200)


def test_library_max_length():
    short = tool_name(LONG, "openai-strict", max_length=24)
    assert len(short) == 24 and short.startswith("get_cloudform")
    assert tool_name(LONG, "openai-strict", 24) == short  # same input, same name
    assert tool_name("ping", "gemini", 12) == "ping"
    assert tool_name("a.b", None) == "a_b"
    with pytest.raises(ValueError):
        tool_name("x", "gemini", max_length=11)
    with pytest.raises(ValueError):
        tool_name("x", "gemini", max_length=True)
    kit = adapt_tools([tool(LONG), tool(LONG + "x")], "gemini", max_name_length=30)
    names = [t.name for t in kit]
    assert len(set(names)) == 2 and all(len(n) <= 30 for n in names)
    assert kit.restore(names[1], {})[0] == LONG + "x"


def test_readme_name_example():
    assert tool_name(LONG, None, 40) == "get_cloudformation_pre_deploy_v_63c496f4"
