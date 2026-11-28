"""Tool names each provider accepts."""

from __future__ import annotations

import hashlib
import re
from typing import Optional

from .core import GEMINI, OPENAI_STRICT, TARGETS

MAX_LEN = 64
MIN_LEN = 12  # room for a few characters of the name plus "_" and the 8-character hash
_DIGEST_LEN = 8
_BAD = {
    OPENAI_STRICT: re.compile(r"[^A-Za-z0-9_-]"),
    GEMINI: re.compile(r"[^A-Za-z0-9_.:-]"),
    # No target: the characters every model API accepts (OpenAI, Anthropic, Bedrock, Gemini).
    None: re.compile(r"[^A-Za-z0-9_-]"),
}


def _digest(name: str) -> str:
    return hashlib.sha1(name.encode("utf-8")).hexdigest()[:_DIGEST_LEN]


def _cut(name: str, max_length: int) -> str:
    """The start of ``name`` that leaves room for ``_`` and the hash, without a dangling ``_``."""
    return name[: max_length - _DIGEST_LEN - 1].rstrip("_-")


def _check(target: Optional[str], max_length: int) -> None:
    if target is not None and target not in TARGETS:
        raise ValueError(f"unknown target {target!r}; use one of {', '.join(TARGETS)}")
    if isinstance(max_length, bool) or not isinstance(max_length, int) or \
            not MIN_LEN <= max_length <= MAX_LEN:
        raise ValueError(f"max_length must be a whole number from {MIN_LEN} to {MAX_LEN}")


def tool_name(name: str, target: Optional[str], max_length: int = MAX_LEN) -> str:
    """Return ``name`` changed only as much as ``target`` needs.

    OpenAI allows ``[A-Za-z0-9_-]{1,64}``; Gemini also allows ``.`` and ``:`` and the name must
    start with a letter or underscore. ``target=None`` keeps to ``[A-Za-z0-9_-]``, which every
    model API accepts. Invalid characters become ``_``; names that had to change in a lossy way
    (longer than ``max_length``, or emptied) get a short hash of the original so two different
    tools never end up with the same name. The result depends only on the arguments, so a
    tool keeps its short name across restarts.

    Pass a ``max_length`` below 64 when the client puts its own prefix in front of the name
    (``mcp__<server>__`` and the like) and the whole thing must still fit in 64 characters.
    """
    _check(target, max_length)
    if not isinstance(name, str):
        raise TypeError("tool name must be a string")
    out = _BAD[target].sub("_", name)
    if target == GEMINI and not re.match(r"[A-Za-z_]", out[:1] or "0"):
        out = "_" + out
    if not out.strip("_"):
        out = "tool_" + _digest(name)
    if len(out) > max_length:
        out = _cut(out, max_length) + "_" + _digest(name)
    return out


def unique_name(name: str, target: Optional[str], taken: set,
                max_length: int = MAX_LEN) -> str:
    """Like :func:`tool_name`, but different from every name in ``taken``."""
    out = tool_name(name, target, max_length)
    if out not in taken:
        return out
    base = _cut(out, max_length)
    candidate = f"{base}_{_digest(name)}"
    n = 2
    while candidate in taken:
        candidate = f"{base}_{_digest(name + '#' + str(n))}"
        n += 1
    return candidate
