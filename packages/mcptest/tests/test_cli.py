import json
import sys

from conftest import FAKE

from mcptest import snapshot as snap
from mcptest.cli import main

CMD = ["--", sys.executable, FAKE]


def test_lint_clean(capsys):
    assert main(["lint", *CMD]) == 0
    out = capsys.readouterr().out
    assert "fake: 2 tools" in out and "0 errors" in out


def test_lint_bad_fails(capsys, monkeypatch):
    monkeypatch.setenv("FAKE_TOOLS", "bad")
    assert main(["lint", *CMD]) == 1
    out = capsys.readouterr().out
    assert "MCP012" in out
    assert main(["lint", "--ignore", "MCP001,MCP003,MCP008,MCP012", *CMD]) == 0
    capsys.readouterr()
    assert main(["lint", "--format", "json", *CMD]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["tools"] == 4 and data["findings"]


def test_lint_rules(capsys):
    assert main(["lint", "--rules"]) == 0
    assert "MCP012" in capsys.readouterr().out


def test_tools_and_call(capsys):
    assert main(["tools", *CMD]) == 0
    assert [t["name"] for t in json.loads(capsys.readouterr().out)] == ["add", "echo"]
    assert main(["call", "add", '{"a": 4, "b": 5}', *CMD]) == 0
    assert json.loads(capsys.readouterr().out)["structuredContent"] == {"result": 9}
    assert main(["call", "nope", *CMD]) == 1


def test_snapshot_and_diff(tmp_path, capsys, monkeypatch):
    old = tmp_path / "old.json"
    assert main(["snapshot", "-o", str(old), *CMD]) == 0
    assert list(snap.load(old)["tools"]) == ["add", "echo"]
    assert main(["diff", str(old), *CMD]) == 0
    assert "no changes" in capsys.readouterr().out
    monkeypatch.setenv("FAKE_TOOLS", "v2")
    assert main(["diff", str(old), *CMD]) == 1
    out = capsys.readouterr().out
    assert "BREAKING echo: tool was removed" in out
    assert main(["diff", "--allow-breaking", str(old), *CMD]) == 0
    new = tmp_path / "new.json"
    main(["snapshot", "-o", str(new), *CMD])
    capsys.readouterr()
    assert main(["diff", "--format", "json", str(old), str(new)]) == 1
    assert any(c["kind"] == "breaking" for c in json.loads(capsys.readouterr().out))


def test_server_failure_exit_code(capsys, monkeypatch):
    monkeypatch.setenv("FAKE_CRASH", "1")
    assert main(["lint", *CMD]) == 2
    assert "database not configured" in capsys.readouterr().err


def test_http_lint(http_server, capsys):
    url, _ = http_server
    assert main(["lint", "--url", url, "--header", "Authorization: Bearer x"]) == 0
    assert "fake-http: 2 tools" in capsys.readouterr().out
