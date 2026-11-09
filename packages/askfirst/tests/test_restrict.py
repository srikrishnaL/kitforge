from types import SimpleNamespace

import pytest

from askfirst import is_read_only, restrict, restrict_from_env


class Manager:
    def __init__(self, tools):
        self.tools = {t.name: t for t in tools}

    def list_tools(self):
        return list(self.tools.values())

    def remove_tool(self, name):
        del self.tools[name]


class Server:
    def __init__(self, tools):
        self._tool_manager = Manager(tools)

    def remove_tool(self, name):
        self._tool_manager.remove_tool(name)

    @property
    def names(self):
        return sorted(self._tool_manager.tools)


def tool(name, ro=None, style="obj"):
    if ro is None:
        ann = None
    elif style == "dict":
        ann = {"readOnlyHint": ro}
    elif style == "snake":
        ann = SimpleNamespace(read_only_hint=ro)
    else:
        ann = SimpleNamespace(readOnlyHint=ro)
    return SimpleNamespace(name=name, annotations=ann)


def server():
    return Server([
        tool("list_issues", True),
        tool("get_issue", True, "dict"),
        tool("search", True, "snake"),
        tool("create_issue", False),
        tool("delete_repo", False),
        tool("mystery"),  # no annotations
    ])


def test_read_only_fails_closed():
    s = server()
    removed = restrict(s, read_only=True)
    assert removed == ["create_issue", "delete_repo", "mystery"]
    assert s.names == ["get_issue", "list_issues", "search"]


def test_nothing_by_default():
    s = server()
    assert restrict(s) == []
    assert len(s.names) == 6


def test_allow_and_deny():
    s = server()
    assert restrict(s, allow=["*_issue*", "delete_repo"], deny=["delete_*"]) == [
        "delete_repo", "mystery", "search"]
    assert s.names == ["create_issue", "get_issue", "list_issues"]


def test_allow_empty_removes_everything():
    s = server()
    restrict(s, allow=[])
    assert s.names == []


def test_unknown_pattern_is_an_error():
    s = server()
    with pytest.raises(ValueError, match="deny pattern.*delete_rep0"):
        restrict(s, deny=["delete_rep0"])
    assert len(s.names) == 6  # nothing removed


def test_patterns_are_case_sensitive():
    with pytest.raises(ValueError):
        restrict(server(), deny=["DELETE_*"])


def test_comma_string_patterns():
    s = server()
    restrict(s, deny="delete_repo, create_issue")
    assert "delete_repo" not in s.names and "create_issue" not in s.names


def test_not_a_server():
    with pytest.raises(TypeError):
        restrict(object())


def test_manager_without_server_remove_tool():
    s = SimpleNamespace(_tool_manager=Manager([tool("a", True), tool("b")]))
    assert restrict(s, read_only=True) == ["b"]


def test_is_read_only_strict_true():
    assert not is_read_only(tool("x", "true", "dict"))
    assert not is_read_only(SimpleNamespace(name="x"))


@pytest.mark.parametrize(
    "env, left",
    [
        ({}, 6),
        ({"MCP_READ_ONLY": "true"}, 3),
        ({"MCP_READ_ONLY": " Off "}, 6),
        ({"MCP_DENY_TOOLS": "delete_*,create_*"}, 4),
        ({"MCP_ALLOW_TOOLS": "list_*"}, 1),
        ({"MCP_ALLOW_TOOLS": ""}, 0),
        ({"MCP_READ_ONLY": "1", "MCP_ALLOW_TOOLS": "list_*,delete_*"}, 1),
    ],
)
def test_from_env(env, left):
    s = server()
    restrict_from_env(s, env=env)
    assert len(s.names) == left


def test_from_env_prefix_and_bad_flag():
    s = server()
    restrict_from_env(s, prefix="GH", env={"GH_READ_ONLY": "yes", "MCP_DENY_TOOLS": "nope"})
    assert len(s.names) == 3
    with pytest.raises(ValueError, match="GH_READ_ONLY"):
        restrict_from_env(server(), prefix="GH", env={"GH_READ_ONLY": "maybe"})


def test_old_sdk_without_remove_tool():
    class OldManager:
        def __init__(self, tools):
            self._tools = {t.name: t for t in tools}

        def list_tools(self):
            return list(self._tools.values())

    s = SimpleNamespace(_tool_manager=OldManager([tool("a", True), tool("b")]))
    assert restrict(s, read_only=True) == ["b"]
    assert list(s._tool_manager._tools) == ["a"]

    s = SimpleNamespace(_tool_manager=SimpleNamespace(list_tools=lambda: [tool("b")]))
    with pytest.raises(TypeError):
        restrict(s, read_only=True)
