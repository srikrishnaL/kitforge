import copy

import fake_server

from mcptest import diff, normalize
from mcptest import snapshot as snap


def test_normalize_is_stable():
    a = normalize(fake_server.GOOD)
    b = normalize(list(reversed(fake_server.GOOD)))
    assert snap.dumps(a) == snap.dumps(b)
    assert list(a["tools"]) == ["add", "echo"]
    assert a["format"] == 1


def test_save_load_roundtrip(tmp_path):
    p = tmp_path / "s" / "tools.json"
    snap.save(p, fake_server.GOOD)
    assert snap.load(p) == normalize(fake_server.GOOD)
    # A raw tools/list result is accepted too.
    assert snap._as_snapshot({"tools": fake_server.GOOD}) == normalize(fake_server.GOOD)


def test_no_changes():
    assert diff(fake_server.GOOD, copy.deepcopy(fake_server.GOOD)) == []


def test_v1_to_v2():
    changes = diff(fake_server.GOOD, fake_server.V2)
    text = [str(c) for c in changes]
    breaking = [c for c in changes if c.breaking]
    assert {c.message for c in breaking} == {
        "tool was removed",
        "new required parameter 'c'",
        "outputSchema was removed",
    }
    assert any("type changed integer -> number" in t and t.startswith("ok") for t in text)
    assert any(c.tool == "upper" and c.message == "tool was added" for c in changes)
    # Breaking changes sort first.
    assert changes[0].breaking


def _tool(props, required=(), **extra):
    schema = {"type": "object", "properties": props, "required": list(required)}
    schema.update(extra)
    return [{"name": "t", "description": "d", "inputSchema": schema}]


def test_param_rules():
    old = _tool({"x": {"type": "string", "enum": ["a", "b"]}, "y": {"type": "string"}}, ["x"])
    new = _tool({"x": {"type": "string", "enum": ["a", "c"]}, "y": {"type": "string"}}, ["x", "y"])
    msgs = {c.message: c.kind for c in diff(old, new)}
    assert msgs["parameter 'x' no longer accepts ['b']"] == "breaking"
    assert msgs["parameter 'x' now also accepts ['c']"] == "compatible"
    assert msgs["parameter 'y' is now required"] == "breaking"


def test_type_narrowing_and_bounds():
    old = _tool({"n": {"type": "number", "maximum": 10}})
    new = _tool({"n": {"type": "integer", "maximum": 5}})
    msgs = {c.message: c.kind for c in diff(old, new)}
    assert msgs["parameter 'n' type changed number -> integer"] == "breaking"
    assert msgs["parameter 'n' maximum tightened to 5"] == "breaking"
    back = {c.message: c.kind for c in diff(new, old)}
    assert back["parameter 'n' maximum relaxed to 10"] == "compatible"


def test_removed_param_and_closed_schema():
    old = _tool({"a": {"type": "string"}, "b": {"type": "string"}})
    new = _tool({"a": {"type": "string"}}, additionalProperties=False)
    msgs = {c.message: c.kind for c in diff(old, new)}
    assert msgs["parameter 'b' was removed"] == "breaking"
    assert msgs["extra parameters are no longer accepted"] == "breaking"


def test_output_fields():
    def with_out(props):
        t = _tool({})
        t[0]["outputSchema"] = {"type": "object", "properties": props}
        return t
    msgs = {c.message: c.kind for c in diff(
        with_out({"a": {"type": "string"}, "b": {"type": "string"}}),
        with_out({"a": {"type": "integer"}, "c": {"type": "string"}}),
    )}
    assert msgs["output field 'b' was removed"] == "breaking"
    assert msgs["output field 'a' changed type"] == "breaking"
    assert msgs["new output field 'c'"] == "compatible"
