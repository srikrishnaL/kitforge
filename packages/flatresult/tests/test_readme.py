"""The README's examples, as written."""

import json
import subprocess
import sys

from flatresult import flatten


def test_readme_example():
    result = {"content": [], "structuredContent": {"temp": 21.5, "unit": "C"}}
    flat = flatten(result)
    assert flat.result["content"] == [{"type": "text", "text": '{"temp": 21.5, "unit": "C"}'}]
    assert [str(c) for c in flat.changes] == [
        "structuredContent: structuredContent -> text block (27 chars)"]


def test_readme_check_command():
    res = subprocess.run([sys.executable, "-m", "flatresult", "check"], input=json.dumps(
        {"content": [{"type": "resource_link", "uri": "file:///r.txt", "name": "report"}]}),
        capture_output=True, text=True)
    assert res.returncode == 2
    assert res.stderr == "rewrote content[0]: resource_link -> text line\n"
    assert json.loads(res.stdout)["content"] == [
        {"type": "text", "text": "Resource: report <file:///r.txt>"}]


def test_readme_binary_note():
    flat = flatten({"content": [{"type": "resource", "resource": {
        "uri": "file:///r.pdf", "mimeType": "application/pdf", "blob": "A" * 69484}}]})
    assert flat.result["content"][0]["text"] == \
        "[binary resource file:///r.pdf: application/pdf, 52,113 bytes, not shown]"
