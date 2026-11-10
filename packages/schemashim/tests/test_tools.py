import pytest

from schemashim import adapt_tool, adapt_tools, tool_name

SEARCH = {"name": "github/search_issues", "description": "Search issues",
          "inputSchema": {"type": "object", "properties": {
              "q": {"type": "string"}, "per_page": {"type": "integer"}}, "required": ["q"]}}
PING = {"name": "ping", "description": "Ping", "inputSchema": {"type": "object"}}


def test_tool_names():
    assert tool_name("search", "openai-strict") == "search"
    assert tool_name("github/search", "openai-strict") == "github_search"
    assert tool_name("ns.tool:v2", "gemini") == "ns.tool:v2"
    assert tool_name("ns.tool:v2", "openai-strict") == "ns_tool_v2"
    assert tool_name("1st", "gemini") == "_1st"
    assert tool_name("1st", "openai-strict") == "1st"
    long = "x" * 100
    assert len(tool_name(long, "gemini")) == 64
    assert tool_name(long, "gemini") != tool_name(long + "y", "gemini")
    assert tool_name("搜索", "openai-strict").startswith("tool_")
    with pytest.raises(ValueError):
        tool_name("a", "nope")


def test_gemini_declaration():
    t = adapt_tool(SEARCH, "gemini")
    assert t.name == "github_search_issues" or t.name == "github_search_issues"
    assert t.declaration == {"name": t.name, "description": "Search issues",
                             "parameters": SEARCH["inputSchema"]}
    assert str(t.changes[0]).startswith("(name): renamed")


def test_gemini_no_arguments_omits_parameters():
    # langchain-google#535: empty OBJECT parameters are rejected.
    assert adapt_tool(PING, "gemini").declaration == {"name": "ping", "description": "Ping"}


def test_openai_declarations():
    t = adapt_tool(SEARCH, "openai-strict")
    d = t.declaration
    assert d["type"] == "function" and d["function"]["strict"] is True
    assert d["function"]["parameters"]["required"] == ["q", "per_page"]
    responses = {"type": "function", **t.function}
    assert responses["name"] == "github_search_issues"


def test_mcp_types_objects():
    class Tool:  # stands in for mcp.types.Tool
        name = "echo"
        description = None
        inputSchema = {"type": "object", "properties": {"text": {"type": "string"}}}

    t = adapt_tool(Tool(), "gemini")
    assert t.description == "" and t.parameters["properties"]["text"] == {"type": "string"}


def test_toolkit_round_trip():
    kit = adapt_tools({"tools": [SEARCH, PING]}, "openai-strict")
    assert len(kit) == 2 and len(kit.declarations) == 2
    name, args = kit.restore("github_search_issues", {"q": "bug", "per_page": None})
    assert name == "github/search_issues" and args == {"q": "bug"}
    with pytest.raises(KeyError):
        kit.restore("nope", {})


def test_toolkit_name_collisions():
    a = {"name": "a/b", "inputSchema": {}}
    b = {"name": "a_b", "inputSchema": {}}
    kit = adapt_tools([a, b], "openai-strict")
    names = [t.name for t in kit]
    assert len(set(names)) == 2
    assert kit.restore(names[0], {})[0] == "a/b"
    assert kit.restore(names[1], {})[0] == "a_b"


def test_toolkit_duplicates_and_nameless():
    with pytest.raises(ValueError):
        adapt_tools([PING, PING], "gemini")
    with pytest.raises(ValueError):
        adapt_tool({"inputSchema": {}}, "gemini")
