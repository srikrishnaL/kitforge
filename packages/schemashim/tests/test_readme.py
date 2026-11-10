"""The README's first example, as written."""

from schemashim import adapt


def test_readme_example():
    schema = {
        "type": "object",
        "$defs": {"Point": {"type": "object", "required": ["lat", "lon"],
                            "properties": {"lat": {"type": "number"},
                                           "lon": {"type": "number"}}}},
        "properties": {
            "start": {"$ref": "#/$defs/Point"},
            "limit": {"anyOf": [{"type": "integer"}, {"type": "null"}], "default": None},
            "tags": {"type": "array"},
        },
        "required": ["start"],
    }
    shim = adapt(schema, "gemini")
    assert [str(c) for c in shim.changes] == [
        "start: inlined $ref #/$defs/Point (references are not supported)",
        "limit: null branch of anyOf changed to nullable",
        "tags: array without an items type: passed as a JSON string",
    ]
    assert shim.restore({"start": {"lat": 1, "lon": 2}, "tags": '["a", "b"]'}) == {
        "start": {"lat": 1, "lon": 2}, "tags": ["a", "b"]}
