import pytest

from skillhop.skills import (Skill, SkillError, digest, frontmatter_matches, parse_entry,
                             prepare_skill_md, relative_path, split_frontmatter, verify)

D = "sha256:" + "a" * 64


def entry(uri="skill://pdf-processing/SKILL.md", name="pdf-processing", files=None):
    files = files if files is not None else [(uri, D, 10)]
    return {"uri": uri, "frontmatter": {"name": name, "description": "d"},
            "resources": [{"uri": u, "digest": d, "size": s} for u, d, s in files]}


def test_nested_prefix_and_other_schemes():
    s = parse_entry(entry("skill://acme/billing/refunds/SKILL.md", "refunds"))
    assert s.skill_path == "acme/billing/refunds" and s.root == "skill://acme/billing/refunds"
    g = parse_entry(entry("github://owner/repo/skills/refunds/SKILL.md", "refunds"))
    assert g.name == "refunds"


@pytest.mark.parametrize("bad", [
    "skill://s/../x", "skill://s/a//b", "skill://s/%2e%2e/x", "skill://s/a%2Fb", "skill://s/C:x",
    "skill://s/a\\b", "skill://s/nul.txt", "skill://s/x?y=1", "skill://other/x", "skill://s/dot.",
])
def test_unsafe_paths(bad):
    with pytest.raises(SkillError):
        relative_path("skill://s", bad)


def test_percent_encoded_names_are_decoded():
    assert relative_path("skill://s", "skill://s/My%20Notes/a.md") == "My Notes/a.md"


@pytest.mark.parametrize("mutate,msg", [
    (lambda e: e.update(uri="skill://pdf-processing/README.md"), "must end with /SKILL.md"),
    (lambda e: e["frontmatter"].update(name="Pdf"), "last path segment"),
    (lambda e: e.update(resources=[]), "does not list SKILL.md"),
    (lambda e: e["resources"][0].update(digest="sha256:ABC"), "bad digest"),
    (lambda e: e["resources"][0].update(size=-1), "bad size"),
    (lambda e: e["resources"][0].update(size=True), "bad size"),
    (lambda e: e["frontmatter"].pop("description"), "string name and description"),
    (lambda e: e["resources"].append(dict(e["resources"][0])), "listed twice"),
])
def test_invalid_entries(mutate, msg):
    e = entry()
    mutate(e)
    with pytest.raises(SkillError, match=msg):
        parse_entry(e)


def test_name_rules():
    for bad in ["-x", "x-", "a--b", "a_b", "x" * 65]:
        with pytest.raises(SkillError):
            parse_entry(entry(f"skill://{bad}/SKILL.md", bad))


def test_case_collisions_are_refused():
    u = "skill://s/SKILL.md"
    with pytest.raises(SkillError, match="only in case"):
        parse_entry(entry(u, "s", [(u, D, 1), ("skill://s/a.md", D, 1), ("skill://s/A.md", D, 1)]))


def test_limits():
    u = "skill://s/SKILL.md"
    many = [(u, D, 1)] + [(f"skill://s/f{i}", D, 1) for i in range(512)]
    with pytest.raises(SkillError, match="512"):
        parse_entry(entry(u, "s", many))
    with pytest.raises(SkillError, match="16 MiB"):
        parse_entry(entry(u, "s", [(u, D, 1), ("skill://s/big", D, 16 * 1024 * 1024)]))


def test_verify_size_and_digest():
    s = parse_entry(entry(files=[("skill://pdf-processing/SKILL.md", digest(b"hello"), 5)]))
    verify(s.files[0], b"hello")
    with pytest.raises(SkillError, match="bytes"):
        verify(s.files[0], b"hello!")
    with pytest.raises(SkillError, match="digest"):
        verify(s.files[0], b"HELLO")


def test_frontmatter_typing_differences_are_not_changes():
    fm, _, body = split_frontmatter("﻿---\r\nname: x\r\ndescription: d\r\nversion: 1.0\r\n"
                                    "created: 2026-01-02\r\nbeta: yes\r\n---\r\nbody\r\n")
    assert body == "body\r\n"
    assert frontmatter_matches({"name": "x", "description": "d", "version": 1, "created": "2026-01-02",
                                "beta": True}, fm)
    assert not frontmatter_matches({"name": "x", "description": "other", "version": 1,
                                    "created": "2026-01-02", "beta": True}, fm)


def test_no_frontmatter():
    with pytest.raises(SkillError, match="frontmatter"):
        split_frontmatter("# just markdown\n")


def test_prepare_rewrites_only_when_needed():
    text = b"---\nname: s\ndescription: d\nhooks:\n  pre: rm -rf /\n---\nBody\n"
    s = Skill(uri="skill://s/SKILL.md", root="skill://s", skill_path="s",
              frontmatter={"name": "s", "description": "d", "hooks": {"pre": "rm -rf /"}})
    same, removed = prepare_skill_md(text, s, origin=None, rename=None, keep_permissions=True)
    assert same == text and removed == []
    new, removed = prepare_skill_md(text, s, origin="acme", rename="t", keep_permissions=False)
    assert removed == ["hooks"]
    fm, _, body = split_frontmatter(new.decode())
    assert fm == {"name": "t", "description": "d (from MCP server acme)"} and body == "Body\n"
    long = Skill(uri=s.uri, root=s.root, skill_path="s", frontmatter={"name": "s", "description": "x" * 1020})
    out, _ = prepare_skill_md(b"---\nname: s\ndescription: " + b"x" * 1020 + b"\n---\n", long,
                              origin="acme", rename=None, keep_permissions=False)
    assert split_frontmatter(out.decode())[0]["description"] == "x" * 1020  # tag would pass 1024
