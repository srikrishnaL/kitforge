import sys

from conftest import FAKE

from mcptest import github as gh
from mcptest.cli import main
from mcptest.lint import Finding
from mcptest.snapshot import Change

CMD = ["--", sys.executable, FAKE]


def test_escaping():
    assert gh.escape_data("50% done\r\nnext") == "50%25 done%0D%0Anext"
    assert gh.escape_property("MCP001 a:b,c") == "MCP001 a%3Ab%2Cc"
    line = gh.annotation("error", "t:1", "x\ny")
    assert line == "::error title=t%3A1::x%0Ay"


def test_message_cannot_inject_a_second_command():
    # A tool description is untrusted. A newline in it must not start a new workflow command.
    f = Finding("MCP012", "error", "evil", "says 'x'\n::add-mask::secret")
    (line,) = gh.lint_annotations([f], "srv")
    assert "\n" not in line and line.startswith("::error title=MCP012 evil (srv)::")


def test_levels():
    fs = [Finding("MCP015", "info", "t", "m"), Finding("MCP007", "warning", None, "m")]
    lines = gh.lint_annotations(fs)
    assert lines[0].startswith("::notice ") and lines[1].startswith("::warning title=MCP007 (server)")
    lines = gh.diff_annotations([Change("breaking", "add", "x"), Change("compatible", "add", "y")])
    assert lines[0].startswith("::error ") and lines[1].startswith("::notice ")


def test_summary_tables_escape_pipes():
    md = gh.lint_summary("a|b", 1, [Finding("MCP005", "warning", "t|u", "too\nshort")])
    assert "a\\|b" in md and "| warning | MCP005 | t\\|u | too short |" in md
    assert "No findings." in gh.lint_summary("s", 2, [])
    assert "No changes" in gh.diff_summary([])
    assert "| **breaking** | echo | tool was removed |" in gh.diff_summary(
        [Change("breaking", "echo", "tool was removed")])


def test_write_step_summary(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert gh.write_step_summary("x") is False
    path = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(path))
    assert gh.write_step_summary("a\n") and gh.write_step_summary("b\n")
    assert path.read_text() == "a\nb\n"


def test_lint_format_github(tmp_path, capsys, monkeypatch):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv("FAKE_TOOLS", "bad")
    assert main(["lint", "--format", "github", *CMD]) == 1
    out = capsys.readouterr().out
    assert "::error title=MCP012 get_file (fake)::" in out
    assert "::warning title=MCP005 Get-File (fake)::" in out
    assert "fake: 4 tools" in out
    assert "| error | MCP012 | get_file |" in summary.read_text()


def test_lint_format_github_clean(tmp_path, capsys, monkeypatch):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert main(["lint", "--format", "github", *CMD]) == 0
    assert "::" not in capsys.readouterr().out
    assert "No findings." in summary.read_text()


def test_diff_format_github(tmp_path, capsys, monkeypatch):
    old = tmp_path / "old.json"
    main(["snapshot", "-o", str(old), *CMD])
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert main(["diff", "--format", "github", str(old), *CMD]) == 0
    assert "No changes" in summary.read_text()
    monkeypatch.setenv("FAKE_TOOLS", "v2")
    assert main(["diff", "--format", "github", str(old), *CMD]) == 1
    out = capsys.readouterr().out
    assert "::error title=Breaking MCP change%3A echo::tool was removed" in out
    assert "BREAKING echo" not in out  # annotations replace the plain lines
    assert "| **breaking** | echo | tool was removed |" in summary.read_text()
    assert main(["diff", "--format", "github", "--allow-breaking", str(old), *CMD]) == 0
    assert "::warning title=Breaking MCP change%3A echo::" in capsys.readouterr().out
