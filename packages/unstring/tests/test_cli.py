import json
import subprocess
import sys

SCHEMA = json.dumps({"type": "object", "properties": {"n": {"type": "integer"}}})


def run(*args, stdin=None):
    return subprocess.run([sys.executable, "-m", "unstring", *args], capture_output=True,
                          text=True, input=stdin)


def test_fix_reads_stdin_and_exits_0_when_nothing_changed():
    res = run("fix", "--schema", SCHEMA, stdin='{"n": 1}')
    assert res.returncode == 0 and json.loads(res.stdout) == {"n": 1} and res.stderr == ""


def test_fix_accepts_a_tool_object_and_a_tools_list_response():
    tool = {"name": "t", "inputSchema": json.loads(SCHEMA)}
    assert json.loads(run("fix", "--schema", json.dumps(tool), '{"n": "2"}').stdout) == {"n": 2}
    response = {"jsonrpc": "2.0", "id": 1, "result": {"tools": [tool]}}
    res = run("fix", "--schema", json.dumps(response), "--tool", "t", '{"n": "3"}')
    assert res.returncode == 2 and json.loads(res.stdout) == {"n": 3}


def test_fix_errors():
    tools = json.dumps([{"name": "a", "inputSchema": {}}])
    assert "--tool must name one of: a" in run("fix", "--schema", tools, "{}").stderr
    assert "no tool 'b'" in run("fix", "--schema", tools, "--tool", "b", "{}").stderr
    assert "ARGS is not JSON" in run("fix", "--schema", SCHEMA, "{nope").stderr
    assert "can't read" in run("fix", "--schema", "/no/such/file.json", "{}").stderr
    assert run("fix", "--schema", SCHEMA, "{nope").returncode == 2


def test_version():
    from unstring import __version__
    assert run("--version").stdout.strip() == f"unstring {__version__}"
