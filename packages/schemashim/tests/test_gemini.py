import copy

import pytest

from schemashim import adapt, check


def g(schema):
    return adapt(schema, "gemini")


def messages(shim):
    return [str(c) for c in shim.changes]


def test_clean_schema_is_untouched():
    schema = {"type": "object", "properties": {"q": {"type": "string", "description": "Query"}},
              "required": ["q"]}
    shim = g(schema)
    assert shim.ok
    assert shim.schema == schema


def test_input_is_not_modified():
    schema = {"type": "object", "$defs": {"A": {"type": "string"}},
              "properties": {"a": {"$ref": "#/$defs/A"}}, "additionalProperties": False}
    before = copy.deepcopy(schema)
    g(schema)
    assert schema == before


def test_refs_are_inlined_and_defs_removed():
    shim = g({"type": "object", "$defs": {"Point": {"type": "object", "properties": {
        "lat": {"type": "number"}, "lon": {"type": "number"}}, "required": ["lat", "lon"]}},
        "properties": {"start": {"$ref": "#/$defs/Point", "description": "Start"}}})
    start = shim.schema["properties"]["start"]
    assert start["type"] == "object"
    assert start["description"] == "Start"
    assert set(start["properties"]) == {"lat", "lon"}
    assert "$defs" not in shim.schema
    assert any("inlined $ref" in m for m in messages(shim))


def test_legacy_definitions_refs():
    shim = g({"type": "object", "definitions": {"S": {"type": "string"}},
              "properties": {"s": {"$ref": "#/definitions/S"}}})
    assert shim.schema["properties"]["s"] == {"type": "string"}


def test_recursive_ref_becomes_json_string_and_restores():
    shim = g({"type": "object", "$defs": {"Node": {"type": "object", "properties": {
        "name": {"type": "string"}, "children": {"type": "array",
                                                 "items": {"$ref": "#/$defs/Node"}}}}},
        "properties": {"tree": {"$ref": "#/$defs/Node"}}})
    children = shim.schema["properties"]["tree"]["properties"]["children"]
    assert children["items"]["type"] == "string"
    assert "recursive" in " ".join(messages(shim))
    args = shim.restore({"tree": {"name": "a", "children": ['{"name": "b"}']}})
    assert args == {"tree": {"name": "a", "children": [{"name": "b"}]}}


def test_unresolvable_ref():
    shim = g({"type": "object", "properties": {"x": {"$ref": "https://example.com/x.json"}}})
    assert shim.schema["properties"]["x"]["type"] == "string"
    assert any("can't resolve" in m for m in messages(shim))


def test_optional_anyof_null_becomes_nullable():
    # What pydantic / FastMCP emit for `limit: Optional[int] = None`.
    shim = g({"type": "object", "properties": {"limit": {
        "anyOf": [{"type": "integer"}, {"type": "null"}], "default": None, "title": "Limit"}}})
    assert shim.schema["properties"]["limit"] == {"type": "integer", "title": "Limit",
                                                  "nullable": True}


def test_type_list_with_null():
    # opencode#48073: {"type": ["array", "null"], "items": ...}
    shim = g({"type": "object", "properties": {"languages": {
        "type": ["array", "null"], "items": {"type": "string"}}}})
    assert shim.schema["properties"]["languages"] == {
        "type": "array", "items": {"type": "string"}, "nullable": True}


def test_type_list_of_two_types_becomes_anyof():
    shim = g({"type": "object", "properties": {"id": {"type": ["string", "integer"]}}})
    assert shim.schema["properties"]["id"] == {"anyOf": [{"type": "string"},
                                                         {"type": "integer"}]}


def test_oneof_becomes_anyof():
    shim = g({"type": "object", "properties": {"v": {"oneOf": [
        {"type": "string"}, {"type": "number"}]}}})
    assert shim.schema["properties"]["v"] == {"anyOf": [{"type": "string"},
                                                        {"type": "number"}]}


def test_allof_is_merged():
    shim = g({"type": "object", "$defs": {"Base": {"type": "object", "properties": {
        "id": {"type": "string"}}, "required": ["id"]}},
        "properties": {"item": {"allOf": [{"$ref": "#/$defs/Base"}, {"properties": {
            "n": {"type": "integer"}}, "required": ["n"]}]}}})
    item = shim.schema["properties"]["item"]
    assert set(item["properties"]) == {"id", "n"}
    assert sorted(item["required"]) == ["id", "n"]


def test_array_without_items_is_json_string():
    # cherry-studio#18890, backstage#34038: arrays without items.
    shim = g({"type": "object", "properties": {"ids": {"type": "array",
                                                       "description": "IDs"}}})
    ids = shim.schema["properties"]["ids"]
    assert ids["type"] == "string"
    assert ids["description"].startswith("IDs")
    assert shim.restore({"ids": "[1, 2]"}) == {"ids": [1, 2]}


def test_free_form_object_is_json_string():
    shim = g({"type": "object", "properties": {"meta": {"type": "object",
                                                        "additionalProperties": True}}})
    assert shim.schema["properties"]["meta"]["type"] == "string"
    assert shim.restore({"meta": '{"a": 1}'}) == {"meta": {"a": 1}}
    # The model sometimes sends the object anyway; keep it.
    assert shim.restore({"meta": {"a": 1}}) == {"meta": {"a": 1}}
    # Not JSON: passed through for the tool to reject.
    assert shim.restore({"meta": "nope"}) == {"meta": "nope"}


def test_empty_root_is_object_and_ok():
    for schema in ({}, None, {"type": "object"}, {"type": "object", "properties": {}}):
        shim = g(schema)
        assert shim.schema == {"type": "object", "properties": {}}
        assert shim.ok


def test_root_not_an_object():
    # litellm#18442: "parameters schema should be of type OBJECT"
    shim = g({"type": "string"})
    assert shim.schema["type"] == "object"
    assert not shim.ok


def test_root_anyof_of_objects_is_merged():
    shim = g({"anyOf": [
        {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
        {"type": "object", "properties": {"id": {"type": "string"}, "name": {"type": "string"}},
         "required": ["id", "name"]}]})
    assert set(shim.schema["properties"]) == {"id", "name"}
    assert shim.schema["required"] == ["id"]


def test_empty_and_non_string_enum_values():
    # kilocode#15512, nakama#2497: enum [""] breaks every Gemini request.
    shim = g({"type": "object", "properties": {"mode": {"type": "string",
                                                        "enum": ["", "fast", "slow"]}}})
    assert shim.schema["properties"]["mode"]["enum"] == ["fast", "slow"]
    shim = g({"type": "object", "properties": {"mode": {"type": "string", "enum": [""]}}})
    assert "enum" not in shim.schema["properties"]["mode"]


def test_integer_enum_moves_to_description():
    shim = g({"type": "object", "properties": {"level": {"type": "integer", "enum": [1, 2],
                                                         "description": "Level"}}})
    level = shim.schema["properties"]["level"]
    assert "enum" not in level
    assert level["description"] == "Level (one of: 1, 2)"


def test_missing_type_is_inferred():
    # t3code#21655, crawl4ai#1311, mcp-jetbrains#71: properties with no type.
    shim = g({"type": "object", "properties": {
        "model": {"enum": ["a", "b"]}, "n": {"minimum": 1}, "opts": {"properties": {
            "x": {"type": "string"}}}}})
    p = shim.schema["properties"]
    assert p["model"]["type"] == "string"
    assert p["n"]["type"] == "number"
    assert p["opts"]["type"] == "object"


def test_untyped_any_value_is_json_string():
    shim = g({"type": "object", "properties": {"value": {"description": "Anything"}}})
    assert shim.schema["properties"]["value"]["type"] == "string"
    assert shim.restore({"value": "3"}) == {"value": 3}


def test_required_listing_undefined_property():
    # cline#11162: 'property is not defined'
    shim = g({"type": "object", "properties": {"a": {"type": "string"}},
              "required": ["a", "port"]})
    assert shim.schema["required"] == ["a"]


def test_boolean_required_flags():
    # hermes-agent#104796: FastAPI-style "required": true inside a property.
    shim = g({"type": "object", "properties": {
        "a": {"type": "string", "required": True}, "b": {"type": "string", "required": False}}})
    assert shim.schema["required"] == ["a"]
    assert "required" not in shim.schema["properties"]["a"]


def test_unsupported_keywords_removed_or_described():
    # modelcontextprotocol/servers#1624: mcp-server-fetch's schema.
    fetch = {"type": "object", "title": "Fetch", "properties": {
        "url": {"type": "string", "format": "uri", "minLength": 1, "title": "Url"},
        "max_length": {"type": "integer", "default": 5000, "exclusiveMaximum": 1000000,
                       "exclusiveMinimum": 0, "title": "Max Length"},
        "raw": {"type": "boolean", "default": False, "title": "Raw"}},
        "required": ["url"], "$schema": "https://json-schema.org/draft/2020-12/schema"}
    shim = g(fetch)
    url = shim.schema["properties"]["url"]
    assert "format" not in url and url["minLength"] == 1 and "uri" in url["description"]
    ml = shim.schema["properties"]["max_length"]
    assert "exclusiveMaximum" not in ml and "less than 1000000" in ml["description"].lower()
    assert ml["default"] == 5000
    assert "$schema" not in shim.schema
    assert not shim.ok


def test_not_and_conditionals_removed():
    # cloudflare/mcp-server-cloudflare#378: {"not": {}}
    shim = g({"type": "object", "properties": {"a": {"type": "string", "not": {}},
                                               "b": {"type": "string", "if": {}, "then": {}}}})
    assert shim.schema["properties"]["a"] == {"type": "string"}
    assert shim.schema["properties"]["b"] == {"type": "string"}
    assert check({"type": "object", "properties": {"a": {"type": "string", "not": {}}}},
                 "gemini")


def test_const():
    shim = g({"type": "object", "properties": {"kind": {"const": "point"},
                                               "v": {"type": "integer", "const": 2}}})
    assert shim.schema["properties"]["kind"] == {"type": "string", "enum": ["point"]}
    assert "must be 2" in shim.schema["properties"]["v"]["description"].lower()


def test_supported_formats_kept():
    shim = g({"type": "object", "properties": {"at": {"type": "string", "format": "date-time"},
                                               "n": {"type": "integer", "format": "int64"}}})
    assert shim.ok


def test_additional_properties_false_removed():
    shim = g({"type": "object", "properties": {"a": {"type": "string"}},
              "additionalProperties": False})
    assert "additionalProperties" not in shim.schema


def test_depth_limit():
    schema = {"type": "string"}
    for _ in range(6):
        schema = {"type": "object", "properties": {"x": schema}}
    shim = adapt(schema, "gemini", max_depth=3)
    node = shim.schema
    for _ in range(3):
        node = node["properties"]["x"]
    assert node["properties"]["x"]["type"] == "string"
    assert "JSON" in node["properties"]["x"]["description"]


def test_integer_floats_restored():
    shim = g({"type": "object", "properties": {"n": {"type": "integer"},
                                               "f": {"type": "number"}}})
    out = shim.restore({"n": 5.0, "f": 5.0})
    assert out == {"n": 5, "f": 5.0} and isinstance(out["n"], int)
    assert shim.restore({"n": 5.5}) == {"n": 5.5}


def test_bad_arguments():
    with pytest.raises(ValueError):
        adapt({}, "claude")
    with pytest.raises(TypeError):
        adapt("not a schema", "gemini")
    with pytest.raises(ValueError):
        adapt({}, "gemini", max_depth=0)
