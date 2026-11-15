import pytest

from briefed import Brief, fold

TOOLS = [
    {"name": "search", "description": "Search the catalog", "inputSchema": {"type": "object"}},
    {"name": "get", "inputSchema": {"type": "object"}},
]


def test_tool_mode_adds_tool_and_hints():
    out = Brief("Use search first.").tools(TOOLS)
    names = [t["name"] for t in out]
    assert names == ["search", "get", "server_instructions"]
    assert out[0]["description"].startswith("Search the catalog\n\n(Call server_instructions")
    assert out[1]["description"] == "(Call server_instructions once before using this server's tools.)"
    tool = out[2]
    assert tool["inputSchema"] == {"type": "object", "properties": {}}
    assert tool["annotations"]["readOnlyHint"] is True
    assert 20 <= len(tool["description"]) <= 1024


def test_input_is_not_changed():
    before = repr(TOOLS)
    Brief("x y z").tools(TOOLS)
    Brief("x y z", mode="inline").tools(TOOLS)
    assert repr(TOOLS) == before


@pytest.mark.parametrize("text", [None, "", "   \n", 42])
def test_no_instructions_means_no_change(text):
    b = Brief(text)
    assert b.tools(TOOLS) is TOOLS
    assert not b.handles("server_instructions")


def test_server_tool_with_same_name_wins():
    tools = TOOLS + [{"name": "server_instructions", "description": "theirs",
                      "inputSchema": {"type": "object"}}]
    out = Brief("mine").tools(tools)
    assert out == tools


def test_no_hint():
    out = Brief("text", hint=False).tools(TOOLS)
    assert out[0]["description"] == "Search the catalog"
    assert "description" not in out[1]


def test_custom_tool_name_and_call():
    b = Brief("  Dates are UTC.  ", tool_name="catalog_guide")
    assert b.tools(TOOLS)[-1]["name"] == "catalog_guide"
    assert b.handles("catalog_guide") and not b.handles("server_instructions")
    assert b.call_result() == {"content": [{"type": "text", "text": "Dates are UTC."}]}


def test_inline_mode_first_tool():
    out = Brief("Dates are UTC.", mode="inline").tools(TOOLS)
    assert [t["name"] for t in out] == ["search", "get"]
    assert out[0]["description"].startswith("How to use this server's tools")
    assert "Dates are UTC." in out[0]["description"]
    assert out[0]["description"].endswith("---\n\nSearch the catalog")
    assert "description" not in out[1]
    assert not Brief("x", mode="inline").handles("server_instructions")


def test_inline_mode_target_and_missing_target():
    out = Brief("Dates are UTC.", mode="inline", target="get").tools(TOOLS)
    assert out[0]["description"] == "Search the catalog"
    assert out[1]["description"].endswith("Dates are UTC.\n\n---")
    out = Brief("Dates are UTC.", mode="inline", target="nope").tools(TOOLS)
    assert "Dates are UTC." in out[0]["description"]


def test_odd_tool_entries_are_kept():
    tools = ["junk", {"description": "no name"}, {"name": "ok", "inputSchema": {"type": "object"}}]
    out = Brief("text").tools(tools)
    assert out[:2] == tools[:2]
    assert out[2]["description"].startswith("(Call")
    assert Brief("text").tools("not a list") == "not a list"
    assert Brief("text", mode="inline").tools([])  == []


def test_initialize_learns_and_keeps_or_moves():
    b = Brief()
    result = {"protocolVersion": "2025-06-18", "instructions": "  Server text  "}
    assert b.initialize_result(result) is result
    assert b.instructions == "Server text"
    moved = b.initialize_result(result, keep=False)
    assert "instructions" not in moved and moved["protocolVersion"] == "2025-06-18"
    assert "instructions" in result  # the original is not changed
    assert b.initialize_result("odd") == "odd"


def test_given_instructions_replace_server_and_extra_is_added():
    b = Brief("Mine.", extra="Also this.")
    b.initialize_result({"instructions": "Theirs."})
    assert b.instructions == "Mine.\n\nAlso this."
    b = Brief(extra="Also this.")
    assert b.instructions == "Also this."
    b.initialize_result({"instructions": "Theirs."})
    assert b.instructions == "Theirs.\n\nAlso this."
    b.initialize_result({})  # a later initialize without instructions keeps what was learned
    assert b.instructions == "Theirs.\n\nAlso this."


def test_bad_options():
    with pytest.raises(ValueError):
        Brief("x", mode="prompt")
    with pytest.raises(ValueError):
        Brief("x", tool_name="")


def test_fold_shortcut():
    assert fold(TOOLS, "text", mode="inline")[0]["description"].startswith("How to use")
