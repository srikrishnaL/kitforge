"""Keep the README honest: its options match the code."""

import inspect
import re
from pathlib import Path

import pytest

from reattach import Reattach

README = (Path(__file__).parent.parent / "README.md").read_text(encoding="utf-8")


def test_middleware_options_in_the_readme_exist():
    params = inspect.signature(Reattach).parameters
    for name in ["secret", "path", "max_sessions", "on_reattach"]:
        assert name in params and f"`{name}" in README
    assert "`reattached`" in README and hasattr(Reattach(lambda *a: None), "reattached")


def test_every_proxy_option_in_the_readme_exists(capsys):
    from reattach.cli import main

    with pytest.raises(SystemExit):
        main(["proxy", "--help"])
    help_text = capsys.readouterr().out
    flags = set(re.findall(r"`(--[a-z-]+)", README))
    assert flags >= {"--host", "--port", "--path", "--secret", "--timeout", "--quiet"}
    for flag in flags:
        assert flag in help_text, flag
