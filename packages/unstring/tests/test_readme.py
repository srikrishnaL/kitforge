"""The README's examples, as written."""

import json
import subprocess
import sys

from unstring import repair


def test_readme_example():
    schema = {
        "type": "object",
        "properties": {
            "pages": {"type": "array", "items": {"type": "object", "properties": {
                "title": {"type": "string"}, "position": {"type": "integer"}}}},
            "query": {"type": "string"},
            "limit": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
        },
    }
    result = repair({"pages": '[{"title": "A", "position": "2"}]', "query": "[1, 2]",
                     "limit": "10"}, schema)
    assert result.value == {"pages": [{"title": "A", "position": 2}], "query": "[1, 2]",
                            "limit": 10}
    assert [str(f) for f in result.fixes] == [
        "pages: string -> array", "pages[0].position: string -> integer",
        "limit: string -> integer"]


def test_readme_fix_command(tmp_path):
    tools = {"tools": [{"name": "create_pages", "inputSchema": {"type": "object", "properties": {
        "pages": {"type": "array", "items": {"type": "object"}}}}}]}
    path = tmp_path / "tools.json"
    path.write_text(json.dumps(tools))
    res = subprocess.run([sys.executable, "-m", "unstring", "fix", "--schema", str(path),
                          "--tool", "create_pages", '{"pages": "[{\\"title\\": \\"A\\"}]"}'],
                         capture_output=True, text=True)
    assert res.returncode == 2
    assert res.stderr == "repaired pages: string -> array\n"
    assert json.loads(res.stdout) == {"pages": [{"title": "A"}]}


def test_readme_mentions_every_http_option():
    from pathlib import Path

    readme = (Path(__file__).parent.parent / "README.md").read_text()
    res = subprocess.run([sys.executable, "-m", "unstring", "http", "--help"],
                         capture_output=True, text=True)
    for flag in ("--host", "--port", "--timeout", "--wrap", "--quiet", "--log"):
        assert flag in res.stdout
        assert f"`{flag}`" in readme
