"""Every command in the README parses, and the Python example uses real names."""

import re
import shlex
from pathlib import Path

import skillhop
from skillhop.cli import build_parser

README = (Path(__file__).parent.parent / "README.md").read_text()


def test_readme_commands_parse():
    blocks = re.findall(r"```bash\n(.*?)```", README, re.S)
    lines = [ln.strip() for b in blocks for ln in b.splitlines() if ln.strip().startswith("skillhop ")]
    assert len(lines) >= 8
    parser = build_parser()
    for line in lines:
        parser.parse_args(shlex.split(line)[1:])


def test_readme_python_example_names_exist():
    code = re.findall(r"```python\n(.*?)```", README, re.S)[0]
    for name in re.findall(r"from skillhop import (.*)", code)[0].split(","):
        assert hasattr(skillhop, name.strip())


def test_readme_agent_table_matches_code():
    for agent, (project, user) in skillhop.AGENTS.items():
        row = next(ln for ln in README.splitlines() if f"`{agent}`" in ln and "|" in ln)
        assert f"`{project}`" in row and f"`~/{user}`" in row, agent
