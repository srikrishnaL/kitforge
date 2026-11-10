"""Schemas as pydantic (and so FastMCP and the MCP Python SDK) generate them."""

from typing import List, Optional

import pytest

pydantic = pytest.importorskip("pydantic")

from schemashim import adapt  # noqa: E402


class Point(pydantic.BaseModel):
    lat: float
    lon: float


class Node(pydantic.BaseModel):
    name: str
    children: List["Node"] = []


class Args(pydantic.BaseModel):
    start: Point
    stops: List[Point] = []
    label: Optional[str] = None
    tree: Optional[Node] = None


def test_gemini_accepts_pydantic_schema_and_restores():
    schema = Args.model_json_schema()
    shim = adapt(schema, "gemini")
    out = shim.schema
    text = repr(out)
    assert "$ref" not in text and "$defs" not in text and "'null'" not in text
    assert out["properties"]["start"]["properties"]["lat"]["type"] == "number"
    assert out["properties"]["label"]["nullable"] is True
    args = shim.restore({"start": {"lat": 1, "lon": 2}, "label": None,
                         "tree": {"name": "a", "children": ['{"name": "b", "children": []}']}})
    parsed = Args.model_validate(args)
    assert parsed.tree.children[0].name == "b"


def test_openai_strict_pydantic_schema_round_trip():
    shim = adapt(Args.model_json_schema(), "openai-strict")
    out = shim.schema
    assert out["additionalProperties"] is False
    assert sorted(out["required"]) == ["label", "start", "stops", "tree"]
    start = out["properties"]["start"]
    assert start["additionalProperties"] is False and start["required"] == ["lat", "lon"]
    # The model sends every key; optional ones as null.
    args = shim.restore({"start": {"lat": 1.0, "lon": 2.0}, "stops": None, "label": None,
                         "tree": None})
    assert args == {"start": {"lat": 1.0, "lon": 2.0}, "label": None, "tree": None}
    Args.model_validate(args)
