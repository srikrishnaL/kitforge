"""Repair tool-call arguments that arrive as JSON strings, guided by the tool's JSON Schema.

Some MCP clients and models send ``"pages": "[{\\"title\\": \\"A\\"}]"`` where the schema says
``array``, ``"limit": "5"`` where it says ``integer``, or the whole ``arguments`` object as one
string. The server then rejects the call (``expected array, received string``).

:func:`repair` walks the arguments next to the schema and decodes a string only when:

* the schema at that spot does not allow a string (so a real string is never touched), and
* the decoded value has a type the schema does allow.

Everything else is returned as it was. The input is never modified; changed parts are copied.
"""

from __future__ import annotations

import ast
import json
import math
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

TYPES = ("null", "boolean", "integer", "number", "string", "array", "object")
MAX_DEPTH = 64


@dataclass(frozen=True)
class Fix:
    """One repaired value: where it was, and what type it had before and after."""

    path: str
    was: str
    now: str
    # "json", "python" (a Python repr such as ['a', 'b']), "index" ({"0": x} -> [x]) or "wrap"
    how: str = "json"

    def __str__(self) -> str:
        where = self.path or "arguments"
        extra = {"python": " (Python literal)", "wrap": " (wrapped in a list)",
                 "index": " (index-keyed object)"}.get(self.how, "")
        return f"{where}: {self.was} -> {self.now}{extra}"


@dataclass
class Result:
    """What :func:`repair` returns: the arguments to use, and the fixes it made."""

    value: Any
    fixes: List[Fix]

    @property
    def changed(self) -> bool:
        return bool(self.fixes)


def type_of(value: Any) -> str:
    """The JSON Schema type name of a decoded JSON value."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "integer" if value.is_integer() else "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (list, tuple)):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _fits(value: Any, allowed: Set[str]) -> bool:
    t = type_of(value)
    return t in allowed or (t == "integer" and "number" in allowed)


class _Walker:
    def __init__(self, root: Any, wrap: bool) -> None:
        self.root = root
        self.wrap = wrap
        self.fixes: List[Fix] = []

    # -- schema helpers ---------------------------------------------------------------

    def resolve(self, schema: Any, seen: Tuple[str, ...] = ()) -> Any:
        """Follow a local ``$ref`` (``#/$defs/X``, ``#/definitions/X``, ``#``)."""
        while isinstance(schema, dict) and isinstance(schema.get("$ref"), str):
            ref = schema["$ref"]
            if not ref.startswith("#") or ref in seen or len(seen) > 32:
                return {}
            seen = seen + (ref,)
            target: Any = self.root
            for part in ref[1:].lstrip("/").split("/") if ref != "#" else []:
                part = part.replace("~1", "/").replace("~0", "~")
                if isinstance(target, dict) and part in target:
                    target = target[part]
                elif isinstance(target, list) and part.isdigit() and int(part) < len(target):
                    target = target[int(part)]
                else:
                    return {}
            # Sibling keywords next to $ref (allowed since 2019-09) still apply.
            rest = {k: v for k, v in schema.items() if k != "$ref"}
            schema = dict(target, **rest) if rest and isinstance(target, dict) else target
        return schema

    def allowed(self, schema: Any, depth: int = 0) -> Optional[Set[str]]:
        """Types this schema accepts, or None if it doesn't say (then nothing is decoded)."""
        schema = self.resolve(schema)
        if not isinstance(schema, dict) or not schema or depth > MAX_DEPTH:
            return None
        found: Optional[Set[str]] = None

        def narrow(types: Optional[Set[str]]) -> None:
            nonlocal found
            if types is None:
                return
            found = set(types) if found is None else _meet(found, types)

        t = schema.get("type")
        if isinstance(t, str):
            narrow({t})
        elif isinstance(t, list) and all(isinstance(x, str) for x in t):
            narrow(set(t))
        if schema.get("nullable") is True and found is not None:  # OpenAPI 3.0 style
            found.add("null")
        if "const" in schema:
            narrow({type_of(schema["const"])})
        if isinstance(schema.get("enum"), list) and schema["enum"]:
            narrow({type_of(v) for v in schema["enum"]})
        for key in ("anyOf", "oneOf"):
            branches = schema.get(key)
            if isinstance(branches, list) and branches:
                union: Set[str] = set()
                for b in branches:
                    bt = self.allowed(b, depth + 1)
                    if bt is None:  # one open branch makes the whole union open
                        union = set()
                        break
                    union |= bt
                if union:
                    narrow(union)
        for b in schema.get("allOf") or []:
            narrow(self.allowed(b, depth + 1))
        if found is None:
            if any(k in schema for k in ("properties", "patternProperties", "required",
                                         "additionalProperties")):
                found = {"object"}
            elif any(k in schema for k in ("items", "prefixItems")):
                found = {"array"}
        return found

    # -- decoding -------------------------------------------------------------------

    def decode(self, text: str, allowed: Set[str]) -> Optional[Tuple[Any, str]]:
        """The value ``text`` stands for, if it has an allowed type."""
        s = text.strip()
        if not s:
            return None
        value: Any = _NOTHING
        how = "json"
        try:
            value = json.loads(s)
            if isinstance(value, str) and value.strip()[:1] in ("[", "{"):
                value = json.loads(value)  # double-encoded: "\"[1, 2]\""
        except ValueError:
            value = _NOTHING
        if value is _NOTHING and s[:1] in ("[", "{", "(") and allowed & {"array", "object"} \
                and len(s) <= 1_000_000:
            try:  # some bridges send Python reprs: ['a', 'b'] or {'k': True}
                value = ast.literal_eval(s)
                how = "python"
                if isinstance(value, tuple):
                    value = list(value)
                if not _json_safe(value):
                    value = _NOTHING
            except (ValueError, SyntaxError, MemoryError, RecursionError, TypeError):
                value = _NOTHING
        if value is _NOTHING and "boolean" in allowed and s.lower() in ("true", "false"):
            value, how = s.lower() == "true", "json"
        if value is _NOTHING or isinstance(value, str):
            return None
        if isinstance(value, float) and not math.isfinite(value):
            return None
        if isinstance(value, float) and value.is_integer() and "integer" in allowed \
                and "number" not in allowed:
            value = int(value)
        if not _fits(value, allowed):
            return None
        return value, how

    # -- walking --------------------------------------------------------------------

    def walk(self, value: Any, schema: Any, path: str, depth: int = 0) -> Any:
        if depth > MAX_DEPTH:
            return value
        schema = self.resolve(schema)
        if not isinstance(schema, dict):
            return value
        allowed = self.allowed(schema)

        if isinstance(value, str) and allowed is not None and "string" not in allowed:
            decoded = self.decode(value, allowed)
            if decoded is not None:
                new, how = decoded
                self.fixes.append(Fix(path, "string", type_of(new), how))
                value = new
            elif self.wrap and "array" in allowed:
                return self._wrap(value, schema, path, depth)
        elif isinstance(value, dict) and allowed is not None and "array" in allowed \
                and "object" not in allowed and _index_keys(value):
            # Some bridges turn [x, y] into {"0": x, "1": y}.
            value = [value[str(i)] for i in range(len(value))]
            self.fixes.append(Fix(path, "object", "array", "index"))
        elif self.wrap and allowed is not None and "array" in allowed \
                and not isinstance(value, list) and not _fits(value, allowed):
            return self._wrap(value, schema, path, depth)

        # Pick the branch(es) of a union that the value now fits, and walk into them too.
        for key in ("anyOf", "oneOf"):
            branches = schema.get(key)
            if isinstance(branches, list):
                value = self.walk_union(value, branches, path, depth)
        for sub in schema.get("allOf") or []:
            value = self.walk(value, sub, path, depth + 1)

        if isinstance(value, dict):
            value = self.walk_object(value, schema, path, depth)
        elif isinstance(value, list):
            value = self.walk_array(value, schema, path, depth)
        return value

    def walk_union(self, value: Any, branches: List[Any], path: str, depth: int) -> Any:
        # A branch the value already matches by type wins; otherwise the first branch that
        # can decode it. Branches are tried on a scratch walker so a failed try leaves no fixes.
        fitting = []
        for branch in branches:
            bt = self.allowed(branch)
            if bt is not None and _fits(value, bt):
                fitting.append(branch)
        if isinstance(value, dict) and len(fitting) > 1:  # prefer a branch whose keys it has
            have = [b for b in fitting if set(_required(self.resolve(b))) <= set(value)]
            fitting = have or fitting
        for want_fit in (True, False):
            for branch in (fitting if want_fit else branches):
                trial = _Walker(self.root, self.wrap)
                new = trial.walk(value, branch, path, depth + 1)
                nt = self.allowed(branch)
                if nt is None or _fits(new, nt):
                    if trial.fixes or want_fit:
                        self.fixes += trial.fixes
                        return new
        return value

    def walk_object(self, value: Dict[str, Any], schema: Dict[str, Any], path: str,
                    depth: int) -> Dict[str, Any]:
        props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        patterns = schema.get("patternProperties")
        extra = schema.get("additionalProperties")
        out: Optional[Dict[str, Any]] = None
        for key, item in value.items():
            subs: List[Any] = []
            if key in props:
                subs.append(props[key])
            if isinstance(patterns, dict):
                for pattern, sub in patterns.items():
                    try:
                        if re.search(pattern, key):
                            subs.append(sub)
                    except re.error:
                        pass
            if not subs and isinstance(extra, dict):
                subs.append(extra)
            new = item
            for sub in subs:
                new = self.walk(new, sub, _join(path, key), depth + 1)
            if new is not item:
                if out is None:
                    out = dict(value)
                out[key] = new
        return value if out is None else out

    def walk_array(self, value: List[Any], schema: Dict[str, Any], path: str,
                   depth: int) -> List[Any]:
        prefix = schema.get("prefixItems")
        items = schema.get("items")
        if isinstance(items, list):  # draft 4-7 tuple form
            prefix, items = items, schema.get("additionalItems")
        out: Optional[List[Any]] = None
        for i, item in enumerate(value):
            if isinstance(prefix, list) and i < len(prefix):
                sub = prefix[i]
            elif isinstance(items, dict):
                sub = items
            else:
                continue
            new = self.walk(item, sub, f"{path}[{i}]", depth + 1)
            if new is not item:
                if out is None:
                    out = list(value)
                out[i] = new
        return value if out is None else out

    def _wrap(self, value: Any, schema: Dict[str, Any], path: str, depth: int) -> Any:
        self.fixes.append(Fix(path, type_of(value), "array", "wrap"))
        return self.walk_array([value], schema, path, depth)


_NOTHING = object()


def _meet(a: Set[str], b: Set[str]) -> Set[str]:
    """Types allowed by both (an integer is also a number)."""
    out = a & b
    if "number" in a and "integer" in b or "number" in b and "integer" in a:
        out.add("integer")
    return out


def _index_keys(value: Dict[str, Any]) -> bool:
    """True for {"0": .., "1": .., ...}: every key a decimal index, 0 to n-1, in any order."""
    return bool(value) and set(value) == {str(i) for i in range(len(value))}


def _required(schema: Any) -> List[str]:
    req = schema.get("required") if isinstance(schema, dict) else None
    return [r for r in req if isinstance(r, str)] if isinstance(req, list) else []


def _json_safe(value: Any) -> bool:
    if value is None or isinstance(value, (bool, int, str)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, (list, tuple)):
        return all(_json_safe(v) for v in value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and _json_safe(v) for k, v in value.items())
    return False


def _join(path: str, key: str) -> str:
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", key):
        return f"{path}.{key}" if path else key
    return f"{path}[{json.dumps(key)}]"


def repair(arguments: Any, schema: Any, *, wrap: bool = False) -> Result:
    """Repair ``arguments`` for a tool whose ``inputSchema`` is ``schema``.

    ``arguments`` may itself be a JSON string (some clients send the whole object that way).
    With ``wrap=True`` a single value where the schema wants an array becomes a one-item list.
    """
    walker = _Walker(schema, wrap)
    value = arguments
    if isinstance(value, str):
        try:
            parsed = json.loads(value) if value.strip() else {}
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            walker.fixes.append(Fix("", "string", "object"))
            value = parsed
    value = walker.walk(value, schema if isinstance(schema, (dict, bool)) else {}, "")
    return Result(value, walker.fixes)


def repair_arguments(arguments: Any, schema: Any, *, wrap: bool = False) -> Any:
    """Like :func:`repair`, returning only the repaired arguments."""
    return repair(arguments, schema, wrap=wrap).value
