"""Tool schema snapshots and breaking-change detection.

A snapshot is the server's tool list in a stable, diff-friendly JSON form.
Comparing two snapshots tells you which changes will break agents or
clients that were built against the old version.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Union

SNAPSHOT_FORMAT = 1

_KEEP = ("title", "description", "inputSchema", "outputSchema", "annotations")


def normalize(tools: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Turn a ``tools/list`` result into a snapshot dictionary."""
    out: Dict[str, Any] = {}
    for tool in sorted(tools, key=lambda t: str(t.get("name", ""))):
        name = str(tool.get("name", ""))
        out[name] = {k: tool[k] for k in _KEEP if k in tool}
    return {"format": SNAPSHOT_FORMAT, "tools": out}


def dumps(snapshot: Dict[str, Any]) -> str:
    return json.dumps(snapshot, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def save(path: Union[str, Path], tools_or_snapshot: Any) -> Dict[str, Any]:
    snap = _as_snapshot(tools_or_snapshot)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(dumps(snap), encoding="utf-8")
    return snap


def load(path: Union[str, Path]) -> Dict[str, Any]:
    return _as_snapshot(json.loads(Path(path).read_text(encoding="utf-8")))


def _as_snapshot(obj: Any) -> Dict[str, Any]:
    if isinstance(obj, dict) and "tools" in obj and isinstance(obj["tools"], dict):
        return obj
    if isinstance(obj, dict) and "tools" in obj and isinstance(obj["tools"], list):
        return normalize(obj["tools"])  # a raw tools/list result
    if isinstance(obj, list):
        return normalize(obj)
    raise ValueError("expected a tools list or a snapshot")


@dataclass(frozen=True)
class Change:
    kind: str  # "breaking" or "compatible"
    tool: str
    message: str

    @property
    def breaking(self) -> bool:
        return self.kind == "breaking"

    def __str__(self) -> str:
        mark = "BREAKING" if self.breaking else "ok      "
        return f"{mark} {self.tool}: {self.message}"

    def as_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "tool": self.tool, "message": self.message}


def _types(schema: Dict[str, Any]) -> Optional[frozenset]:
    t = schema.get("type")
    if t is None:
        return None
    if isinstance(t, list):
        return frozenset(t)
    return frozenset([t])


def _type_widened(old: Optional[frozenset], new: Optional[frozenset]) -> bool:
    if old is None or new is None:
        return new is None  # dropping the type constraint accepts more
    if old <= new:
        return True
    # integer -> number accepts every old value.
    return old == frozenset(["integer"]) and "number" in new


def diff(old: Any, new: Any) -> List[Change]:
    """Compare two snapshots (or tool lists). Breaking changes come first."""
    a = _as_snapshot(old)["tools"]
    b = _as_snapshot(new)["tools"]
    changes: List[Change] = []

    for name in sorted(set(a) - set(b)):
        changes.append(Change("breaking", name, "tool was removed"))
    for name in sorted(set(b) - set(a)):
        changes.append(Change("compatible", name, "tool was added"))
    for name in sorted(set(a) & set(b)):
        changes.extend(_diff_tool(name, a[name], b[name]))

    changes.sort(key=lambda c: (not c.breaking, c.tool))
    return changes


def _diff_tool(name: str, old: Dict[str, Any], new: Dict[str, Any]) -> List[Change]:
    out: List[Change] = []
    if old.get("description") != new.get("description"):
        out.append(Change("compatible", name, "description changed"))
    if old.get("annotations") != new.get("annotations"):
        out.append(Change("compatible", name, "annotations changed"))

    oin = old.get("inputSchema") or {}
    nin = new.get("inputSchema") or {}
    oprops = oin.get("properties") or {}
    nprops = nin.get("properties") or {}
    oreq = set(oin.get("required") or [])
    nreq = set(nin.get("required") or [])

    for p in sorted(set(oprops) - set(nprops)):
        out.append(Change("breaking", name, f"parameter {p!r} was removed"))
    for p in sorted(set(nprops) - set(oprops)):
        if p in nreq:
            out.append(Change("breaking", name, f"new required parameter {p!r}"))
        else:
            out.append(Change("compatible", name, f"new optional parameter {p!r}"))
    for p in sorted(set(oprops) & set(nprops)):
        out.extend(_diff_param(name, p, oprops[p] or {}, nprops[p] or {}))
        if p in nreq and p not in oreq:
            out.append(Change("breaking", name, f"parameter {p!r} is now required"))
        elif p in oreq and p not in nreq:
            out.append(Change("compatible", name, f"parameter {p!r} is now optional"))

    if oin.get("additionalProperties", True) is not False and nin.get("additionalProperties", True) is False:
        out.append(Change("breaking", name, "extra parameters are no longer accepted"))

    oout = old.get("outputSchema")
    nout = new.get("outputSchema")
    if oout and not nout:
        out.append(Change("breaking", name, "outputSchema was removed"))
    elif oout and nout:
        oo = (oout.get("properties") or {})
        no = (nout.get("properties") or {})
        for p in sorted(set(oo) - set(no)):
            out.append(Change("breaking", name, f"output field {p!r} was removed"))
        for p in sorted(set(no) - set(oo)):
            out.append(Change("compatible", name, f"new output field {p!r}"))
        for p in sorted(set(oo) & set(no)):
            if _types(oo[p] or {}) != _types(no[p] or {}):
                out.append(Change("breaking", name, f"output field {p!r} changed type"))
    elif nout and not oout:
        out.append(Change("compatible", name, "outputSchema was added"))
    return out


def _diff_param(tool: str, p: str, old: Dict[str, Any], new: Dict[str, Any]) -> List[Change]:
    out: List[Change] = []
    ot, nt = _types(old), _types(new)
    if ot != nt:
        kind = "compatible" if _type_widened(ot, nt) else "breaking"
        out.append(Change(kind, tool, f"parameter {p!r} type changed {_fmt(ot)} -> {_fmt(nt)}"))
    if "enum" in old or "enum" in new:
        oe = old.get("enum")
        ne = new.get("enum")
        if oe is not None and ne is None:
            out.append(Change("compatible", tool, f"parameter {p!r} no longer limited to fixed values"))
        elif ne is not None and oe is None:
            out.append(Change("breaking", tool, f"parameter {p!r} is now limited to {ne!r}"))
        else:
            removed = [v for v in oe if v not in ne]
            added = [v for v in ne if v not in oe]
            if removed:
                out.append(Change("breaking", tool, f"parameter {p!r} no longer accepts {removed!r}"))
            if added:
                out.append(Change("compatible", tool, f"parameter {p!r} now also accepts {added!r}"))
    for bound, tighter in (("minimum", 1), ("maximum", -1), ("minLength", 1), ("maxLength", -1)):
        ob, nb = old.get(bound), new.get(bound)
        if ob == nb or nb is None:
            if ob is not None and nb is None:
                out.append(Change("compatible", tool, f"parameter {p!r} {bound} removed"))
            continue
        if ob is None or (nb - ob) * tighter > 0:
            out.append(Change("breaking", tool, f"parameter {p!r} {bound} tightened to {nb}"))
        else:
            out.append(Change("compatible", tool, f"parameter {p!r} {bound} relaxed to {nb}"))
    if old.get("description") != new.get("description"):
        out.append(Change("compatible", tool, f"parameter {p!r} description changed"))
    return out


def _fmt(t: Optional[frozenset]) -> str:
    if t is None:
        return "any"
    return "|".join(sorted(t))


def breaking(changes: Iterable[Change]) -> List[Change]:
    return [c for c in changes if c.breaking]
