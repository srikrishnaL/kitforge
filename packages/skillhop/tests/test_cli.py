import json
import zipfile

import pytest

from skillhop.install import LOCK_NAME
from skillhop.skills import digest


def test_ls_lists_every_page(run, cmd):
    code, out, err = run("ls", "--cmd", cmd)
    assert code == 0, err
    for path in ["pdf-processing", "acme/billing/refunds", "acme/support/refunds", "git-workflow"]:
        assert path in out
    assert "4 files" in out  # pdf-processing: SKILL.md, template, script, logo


def test_add_installs_verified_files(run, cmd, skills_root, tmp_path):
    dest = tmp_path / "skills"
    code, out, err = run("add", "--cmd", cmd, "pdf-processing", "--dir", dest, "--yes")
    assert code == 0, err
    folder = dest / "pdf-processing"
    assert (folder / "templates" / "invoice.md").read_text() == "# Invoice\n\nCustomer:\nAmount:\n"
    assert (folder / "assets" / "logo.png").read_bytes() == b"\x89PNG\r\n\x1a\n\x00\xff\xfe"
    skill_md = (folder / "SKILL.md").read_text()
    # allowed-tools is taken out, the origin is visible, the rest is kept
    assert "allowed-tools" not in skill_md
    assert "description: Extract, fill, and assemble PDF documents (from MCP server fakeskills)" in skill_md
    assert "license: Apache-2.0" in skill_md and "version: '1.2'" in skill_md
    assert skill_md.endswith("Choose the matching template from `templates/`, then run `scripts/extract.py`.\n")
    assert "removed allowed-tools" in out
    assert "scripts the agent may run" in out and "scripts/extract.py" in out
    lock = json.loads((dest / LOCK_NAME).read_text())
    rec = lock["skills"]["pdf-processing"]
    assert rec["uri"] == "skill://pdf-processing/SKILL.md"
    assert rec["server"]["command"][-1] == str(skills_root)
    assert rec["removed_frontmatter"] == ["allowed-tools"]
    assert rec["files"]["templates/invoice.md"] == digest(b"# Invoice\n\nCustomer:\nAmount:\n")


def test_keep_permissions_and_no_origin_leave_skill_md_byte_identical(run, cmd, skills_root, tmp_path):
    dest = tmp_path / "skills"
    code, _, err = run("add", "--cmd", cmd, "pdf-processing", "--dir", dest, "--yes",
                       "--keep-permissions", "--no-origin")
    assert code == 0, err
    assert (dest / "pdf-processing" / "SKILL.md").read_bytes() == \
        (skills_root / "pdf-processing" / "SKILL.md").read_bytes()


def test_tampered_file_installs_nothing(run, cmd, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_TAMPER", "pdf-processing/templates/invoice.md")
    dest = tmp_path / "skills"
    code, out, err = run("add", "--cmd", cmd, "pdf-processing", "--dir", dest, "--yes")
    assert code == 1
    assert "does not match" in err or "bytes, the entry says" in err
    assert not (dest / "pdf-processing").exists()
    assert not (dest / LOCK_NAME).exists()


def test_frontmatter_mismatch_is_refused(run, cmd, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_FM", json.dumps({"description": "Harmless helper"}))
    code, _, err = run("add", "--cmd", cmd, "git-workflow", "--dir", tmp_path / "s", "--yes")
    assert code == 1 and "frontmatter differs" in err


def test_ambiguous_name_needs_a_path(run, cmd, tmp_path):
    code, _, err = run("add", "--cmd", cmd, "refunds", "--dir", tmp_path / "s", "--yes")
    assert code == 2 and "acme/billing/refunds" in err and "acme/support/refunds" in err
    code, out, err = run("add", "--cmd", cmd, "acme/billing/refunds", "--dir", tmp_path / "s", "--yes")
    assert code == 0, err
    assert (tmp_path / "s" / "refunds" / "examples" / "email.md").exists()
    # the other one can't silently replace it ...
    code, _, err = run("add", "--cmd", cmd, "acme/support/refunds", "--dir", tmp_path / "s", "--yes")
    assert code == 1 and "use --as" in err
    # ... but can be installed beside it
    code, _, err = run("add", "--cmd", cmd, "acme/support/refunds", "--as", "support-refunds",
                       "--dir", tmp_path / "s", "--yes")
    assert code == 0, err
    md = (tmp_path / "s" / "support-refunds" / "SKILL.md").read_text()
    assert "name: support-refunds" in md and "support policy" in md


def test_existing_folder_is_never_replaced(run, cmd, tmp_path):
    mine = tmp_path / "s" / "git-workflow"
    mine.mkdir(parents=True)
    (mine / "SKILL.md").write_text("my own skill")
    code, _, err = run("add", "--cmd", cmd, "git-workflow", "--dir", tmp_path / "s", "--yes")
    assert code == 1 and "not installed by skillhop" in err
    assert (mine / "SKILL.md").read_text() == "my own skill"


def test_skill_uri_not_in_listing_uses_skills_get(run, cmd, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_PAGE", "1")
    code, out, err = run("add", "--cmd", cmd, "skill://git-workflow/SKILL.md", "--dir", tmp_path / "s", "--yes")
    assert code == 0, err
    assert (tmp_path / "s" / "git-workflow" / "SKILL.md").exists()


def test_dynamic_skill_needs_opt_in_and_uses_directory_read(run, cmd, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_DYNAMIC", "pdf-processing")
    code, _, err = run("add", "--cmd", cmd, "pdf-processing", "--dir", tmp_path / "s", "--yes")
    assert code == 1 and "--allow-dynamic" in err
    code, out, err = run("add", "--cmd", cmd, "pdf-processing", "--dir", tmp_path / "s", "--yes",
                         "--allow-dynamic")
    assert code == 0, err
    assert (tmp_path / "s" / "pdf-processing" / "templates" / "invoice.md").exists()
    assert (tmp_path / "s" / "pdf-processing" / "scripts" / "extract.py").exists()


def test_invalid_entries_are_skipped_with_a_warning(run, cmd, tmp_path, monkeypatch):
    bad = [
        {"uri": "skill://evil/SKILL.md", "frontmatter": {"name": "evil", "description": "x"},
         "resources": [{"uri": "skill://evil/SKILL.md", "digest": "sha256:" + "0" * 64, "size": 1},
                       {"uri": "skill://evil/../../.bashrc", "digest": "sha256:" + "0" * 64, "size": 1}]},
        {"uri": "skill://a/b/SKILL.md", "frontmatter": {"name": "not-b", "description": "x"},
         "resources": []},
        {"uri": "skill://nores/SKILL.md", "frontmatter": {"name": "nores", "description": "x"}},
    ]
    monkeypatch.setenv("FAKE_EXTRA", json.dumps(bad))
    code, out, err = run("add", "--cmd", cmd, "--all", "--dir", tmp_path / "s", "--yes")
    assert code == 1, err  # the second "refunds" is not installed (name taken)
    assert err.count("skipped an invalid entry") == 3
    assert "unsafe file path" in err and "last path segment" in err and "array or" in err
    assert not (tmp_path / "s" / "evil").exists()
    assert sorted(p.name for p in (tmp_path / "s").iterdir() if not p.name.startswith(".")) == \
        ["git-workflow", "pdf-processing", "refunds"]  # second refunds collides by name
    assert "use --as" in err


def test_noisy_stdout_server_still_works(run, cmd, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_NOISE", "1")
    code, _, err = run("add", "--cmd", cmd, "git-workflow", "--dir", tmp_path / "s", "--yes")
    assert code == 0, err


@pytest.mark.parametrize("mode", ["legacy", "silent-legacy", "picky"])
def test_protocol_eras(run, cmd, tmp_path, monkeypatch, mode):
    monkeypatch.setenv("FAKE_MODE", mode)
    log = tmp_path / "log.jsonl"
    monkeypatch.setenv("FAKE_LOG", str(log))
    code, _, err = run("add", "--cmd", cmd, "git-workflow", "--dir", tmp_path / "s", "--yes",
                       "--timeout", "2")
    assert code == 0, err
    msgs = [json.loads(line) for line in log.read_text().splitlines()]
    methods = [m["method"] for m in msgs]
    assert methods[0] == "server/discover"
    if mode == "picky":
        assert "initialize" not in methods
        last = msgs[-1]["params"]["_meta"]
        assert last["io.modelcontextprotocol/protocolVersion"] == "2099-01-01"
        assert last["io.modelcontextprotocol/clientCapabilities"]["extensions"] == \
            {"io.modelcontextprotocol/skills": {}}
    else:
        assert methods[1:3] == ["initialize", "notifications/initialized"]


def test_modern_requests_carry_meta(run, cmd, tmp_path, monkeypatch):
    log = tmp_path / "log.jsonl"
    monkeypatch.setenv("FAKE_LOG", str(log))
    assert run("ls", "--cmd", cmd)[0] == 0
    for m in map(json.loads, log.read_text().splitlines()):
        meta = m["params"]["_meta"]
        assert meta["io.modelcontextprotocol/protocolVersion"] == "2026-07-28"
        assert meta["io.modelcontextprotocol/clientInfo"]["name"] == "skillhop"


def test_check_update_and_remove(run, cmd, skills_root, tmp_path):
    dest = tmp_path / "s"
    assert run("add", "--cmd", cmd, "pdf-processing", "git-workflow", "--dir", dest, "--yes")[0] == 0
    code, out, _ = run("check", "--dir", dest)
    assert code == 0 and out.count(" ok ") == 2

    (skills_root / "pdf-processing" / "templates" / "invoice.md").write_text("# Invoice v2\n")
    (skills_root / "pdf-processing" / "templates" / "credit-note.md").write_text("# Credit\n")
    code, out, _ = run("check", "--dir", dest)
    assert code == 1
    assert "changed templates/invoice.md; added templates/credit-note.md" in out

    code, out, err = run("update", "--dir", dest, "--yes")
    assert code == 0, err
    assert (dest / "pdf-processing" / "templates" / "credit-note.md").read_text() == "# Credit\n"
    assert "allowed-tools" not in (dest / "pdf-processing" / "SKILL.md").read_text()
    assert run("check", "--dir", dest)[0] == 0

    # local edits are reported and not overwritten without --force
    (dest / "git-workflow" / "SKILL.md").write_text("edited")
    code, out, _ = run("status", "--dir", dest)
    assert "edited locally" in out
    code, out, _ = run("check", "--dir", dest)
    assert code == 0 and "edited" in out

    # changed on the server and edited here: update keeps the edits unless --force
    (skills_root / "git-workflow" / "SKILL.md").write_text(
        "---\nname: git-workflow\ndescription: Follow this team's Git conventions\n---\n\nUse merge.\n")
    code, out, err = run("update", "--dir", dest, "--yes")
    assert "you edited it" in err and (dest / "git-workflow" / "SKILL.md").read_text() == "edited"
    code, out, err = run("update", "--dir", dest, "--yes", "--force")
    assert code == 0, err
    assert "Use merge." in (dest / "git-workflow" / "SKILL.md").read_text()

    # a skill the server stopped serving
    import shutil
    shutil.rmtree(skills_root / "git-workflow")
    code, out, _ = run("check", "--dir", dest)
    assert code == 1 and "gone" in out

    code, out, _ = run("remove", "git-workflow", "--dir", dest)
    assert code == 0 and not (dest / "git-workflow").exists()
    assert "git-workflow" not in json.loads((dest / LOCK_NAME).read_text())["skills"]
    code, _, err = run("remove", "not-mine", "--dir", dest)
    assert code == 1 and "only removes" in err


def test_secrets_are_not_written_to_the_lock(run, cmd, tmp_path):
    dest = tmp_path / "s"
    code, _, err = run("add", "--cmd", cmd, "--env", "API_TOKEN=s3cret", "--env", "REGION=${REGION:-eu}",
                       "git-workflow", "--dir", dest, "--yes")
    assert code == 0, err
    text = (dest / LOCK_NAME).read_text()
    assert "s3cret" not in text
    env = json.loads(text)["skills"]["git-workflow"]["server"]["env"]
    assert env == {"API_TOKEN": None, "REGION": "${REGION:-eu}"}
    code, out, _ = run("check", "--dir", dest)
    assert code == 2 and "API_TOKEN" in out
    code, out, _ = run("check", "--dir", dest, "--env", "API_TOKEN=s3cret")
    assert code == 0, out


def test_zip_for_claude_upload(run, cmd, tmp_path):
    code, out, err = run("add", "--cmd", cmd, "acme/billing/refunds", "--zip", tmp_path / "zips", "--yes")
    assert code == 0, err
    with zipfile.ZipFile(tmp_path / "zips" / "refunds.zip") as z:
        assert sorted(z.namelist()) == ["refunds/SKILL.md", "refunds/examples/email.md"]


def test_agent_folders(run, cmd, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SKILLHOP_HOME", str(tmp_path / "home"))
    code, out, err = run("add", "--cmd", cmd, "git-workflow", "-a", "claude-code", "-a", "codex",
                         "-a", "cursor", "--yes")
    assert code == 0, err
    assert (tmp_path / ".claude" / "skills" / "git-workflow" / "SKILL.md").exists()
    assert (tmp_path / ".agents" / "skills" / "git-workflow" / "SKILL.md").exists()
    assert out.count("installed git-workflow") == 2  # codex and cursor share .agents/skills
    code, out, err = run("add", "--cmd", cmd, "git-workflow", "-a", "goose", "--global", "--yes")
    assert code == 0, err
    assert (tmp_path / "home" / ".config" / "goose" / "skills" / "git-workflow" / "SKILL.md").exists()


def test_no_confirmation_without_a_terminal(run, cmd, tmp_path, monkeypatch):
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    code, out, err = run("add", "--cmd", cmd, "git-workflow", "--dir", tmp_path / "s")
    assert code == 2 and "--yes" in err
    assert "Install only from servers you trust" in out
    assert not (tmp_path / "s").exists()


def test_show_prints_verified_skill_md(run, cmd, skills_root):
    code, out, err = run("show", "--cmd", cmd, "pdf-processing")
    assert code == 0, err
    assert out == (skills_root / "pdf-processing" / "SKILL.md").read_text()


def test_server_that_is_not_mcp(run, tmp_path):
    code, _, err = run("ls", "--cmd", "python3 -c \"print('hello')\"", "--timeout", "3")
    assert code == 2 and "exited" in err
