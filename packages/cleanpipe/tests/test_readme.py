"""The README's claims, checked."""

import re
from pathlib import Path

from cleanpipe import split
from cleanpipe.cli import USAGE

README = (Path(__file__).parent.parent / "README.md").read_text(encoding="utf-8")


def test_python_example_output():
    assert "# ([b'{\"jsonrpc\":\"2.0\",\"id\":1,\"result\":{}}'], [b'Loading...'])" in README
    assert split(b'Loading...{"jsonrpc":"2.0","id":1,"result":{}}\n') == (
        [b'{"jsonrpc":"2.0","id":1,"result":{}}'], [b"Loading..."])


def test_noise_table_rows():
    assert split(b"Server started on stdio\n") == ([], [b"Server started on stdio"])
    assert split(b'{"level":"info","event":"startup"}\n')[0] == []


def test_usage_and_options_match_the_cli():
    assert "cleanpipe [--log FILE | --drop] [--quiet] -- <server command> [args...]" in README
    assert USAGE.splitlines()[0] in README
    for flag in ["--log FILE", "--drop", "--quiet", "--timeout S", "--json"]:
        assert flag in README


def test_config_examples_put_cleanpipe_before_the_server():
    assert re.search(r'args = \["cleanpipe", "--", "npx"', README)
    assert '"args": ["cleanpipe", "--", "npx"' in README
