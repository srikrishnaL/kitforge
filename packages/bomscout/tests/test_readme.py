"""Keep the README honest: its example output and settings match the code."""

import json
import re
from pathlib import Path

from bomscout import server

README = (Path(__file__).parent.parent / "README.md").read_text()


def test_example_output_matches(scout):
    block = re.search(r"```json\n(\{\n  \"project\".*?)\n```", README, re.S).group(1)
    example = json.loads(block.replace('"..."', '""'))
    real = scout.project_findings("shop", min_severity="HIGH")
    assert set(example) <= set(real)
    for key in ("project", "by_severity", "by_analysis", "total", "shown"):
        assert example[key] == real[key], key
    assert set(example["findings"][0]) <= set(real["findings"][0])
    assert example["findings"][0]["component"] == real["findings"][0]["component"]


def test_every_documented_setting_is_read():
    source = "".join(p.read_text() for p in Path(server.__file__).parent.glob("*.py"))
    for name in re.findall(r"`((?:DT|DEPENDENCY_TRACK|BOMSCOUT)_[A-Z_]+)", README):
        assert name in source, name


def test_every_tool_is_documented():
    tools = re.findall(r"async def (\w+)\(", Path(server.__file__).read_text())
    for name in tools:
        if name != "run":
            assert f"`{name}`" in README, name
