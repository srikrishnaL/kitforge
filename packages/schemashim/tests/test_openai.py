from schemashim import adapt


def o(schema):
    return adapt(schema, "openai-strict")


def test_strict_clean_schema_is_untouched():
    schema = {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"],
              "additionalProperties": False}
    shim = o(schema)
    assert shim.ok and shim.schema == schema


def test_optional_fields_become_nullable_and_required():
    shim = o({"type": "object", "properties": {"q": {"type": "string"},
                                               "limit": {"type": "integer"}},
              "required": ["q"]})
    assert shim.schema == {
        "type": "object",
        "properties": {"q": {"type": "string"}, "limit": {"type": ["integer", "null"]}},
        "required": ["q", "limit"], "additionalProperties": False}
    # The model must send null for limit; the tool never sees it.
    assert shim.restore({"q": "x", "limit": None}) == {"q": "x"}
    assert shim.restore({"q": "x", "limit": 3}) == {"q": "x", "limit": 3}


def test_null_kept_when_original_allowed_it():
    shim = o({"type": "object", "properties": {"note": {"type": ["string", "null"]}},
              "required": []})
    assert shim.schema["properties"]["note"] == {"type": ["string", "null"]}
    assert shim.restore({"note": None}) == {"note": None}


def test_optional_enum_gets_null_value():
    shim = o({"type": "object", "properties": {"sort": {"type": "string",
                                                        "enum": ["asc", "desc"]}}})
    sort = shim.schema["properties"]["sort"]
    assert sort["type"] == ["string", "null"] and sort["enum"] == ["asc", "desc", None]


def test_optional_anyof_gets_null_branch():
    shim = o({"type": "object", "properties": {"id": {"anyOf": [{"type": "string"},
                                                                {"type": "integer"}]}}})
    assert shim.schema["properties"]["id"]["anyOf"][-1] == {"type": "null"}


def test_nested_objects_are_closed():
    shim = o({"type": "object", "properties": {"f": {"type": "object", "properties": {
        "a": {"type": "string"}}}}, "required": ["f"]})
    f = shim.schema["properties"]["f"]
    assert f["additionalProperties"] is False and f["required"] == ["a"]
    assert shim.restore({"f": {"a": None}}) == {"f": {}}


def test_refs_kept_inline_without_notes():
    shim = o({"type": "object", "$defs": {"A": {"type": "string"}},
              "properties": {"a": {"$ref": "#/$defs/A"}}, "required": ["a"],
              "additionalProperties": False})
    assert shim.ok
    assert shim.schema["properties"]["a"] == {"type": "string"}


def test_unsupported_constraints_go_to_description():
    shim = o({"type": "object", "properties": {"name": {
        "type": "string", "minLength": 2, "maxLength": 40, "default": "x",
        "description": "Name"}}, "required": ["name"], "additionalProperties": False})
    name = shim.schema["properties"]["name"]
    assert set(name) == {"type", "description"}
    assert name["description"] == ("Name (at least 2 character(s); at most 40 character(s); "
                                   "default: x)")


def test_lookaround_pattern_moved():
    # openclaw#166877: one tool with a regex lookaround fails every request.
    shim = o({"type": "object", "properties": {
        "a": {"type": "string", "pattern": "^(?!-).+$"},
        "b": {"type": "string", "pattern": "^[a-z]+$"}},
        "required": ["a", "b"], "additionalProperties": False})
    assert "pattern" not in shim.schema["properties"]["a"]
    assert shim.schema["properties"]["b"]["pattern"] == "^[a-z]+$"


def test_formats():
    shim = o({"type": "object", "properties": {
        "e": {"type": "string", "format": "email"}, "u": {"type": "string", "format": "uri"}},
        "required": ["e", "u"], "additionalProperties": False})
    assert shim.schema["properties"]["e"]["format"] == "email"
    assert "format" not in shim.schema["properties"]["u"]


def test_free_form_root_has_no_arguments():
    shim = o({"type": "object", "additionalProperties": True})
    assert shim.schema == {"type": "object", "properties": {}, "required": [],
                           "additionalProperties": False}
    assert not shim.ok


def test_empty_root_is_ok():
    shim = o({})
    assert shim.schema == {"type": "object", "properties": {}, "required": [],
                           "additionalProperties": False}


def test_free_form_optional_property_round_trip():
    shim = o({"type": "object", "properties": {"meta": {"type": "object"}}})
    meta = shim.schema["properties"]["meta"]
    assert meta["type"] == ["string", "null"]
    assert shim.restore({"meta": None}) == {}
    assert shim.restore({"meta": '{"a": [1]}'}) == {"meta": {"a": [1]}}


def test_openapi_nullable_keyword():
    shim = o({"type": "object", "properties": {"a": {"type": "string", "nullable": True}},
              "required": ["a"], "additionalProperties": False})
    assert shim.schema["properties"]["a"] == {"type": ["string", "null"]}
    assert not shim.ok


def test_restore_none_and_arrays():
    shim = o({"type": "object", "properties": {"rows": {"type": "array", "items": {
        "type": "object", "properties": {"n": {"type": "integer"}, "tag": {"type": "string"}},
        "required": ["n"]}}}, "required": ["rows"]})
    assert shim.restore(None) == {}
    assert shim.restore({"rows": [{"n": 1.0, "tag": None}]}) == {"rows": [{"n": 1}]}
