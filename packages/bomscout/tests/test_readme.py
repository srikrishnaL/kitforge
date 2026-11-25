"""Keep the README honest: its example output and settings match the code."""

import json
import re
from pathlib import Path

import pytest

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


def test_registry_entry_matches_package():
    """The MCP Registry only accepts server.json if PyPI's README names it and versions agree."""
    tomllib = pytest.importorskip("tomllib")  # 3.11+

    root = Path(__file__).parent.parent
    entry = json.loads((root / "server.json").read_text())
    project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    assert entry["name"] == entry["name"].lower()
    assert re.search(rf"<!-- mcp-name: {re.escape(entry['name'])} -->", README)
    assert len(entry["description"]) <= 100
    (package,) = entry["packages"]
    assert package["identifier"] == project["name"]
    assert entry["version"] == package["version"] == project["version"]
    documented = re.findall(r"`((?:DT|DEPENDENCY_TRACK|BOMSCOUT)_[A-Z_]+)", README)
    for var in package["environmentVariables"]:
        assert var["name"] in documented, var["name"]
