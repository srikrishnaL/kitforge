"""Skill entries (SEP-2640): validation, file paths, digest checks and frontmatter."""

from __future__ import annotations

import datetime as _dt
import hashlib
import re
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

import yaml

MAX_FILES = 512
MAX_TOTAL_BYTES = 16 * 1024 * 1024
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_WINDOWS_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
                     *(f"lpt{i}" for i in range(1, 10))}


class SkillError(Exception):
    """A skill entry or file failed validation or verification. Nothing was installed."""


def digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def valid_name(name: str) -> bool:
    """Agent Skills naming rules: 1-64 chars, lowercase letters, digits and single hyphens."""
    return isinstance(name, str) and 0 < len(name) <= 64 and bool(_NAME.match(name))


@dataclass
class FileEntry:
    uri: str
    path: str  # relative path inside the skill folder, "/"-separated
    digest: Optional[str] = None
    size: Optional[int] = None


@dataclass
class Skill:
    """One validated skills/list or skills/get entry."""

    uri: str
    root: str  # the SKILL.md URI without "/SKILL.md"
    skill_path: str  # root without the scheme, e.g. "acme/billing/refunds"
    frontmatter: Dict[str, Any]
    files: List[FileEntry] = field(default_factory=list)
    dynamic: bool = False
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.frontmatter["name"]

    @property
    def description(self) -> str:
        return str(self.frontmatter.get("description", ""))

    @property
    def total_size(self) -> int:
        return sum(f.size or 0 for f in self.files)

    def resource_set(self) -> List[Tuple[str, str, int]]:
        return sorted((f.uri, f.digest or "", f.size or 0) for f in self.files)


def _split_root(uri: str) -> Tuple[str, str]:
    if not isinstance(uri, str) or not uri.endswith("/SKILL.md"):
        raise SkillError(f"skill uri must end with /SKILL.md: {uri!r}")
    root = uri[: -len("/SKILL.md")]
    if "://" not in root:
        raise SkillError(f"skill uri has no scheme: {uri!r}")
    path = root.split("://", 1)[1]
    if not path or path.startswith("/") or path.endswith("/") or "//" in path:
        raise SkillError(f"skill uri has an empty path segment: {uri!r}")
    return root, path


def relative_path(root: str, uri: str) -> str:
    """The safe, local relative path of ``uri`` inside the skill at ``root``."""
    prefix = root + "/"
    if not isinstance(uri, str) or not uri.startswith(prefix):
        raise SkillError(f"{uri!r} is not inside the skill directory {root!r}")
    rest = uri[len(prefix):]
    if "?" in rest or "#" in rest:
        raise SkillError(f"file uri has a query or fragment: {uri!r}")
    parts = []
    for seg in rest.split("/"):
        dec = urllib.parse.unquote(seg)
        if (not dec or dec in (".", "..") or "/" in dec or "\\" in dec or "\x00" in dec
                or ":" in dec or any(ord(c) < 0x20 for c in dec)):
            raise SkillError(f"unsafe file path in {uri!r}")
        if dec.split(".")[0].lower() in _WINDOWS_RESERVED or dec.endswith((" ", ".")):
            raise SkillError(f"file name not portable (Windows reserved or trailing dot/space): {uri!r}")
        parts.append(dec)
    return "/".join(parts)


def parse_entry(entry: Any) -> Skill:
    """Validate a ``Skill`` entry as the spec requires before anything is fetched."""
    if not isinstance(entry, dict):
        raise SkillError("skill entry is not an object")
    uri = entry.get("uri")
    root, skill_path = _split_root(uri)
    fm = entry.get("frontmatter")
    if not isinstance(fm, dict) or not isinstance(fm.get("name"), str) \
            or not isinstance(fm.get("description"), str):
        raise SkillError(f"{uri}: frontmatter must have string name and description")
    name = fm["name"]
    if skill_path.split("/")[-1] != name:
        raise SkillError(f"{uri}: last path segment does not equal frontmatter name {name!r}")
    if not valid_name(name):
        raise SkillError(f"{uri}: {name!r} is not a valid skill name "
                         "(lowercase letters, digits and single hyphens, at most 64)")
    resources = entry.get("resources")
    skill = Skill(uri=uri, root=root, skill_path=skill_path, frontmatter=fm, raw=entry)
    if resources == "dynamic":
        skill.dynamic = True
        return skill
    if not isinstance(resources, list):
        raise SkillError(f"{uri}: resources must be an array or \"dynamic\"")
    if len(resources) > MAX_FILES:
        raise SkillError(f"{uri}: {len(resources)} files is over the {MAX_FILES}-file limit")
    seen_uri, seen_path = set(), set()
    for r in resources:
        if not isinstance(r, dict):
            raise SkillError(f"{uri}: resource entry is not an object")
        ruri, dig, size = r.get("uri"), r.get("digest"), r.get("size")
        if not isinstance(dig, str) or not _DIGEST.match(dig):
            raise SkillError(f"{uri}: bad digest for {ruri!r} (want sha256:<64 lowercase hex>)")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise SkillError(f"{uri}: bad size for {ruri!r}")
        if ruri in seen_uri:
            raise SkillError(f"{uri}: {ruri!r} listed twice")
        rel = relative_path(root, ruri)
        if rel.lower() in seen_path:
            raise SkillError(f"{uri}: two files differ only in case ({rel!r}); "
                             "they would overwrite each other on macOS and Windows")
        seen_uri.add(ruri)
        seen_path.add(rel.lower())
        skill.files.append(FileEntry(uri=ruri, path=rel, digest=dig, size=size))
    if uri not in seen_uri:
        raise SkillError(f"{uri}: resources does not list SKILL.md itself")
    if skill.total_size > MAX_TOTAL_BYTES:
        raise SkillError(f"{uri}: {skill.total_size} bytes is over the 16 MiB limit")
    return skill


def verify(entry: FileEntry, data: bytes) -> None:
    if entry.size is not None and len(data) != entry.size:
        raise SkillError(f"{entry.uri}: got {len(data)} bytes, the entry says {entry.size}")
    if entry.digest is not None and digest(data) != entry.digest:
        raise SkillError(f"{entry.uri}: content does not match its digest "
                         "(changed or tampered with since the listing)")


# --- frontmatter -----------------------------------------------------------------------

_FM = re.compile(r"\A(?:﻿)?---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)


def split_frontmatter(text: str) -> Tuple[Dict[str, Any], str, str]:
    """Return (frontmatter, raw frontmatter block, body) of a SKILL.md."""
    m = _FM.match(text)
    if not m:
        raise SkillError("SKILL.md does not start with YAML frontmatter")
    try:
        data = yaml.safe_load(m.group(1))
    except yaml.YAMLError as exc:
        raise SkillError(f"SKILL.md frontmatter is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise SkillError("SKILL.md frontmatter is not a mapping")
    return data, m.group(0), text[m.end():]


def _norm(v: Any) -> Any:
    """Normalize YAML/JSON values so that YAML 1.1 vs 1.2 typing doesn't count as a change."""
    if isinstance(v, dict):
        return {str(k): _norm(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_norm(x) for x in v]
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return "null"
    if isinstance(v, (_dt.date, _dt.datetime)):
        return v.isoformat()
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def frontmatter_matches(declared: Dict[str, Any], parsed: Dict[str, Any]) -> bool:
    return _norm(declared) == _norm(parsed)


PERMISSION_FIELDS = ("allowed-tools", "hooks")


def render_skill_md(body: str, frontmatter: Dict[str, Any]) -> str:
    block = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True,
                           default_flow_style=False, width=1_000_000)
    return f"---\n{block}---\n{body}"


def prepare_skill_md(data: bytes, skill: Skill, *, origin: Optional[str], rename: Optional[str],
                     keep_permissions: bool) -> Tuple[bytes, List[str]]:
    """Check SKILL.md against the entry, then apply skillhop's edits.

    Returns the bytes to write and the list of frontmatter fields that were removed.
    """
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SkillError(f"{skill.uri}: SKILL.md is not UTF-8") from exc
    parsed, _, body = split_frontmatter(text)
    if not frontmatter_matches(skill.frontmatter, parsed):
        raise SkillError(f"{skill.uri}: SKILL.md frontmatter differs from the entry's frontmatter")
    removed = [] if keep_permissions else [k for k in PERMISSION_FIELDS if k in parsed]
    if not removed and origin is None and rename is None:
        return data, []
    fm = {k: v for k, v in parsed.items() if k not in removed}
    if rename is not None:
        fm["name"] = rename
    if origin is not None:
        tag = f" (from MCP server {origin})"
        desc = str(fm.get("description", ""))
        if len(desc) + len(tag) <= 1024:
            fm["description"] = desc + tag
    return render_skill_md(body, fm).encode("utf-8"), removed


def is_script(path: str) -> bool:
    low = path.lower()
    return low.startswith("scripts/") or "/scripts/" in low or low.endswith((
        ".sh", ".bash", ".zsh", ".ps1", ".bat", ".cmd", ".py", ".js", ".mjs", ".cjs", ".ts",
        ".rb", ".pl", ".php", ".exe", ".bin"))


def summarize(skill: Skill) -> Dict[str, Union[int, List[str]]]:
    paths = [f.path for f in skill.files]
    return {
        "files": len(paths),
        "bytes": skill.total_size,
        "scripts": [p for p in paths if is_script(p)],
        "nested": [p for p in paths if p.endswith("/SKILL.md")],
    }
