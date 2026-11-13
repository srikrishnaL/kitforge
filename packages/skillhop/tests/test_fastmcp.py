"""Interop with a real server: FastMCP's own Skills extension (SEP-2640)."""

import shlex
import sys
from pathlib import Path

import pytest

pytest.importorskip("fastmcp.skills.extension")

SERVER = Path(__file__).parent / "servers" / "fastmcp_skills.py"


def test_install_check_update_from_fastmcp(run, skills_root, tmp_path):
    cmd = f"{shlex.quote(sys.executable)} {shlex.quote(str(SERVER))} {shlex.quote(str(skills_root))}"
    dest = tmp_path / "s"
    code, out, err = run("add", "--cmd", cmd, "pdf-processing", "git-workflow", "--dir", dest, "--yes",
                         "--timeout", "60")
    assert code == 0, err + out
    assert (dest / "pdf-processing" / "assets" / "logo.png").read_bytes() == \
        (skills_root / "pdf-processing" / "assets" / "logo.png").read_bytes()
    assert "allowed-tools" not in (dest / "pdf-processing" / "SKILL.md").read_text()
    assert run("check", "--dir", dest, "--timeout", "60")[0] == 0
    (skills_root / "git-workflow" / "SKILL.md").write_text(
        "---\nname: git-workflow\ndescription: Follow this team's Git conventions\n---\n\nUse merge.\n")
    code, out, _ = run("check", "--dir", dest, "--timeout", "60")
    assert code == 1 and "changed SKILL.md" in out
    assert run("update", "--dir", dest, "--yes", "--timeout", "60")[0] == 0
    assert "Use merge." in (dest / "git-workflow" / "SKILL.md").read_text()
