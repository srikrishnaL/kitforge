"""Fetch, verify and install skills into agents' skill folders, with a lock file."""

from __future__ import annotations

import datetime as _dt
import io
import json
import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .rpc import Client, McpError
from .skills import (MAX_FILES, MAX_TOTAL_BYTES, FileEntry, Skill, SkillError, digest, parse_entry,
                     prepare_skill_md, relative_path, valid_name, verify)

LOCK_NAME = ".skillhop.json"

# Where each agent loads skills from: (project folder, user folder under $HOME).
AGENTS: Dict[str, Tuple[str, str]] = {
    "claude-code": (".claude/skills", ".claude/skills"),
    "codex": (".agents/skills", ".agents/skills"),
    "cursor": (".agents/skills", ".cursor/skills"),
    "gemini-cli": (".agents/skills", ".gemini/skills"),
    "github-copilot": (".agents/skills", ".copilot/skills"),
    "goose": (".goose/skills", ".config/goose/skills"),
    "opencode": (".agents/skills", ".config/opencode/skills"),
    "agents": (".agents/skills", ".agents/skills"),
}


def skills_dir(agent: str, global_: bool = False, cwd: Optional[Path] = None) -> Path:
    if agent not in AGENTS:
        raise SkillError(f"unknown agent {agent!r}; one of: {', '.join(AGENTS)} (or use --dir)")
    project, user = AGENTS[agent]
    if global_:
        return Path(os.environ.get("SKILLHOP_HOME") or Path.home()) / user
    return (cwd or Path.cwd()) / project


# --- choosing skills ---------------------------------------------------------------------

def select(skills: Sequence[Skill], wanted: Sequence[str]) -> List[Skill]:
    """Match names, paths (acme/billing/refunds) or SKILL.md URIs. Ambiguous names fail."""
    out: List[Skill] = []
    for w in wanted:
        hits = [s for s in skills if w in (s.uri, s.root, s.skill_path)]
        if not hits:
            hits = [s for s in skills if s.name == w]
        if not hits:
            names = ", ".join(sorted({s.name for s in skills})) or "none"
            raise SkillError(f"the server lists no skill {w!r} (it lists: {names})")
        if len(hits) > 1:
            paths = ", ".join(s.skill_path for s in hits)
            raise SkillError(f"{len(hits)} skills are named {w!r} ({paths}); give the path instead")
        if hits[0] not in out:
            out.append(hits[0])
    return out


def entries(client: Client, wanted: Sequence[str] = (), *, all_: bool = False
            ) -> Tuple[List[Skill], List[str]]:
    """Valid entries for ``wanted`` (or every listed skill), plus problems with other entries."""
    problems: List[str] = []
    if client.skills_extension is None:
        problems.append(f"{client.label} does not declare the Skills extension "
                        "(io.modelcontextprotocol/skills); trying anyway")
    direct = [w for w in wanted if "://" in w]
    listed: List[Skill] = []
    if not direct or len(direct) < len(wanted) or all_:
        try:
            raw = client.list_skills()
        except McpError as exc:
            raise SkillError(f"{client.label}: skills/list failed: {exc.message}; the server may "
                             "not serve skills over MCP (SEP-2640) yet") from exc
        for e in raw:
            try:
                listed.append(parse_entry(e))
            except SkillError as exc:
                problems.append(f"skipped an invalid entry: {exc}")
    for uri in direct:  # skills/get works for skills that are not in the listing
        if not any(s.uri == uri for s in listed):
            try:
                listed.append(parse_entry(client.get_skill(uri)))
            except McpError as exc:
                raise SkillError(f"{client.label} serves no skill at {uri}: {exc.message}") from exc
    if all_:
        return listed, problems
    return select(listed, wanted), problems


# --- fetching ----------------------------------------------------------------------------

def _walk_dynamic(client: Client, skill: Skill) -> List[FileEntry]:
    ext = client.skills_extension or {}
    files = [FileEntry(uri=skill.uri, path="SKILL.md")]
    if not ext.get("directoryRead"):
        return files
    stack, seen = [skill.root], set()
    while stack:
        d = stack.pop()
        if d in seen:
            continue
        seen.add(d)
        for r in client.read_directory(d):
            uri = r.get("uri")
            if r.get("mimeType") == "inode/directory":
                relative_path(skill.root, uri)
                stack.append(uri)
            elif uri != skill.uri:
                files.append(FileEntry(uri=uri, path=relative_path(skill.root, uri)))
            if len(files) > MAX_FILES:
                raise SkillError(f"{skill.uri}: more than {MAX_FILES} files")
    return files


@dataclass
class Fetched:
    skill: Skill
    files: Dict[str, bytes] = field(default_factory=dict)  # relative path -> bytes to write
    removed: List[str] = field(default_factory=list)  # frontmatter fields taken out
    name: str = ""


def fetch(client: Client, skill: Skill, *, rename: Optional[str] = None, tag_origin: bool = True,
          keep_permissions: bool = False, allow_dynamic: bool = False) -> Fetched:
    """Read every file of ``skill`` and verify it against the entry. Raises on any mismatch."""
    if rename is not None and not valid_name(rename):
        raise SkillError(f"--as {rename!r} is not a valid skill name")
    if skill.dynamic:
        if not allow_dynamic:
            raise SkillError(f"{skill.uri} is generated on the server (resources: \"dynamic\"), so "
                             "its content cannot be verified; pass --allow-dynamic to install it anyway")
        file_list = _walk_dynamic(client, skill)
    else:
        file_list = skill.files
    out = Fetched(skill=skill, name=rename or skill.name)
    total = 0
    for f in file_list:
        data = client.read_resource(f.uri)
        verify(f, data)
        total += len(data)
        if total > MAX_TOTAL_BYTES:
            raise SkillError(f"{skill.uri}: over the 16 MiB limit")
        if f.uri == skill.uri:
            data, out.removed = prepare_skill_md(
                data, skill, origin=client.label if tag_origin else None, rename=rename,
                keep_permissions=keep_permissions)
        out.files[f.path] = data
    if "SKILL.md" not in out.files:
        raise SkillError(f"{skill.uri}: SKILL.md was not fetched")
    return out


# --- lock file ---------------------------------------------------------------------------

def read_lock(directory: Path) -> Dict[str, Any]:
    path = directory / LOCK_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"version": 1, "skills": {}}
    except ValueError as exc:
        raise SkillError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("skills"), dict):
        raise SkillError(f"{path} is not a skillhop lock file")
    return data


def write_lock(directory: Path, lock: Dict[str, Any]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    tmp = directory / (LOCK_NAME + ".tmp")
    tmp.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, directory / LOCK_NAME)


def _hashes(files: Dict[str, bytes]) -> Dict[str, str]:
    return {p: digest(b) for p, b in sorted(files.items())}


def local_hashes(folder: Path) -> Dict[str, str]:
    out = {}
    for p in sorted(folder.rglob("*")):
        if p.is_file() and not p.is_symlink():
            out[p.relative_to(folder).as_posix()] = digest(p.read_bytes())
    return out


# --- installing --------------------------------------------------------------------------

def install(fetched: Fetched, directory: Path, server: Dict[str, Any], *, force: bool = False,
            options: Optional[Dict[str, Any]] = None) -> Path:
    """Write the skill atomically into ``directory/<name>`` and record it in the lock file."""
    lock = read_lock(directory)
    name = fetched.name
    target = directory / name
    owned = lock["skills"].get(name)
    if target.exists() or target.is_symlink():
        if owned is None and not force:
            raise SkillError(f"{target} already exists and was not installed by skillhop; "
                             f"skillhop will not replace it (use --as <new-name> to install beside it)")
        if owned is not None and (owned.get("server", {}).get("label"), owned.get("uri")) != \
                (server.get("label"), fetched.skill.uri) and not force:
            raise SkillError(f"{target} was installed from {owned.get('server', {}).get('label')} "
                             f"({owned.get('uri')}); use --as <new-name> to install this one beside it")
        if owned is not None and not force and target.is_dir() \
                and local_hashes(target) != owned.get("files"):
            raise SkillError(f"{target} was edited after skillhop installed it; "
                             "pass --force to replace your edits")
    directory.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{name}.", dir=directory))
    try:
        for rel, data in fetched.files.items():
            dest = staging.joinpath(*rel.split("/"))
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "wb") as fh:
                fh.write(data)
            os.chmod(dest, 0o644)
        old = None
        if target.exists() or target.is_symlink():
            old = directory / f".{name}.old"
            if old.exists():
                shutil.rmtree(old)
            os.replace(target, old)
        os.replace(staging, target)
        if old is not None:
            shutil.rmtree(old, ignore_errors=True)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    lock["skills"][name] = {
        "name": fetched.skill.name,
        "uri": fetched.skill.uri,
        "server": server,
        "dynamic": fetched.skill.dynamic,
        "resources": [{"uri": u, "digest": d, "size": s} for u, d, s in fetched.skill.resource_set()],
        "files": _hashes(fetched.files),
        "removed_frontmatter": fetched.removed,
        "options": options or {},
        "installed_at": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    write_lock(directory, lock)
    return target


def remove(directory: Path, name: str) -> Path:
    lock = read_lock(directory)
    if name not in lock["skills"]:
        raise SkillError(f"{name!r} is not in {directory / LOCK_NAME}; skillhop only removes "
                         "skills it installed")
    target = directory / name
    if target.is_dir() and not target.is_symlink():
        shutil.rmtree(target)
    elif target.exists() or target.is_symlink():
        target.unlink()
    del lock["skills"][name]
    write_lock(directory, lock)
    return target


def to_zip(fetched: Fetched) -> bytes:
    """A zip with one top-level folder, the layout Claude's skill upload expects."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for rel, data in sorted(fetched.files.items()):
            info = zipfile.ZipInfo(f"{fetched.name}/{rel}", date_time=(2020, 1, 1, 0, 0, 0))
            info.external_attr = 0o644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)
    return buf.getvalue()


# --- checking for changes ----------------------------------------------------------------

@dataclass
class Status:
    name: str
    state: str  # "ok", "changed", "gone", "edited", "error"
    detail: str = ""
    edited: bool = False  # files were changed locally after install


def compare(locked: Dict[str, Any], skill: Skill) -> str:
    old = {r["uri"]: (r["digest"], r["size"]) for r in locked.get("resources", [])}
    new = {u: (d, s) for u, d, s in skill.resource_set()}
    if skill.dynamic or locked.get("dynamic"):
        return "dynamic skill: cannot tell whether it changed"
    if old == new:
        return ""
    parts = []
    added = sorted(set(new) - set(old))
    gone = sorted(set(old) - set(new))
    changed = sorted(u for u in set(old) & set(new) if old[u] != new[u])
    root = skill.root + "/"
    short = lambda u: u[len(root):] if u.startswith(root) else u  # noqa: E731
    if changed:
        parts.append("changed " + ", ".join(map(short, changed)))
    if added:
        parts.append("added " + ", ".join(map(short, added)))
    if gone:
        parts.append("removed " + ", ".join(map(short, gone)))
    return "; ".join(parts)
