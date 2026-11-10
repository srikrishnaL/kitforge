import json

from schemashim.cli import main

BAD = {"tools": [{"name": "fetch", "description": "Fetch a URL", "inputSchema": {
    "type": "object", "properties": {"url": {"type": "string", "format": "uri"}},
    "required": ["url"]}}]}


def write(tmp_path, data):
    p = tmp_path / "in.json"
    p.write_text(json.dumps(data))
    return str(p)


def test_check_reports_and_fails(tmp_path, capsys):
    assert main(["check", write(tmp_path, BAD)]) == 1
    out = capsys.readouterr().out
    assert "[gemini] fetch: url: moved format" in out
    assert "[openai-strict] fetch: (root): strict mode needs additionalProperties" in out


def test_check_ok(tmp_path, capsys):
    clean = {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"],
             "additionalProperties": False}
    assert main(["check", write(tmp_path, clean), "--target", "openai-strict"]) == 0
    assert "OK" in capsys.readouterr().out


def test_check_json(tmp_path, capsys):
    assert main(["check", write(tmp_path, BAD), "--target", "gemini", "--json"]) == 1
    findings = json.loads(capsys.readouterr().out)
    assert findings[0]["tool"] == "fetch" and findings[0]["target"] == "gemini"


def test_fix_tools_and_schema(tmp_path, capsys):
    assert main(["fix", write(tmp_path, BAD), "--target", "gemini"]) == 0
    decls = json.loads(capsys.readouterr().out)
    assert decls[0]["name"] == "fetch" and "format" not in decls[0]["parameters"]["properties"]["url"]
    schema = BAD["tools"][0]["inputSchema"]
    assert main(["fix", write(tmp_path, schema), "--target", "openai-strict"]) == 0
    assert json.loads(capsys.readouterr().out)["additionalProperties"] is False


def test_jsonrpc_result_and_single_tool(tmp_path, capsys):
    rpc = {"jsonrpc": "2.0", "id": 1, "result": BAD}
    assert main(["check", write(tmp_path, rpc), "--target", "gemini"]) == 1
    assert main(["check", write(tmp_path, BAD["tools"][0]), "--target", "gemini"]) == 1


def test_bad_input(tmp_path, capsys):
    p = tmp_path / "bad.json"
    p.write_text("{nope")
    try:
        main(["check", str(p)])
    except SystemExit as exc:
        assert "not valid JSON" in str(exc)
    else:
        raise AssertionError("expected SystemExit")
    assert main(["check", write(tmp_path, ["not a tool"])]) == 2


def test_mcptest_snapshot_format(tmp_path, capsys):
    snap = {"format": 1, "tools": {"fetch": {k: v for k, v in BAD["tools"][0].items()
                                             if k != "name"}}}
    assert main(["check", write(tmp_path, snap), "--target", "gemini"]) == 1
    assert "fetch: url" in capsys.readouterr().out
