"""Tool names each provider accepts."""

from __future__ import annotations

import hashlib
import re

from .core import GEMINI, OPENAI_STRICT, TARGETS

MAX_LEN = 64
_BAD = {
    OPENAI_STRICT: re.compile(r"[^A-Za-z0-9_-]"),
    GEMINI: re.compile(r"[^A-Za-z0-9_.:-]"),
}


def _digest(name: str) -> str:
    return hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]


def tool_name(name: str, target: str) -> str:
    """Return ``name`` changed only as much as ``target`` needs.

    OpenAI allows ``[A-Za-z0-9_-]{1,64}``; Gemini also allows ``.`` and ``:`` and the name must
    start with a letter or underscore. Invalid characters become ``_``; names that had to change
    in a lossy way (too long, or emptied) get a short hash of the original so two different
    tools never end up with the same name.
    """
    if target not in TARGETS:
        raise ValueError(f"unknown target {target!r}; use one of {', '.join(TARGETS)}")
    if not isinstance(name, str):
        raise TypeError("tool name must be a string")
    out = _BAD[target].sub("_", name)
    if target == GEMINI and not re.match(r"[A-Za-z_]", out[:1] or "0"):
        out = "_" + out
    if not out.strip("_"):
        out = "tool_" + _digest(name)
    if len(out) > MAX_LEN:
        out = out[: MAX_LEN - 9] + "_" + _digest(name)
    return out


def unique_name(name: str, target: str, taken: set) -> str:
    """Like :func:`tool_name`, but different from every name in ``taken``."""
    out = tool_name(name, target)
    if out not in taken:
        return out
    base = out[: MAX_LEN - 9]
    candidate = f"{base}_{_digest(name)}"
    n = 2
    while candidate in taken:
        candidate = f"{base}_{_digest(name + '#' + str(n))}"
        n += 1
    return candidate
