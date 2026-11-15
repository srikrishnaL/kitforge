"""Keep the README honest: its options and examples match the code."""

import re
from pathlib import Path

from briefed import Brief, fold
from briefed.cli import main

README = (Path(__file__).parent.parent / "README.md").read_text(encoding="utf-8")


def test_every_proxy_option_in_the_readme_exists(capsys):
    try:
        main(["proxy", "--help"])
    except SystemExit:
        pass
    help_text = capsys.readouterr().out
    for flag in set(re.findall(r"`(--[a-z-]+)", README)):
        assert flag in help_text, flag


def test_middleware_options_in_the_readme_exist():
    import inspect

    from briefed.asgi import Briefed

    params = inspect.signature(Briefed).parameters
    for name in ["mode", "tool_name", "target", "hint", "extra", "keep", "path"]:
        assert name in params and f"`{name}" in README


def test_code_example():
    tools = [{"name": "search", "description": "Search", "inputSchema": {"type": "object"}}]
    brief = Brief("Call search before get. All dates are UTC.")
    out = brief.tools(tools)
    assert out[-1]["name"] == "server_instructions"
    assert "(Call server_instructions once before using this server's tools.)" in README
    assert out[0]["description"].endswith("(Call server_instructions once before using this server's tools.)")
    assert brief.handles("server_instructions")
    assert fold(tools, "x") == Brief("x").tools(tools)
