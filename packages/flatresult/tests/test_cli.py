import json
import subprocess
import sys


def run(*args, stdin=None):
    return subprocess.run([sys.executable, "-m", "flatresult", *args], input=stdin,
                          capture_output=True, text=True)


def test_check_rewrites_a_jsonrpc_response_from_stdin():
    res = run("check", stdin=json.dumps({"jsonrpc": "2.0", "id": 1, "result": {
        "content": [], "structuredContent": {"ok": True}}}))
    assert res.returncode == 2
    assert res.stderr == "rewrote structuredContent: structuredContent -> text block (12 chars)\n"
    assert json.loads(json.loads(res.stdout)["content"][0]["text"]) == {"ok": True}


def test_check_exit_zero_when_nothing_to_do(tmp_path):
    path = tmp_path / "r.json"
    path.write_text(json.dumps({"content": [{"type": "text", "text": "hi"}]}))
    res = run("check", str(path))
    assert res.returncode == 0 and res.stderr == ""


def test_errors_are_clear():
    assert "not JSON" in run("check", stdin="nope").stderr
    assert "can't read" in run("check", "/no/such/file").stderr
    assert "needs the server command after --" in run("proxy").stderr
    assert "--max-links must be 0 or more" in run("proxy", "--max-links", "-1", "--", "x").stderr


def test_a_missing_server_command_reports_127():
    res = run("proxy", "--", "flatresult-no-such-command-xyz", stdin="")
    assert res.returncode == 127 and "can't start" in res.stderr


def test_version():
    assert run("--version").stdout.startswith("flatresult 0.1.0")
