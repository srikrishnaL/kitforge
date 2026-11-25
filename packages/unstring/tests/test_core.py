import json

import pytest

from unstring import Fix, repair, repair_arguments


def obj(**props):
    return {"type": "object", "properties": props}


ARR = {"type": "array", "items": {"type": "string"}}
INT = {"type": "integer"}


def fixes(result):
    return [str(f) for f in result.fixes]


def test_nothing_to_do_returns_the_same_object():
    args = {"tags": ["a"], "n": 1}
    result = repair(args, obj(tags=ARR, n=INT))
    assert result.value is args and not result.changed


def test_input_is_not_modified():
    args = {"tags": '["a"]'}
    result = repair(args, obj(tags=ARR))
    assert result.value == {"tags": ["a"]} and args == {"tags": '["a"]'}


@pytest.mark.parametrize("schema", [
    {"type": "string"}, {"type": ["string", "array"]}, {}, True,
    {"anyOf": [{"type": "string"}, ARR]}, {"enum": ["[1]", "x"]},
    {"description": "untyped"},
])
def test_strings_are_kept_where_a_string_is_allowed(schema):
    args = {"v": "[1, 2]"}
    assert repair(args, obj(v=schema)).value is args


@pytest.mark.parametrize("text,schema,want", [
    ("[1, 2]", ARR | {"items": INT}, [1, 2]),
    ('{"a": 1}', {"type": "object"}, {"a": 1}),
    ("5", INT, 5),
    (" 5 ", INT, 5),
    ("5.0", INT, 5),
    ("2.5", {"type": "number"}, 2.5),
    ("7", {"type": "number"}, 7),
    ("true", {"type": "boolean"}, True),
    ("False", {"type": "boolean"}, False),
    ("null", {"type": ["integer", "null"]}, None),
    ('"[1, 2]"', ARR | {"items": INT}, [1, 2]),
    ("['a', 'b']", ARR, ["a", "b"]),
    ("{'k': True, 'n': None}", {"type": "object"}, {"k": True, "n": None}),
    ("('a', 'b')", ARR, ["a", "b"]),
])
def test_decodes(text, schema, want):
    got = repair({"v": text}, obj(v=schema)).value["v"]
    assert got == want and type(got) is type(want)


@pytest.mark.parametrize("text,schema", [
    ("2.5", INT),                       # not an integer
    ("[1]", {"type": "object"}),         # wrong type after decoding
    ("007", INT),                        # not JSON
    ("1,2", ARR),
    ("", ARR),
    ("NaN", {"type": "number"}),
    ("1e999", {"type": "number"}),
    ("null", INT),                       # null not allowed
    ("true", INT),                       # a boolean is not an integer
    ("{'s': {1, 2}}", {"type": "object"}),  # a Python set is not JSON
    ("__import__('os')", ARR),           # never evaluated
    ('"text"', INT),
])
def test_leaves_undecodable_values_alone(text, schema):
    args = {"v": text}
    assert repair(args, obj(v=schema)).value is args


def test_nested_refs_and_items():
    schema = {"type": "object", "$defs": {
        "Page": obj(tags=ARR, position=INT, children={"type": "array",
                                                       "items": {"$ref": "#/$defs/Page"}})},
        "properties": {"pages": {"type": "array", "items": {"$ref": "#/$defs/Page"}}}}
    inner = json.dumps([{"position": "3"}])
    args = {"pages": json.dumps([{"tags": '["x"]', "children": inner}])}
    result = repair(args, schema)
    assert result.value == {"pages": [{"tags": ["x"], "children": [{"position": 3}]}]}
    assert fixes(result) == ["pages: string -> array", "pages[0].tags: string -> array",
                             "pages[0].children: string -> array",
                             "pages[0].children[0].position: string -> integer"]


def test_recursive_ref_does_not_loop():
    schema = {"$defs": {"A": {"$ref": "#/$defs/B"}, "B": {"$ref": "#/$defs/A"}},
              "type": "object", "properties": {"v": {"$ref": "#/$defs/A"}}}
    args = {"v": "[1]"}
    assert repair(args, schema).value is args


def test_ref_to_root_and_remote_refs():
    tree = {"type": "object", "properties": {"kids": {"type": "array", "items": {"$ref": "#"}},
                                             "n": INT}}
    got = repair({"kids": '[{"n": "1"}]'}, tree).value
    assert got == {"kids": [{"n": 1}]}
    remote = obj(v={"$ref": "https://example.com/s.json"})
    assert repair({"v": "[1]"}, remote).value == {"v": "[1]"}


def test_any_of_picks_the_branch_the_value_fits():
    schema = obj(v={"anyOf": [{"type": "null"}, {"type": "array", "items": INT}]})
    result = repair({"v": '["1", 2]'}, schema)
    assert result.value == {"v": [1, 2]}
    assert fixes(result) == ["v: string -> array", "v[0]: string -> integer"]


def test_one_of_objects_prefers_the_branch_with_matching_required_keys():
    a = {"type": "object", "required": ["kind_a"], "properties": {"kind_a": INT}}
    b = {"type": "object", "required": ["n"], "properties": {"n": INT}}
    got = repair({"v": '{"n": "4"}'}, obj(v={"oneOf": [a, b]})).value
    assert got == {"v": {"n": 4}}


def test_all_of_and_nullable_and_inferred_types():
    schema = obj(
        a={"allOf": [{"type": "array"}, {"items": INT}]},
        b={"type": "integer", "nullable": True},
        c={"properties": {"x": INT}},          # no "type": still an object
        d={"items": INT},                     # no "type": still an array
        e={"type": "number", "enum": [1, 2]},
        f={"const": 3},
    )
    got = repair({"a": '["1"]', "b": "null", "c": '{"x": "2"}', "d": "[3]", "e": "1",
                  "f": "3"}, schema).value
    assert got == {"a": [1], "b": None, "c": {"x": 2}, "d": [3], "e": 1, "f": 3}


def test_additional_and_pattern_properties():
    schema = {"type": "object", "properties": {"name": {"type": "string"}},
              "patternProperties": {"^n_": INT, "([": INT},
              "additionalProperties": ARR}
    got = repair({"name": "[1]", "n_count": "4", "other": '["z"]'}, schema).value
    assert got == {"name": "[1]", "n_count": 4, "other": ["z"]}


def test_tuple_items():
    new = {"type": "array", "prefixItems": [INT, {"type": "boolean"}], "items": ARR}
    old = {"type": "array", "items": [INT, {"type": "boolean"}], "additionalItems": ARR}
    for schema in (new, old):
        got = repair({"v": ["1", "true", '["s"]']}, obj(v=schema)).value
        assert got == {"v": [1, True, ["s"]]}


def test_whole_arguments_string():
    result = repair('{"n": "2"}', obj(n=INT))
    assert result.value == {"n": 2}
    assert fixes(result) == ["arguments: string -> object", "n: string -> integer"]
    assert repair("not json", obj(n=INT)).value == "not json"
    assert repair("[1]", obj(n=INT)).value == "[1]"


def test_wrap_is_opt_in():
    schema = obj(ids={"type": "array", "items": INT}, tags=ARR)
    args = {"ids": 4, "tags": "solo"}
    assert repair(args, schema).value is args
    result = repair(args, schema, wrap=True)
    assert result.value == {"ids": [4], "tags": ["solo"]}
    assert fixes(result) == ["ids: integer -> array (wrapped in a list)",
                             "tags: string -> array (wrapped in a list)"]
    assert repair({"ids": "7"}, schema, wrap=True).value == {"ids": [7]}


def test_keys_with_odd_characters_in_paths():
    result = repair({"a b": "1"}, {"type": "object", "properties": {"a b": INT}})
    assert fixes(result) == ['["a b"]: string -> integer']


def test_bad_schemas_do_not_raise():
    for schema in (None, [], "x", {"type": 5}, {"properties": []}, {"items": "x"},
                   {"anyOf": "x"}, {"allOf": [None]}, {"$ref": "#/nope"}):
        assert repair({"v": "1"}, schema).value == {"v": "1"}


def test_deep_nesting_is_bounded():
    schema = {"$defs": {"N": {"type": "object", "properties": {"n": {"$ref": "#/$defs/N"}}}},
              "$ref": "#/$defs/N"}
    value = current = {}
    for _ in range(200):
        current["n"] = {}
        current = current["n"]
    assert repair(value, schema).value is value


def test_repair_arguments_and_fix_type():
    assert repair_arguments({"n": "1"}, obj(n=INT)) == {"n": 1}
    assert str(Fix("", "string", "object")) == "arguments: string -> object"
    assert str(Fix("x", "string", "array", "python")) == "x: string -> array (Python literal)"


def test_index_keyed_objects_become_arrays():
    # n8n-mcp#814: [x, y] arrived as {"0": x, "1": y}, nested too, numbers as strings.
    schema = obj(position={"type": "array", "items": INT},
                 main={"type": "array", "items": {"type": "array", "items": {"type": "object"}}},
                 meta={"type": "object"})
    args = {"position": {"1": "20", "0": 10}, "main": {"0": {"0": {"node": "A"}}},
            "meta": {"0": "keep"}}
    result = repair(args, schema)
    assert result.value == {"position": [10, 20], "main": [[{"node": "A"}]],
                            "meta": {"0": "keep"}}
    assert fixes(result) == [
        "position: object -> array (index-keyed object)",
        "position[1]: string -> integer",
        "main: object -> array (index-keyed object)",
        "main[0]: object -> array (index-keyed object)"]


@pytest.mark.parametrize("value", [{}, {"1": "a"}, {"0": 1, "2": 2}, {"0": 1, "x": 2},
                                   {"00": 1}])
def test_other_objects_are_not_turned_into_arrays(value):
    args = {"v": value}
    assert repair(args, obj(v=ARR)).value is args
