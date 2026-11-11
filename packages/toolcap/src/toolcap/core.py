"""Fit tool results under a token budget, with cursor pagination."""

from __future__ import annotations

import base64
import binascii
import dataclasses
import functools
import hashlib
import inspect
import json
import os
import typing
from typing import Any, Callable, List, Optional, Sequence, Tuple, Union

from .errors import CursorError

#: Claude Code refuses MCP results over 25,000 tokens by default; stay under it.
DEFAULT_MAX_TOKENS = 20_000
MIN_TOKENS = 100

Path = Tuple[str, ...]


def estimate_tokens(text: str) -> int:
    """A deliberately high token estimate: one token per 3 bytes of UTF-8.

    Real tokenizers average about 4 characters per token for English prose,
    fewer for JSON, code and IDs, and about 1 token per character for Chinese,
    Japanese or Korean (3 UTF-8 bytes each). Counting bytes / 3 stays at or
    above all of these, so a result that fits here fits in the client.
    """
    return -(-len(text.encode("utf-8")) // 3)


# --------------------------------------------------------------------------- #
# JSON conversion


def jsonable(value: Any) -> Any:
    """Convert pydantic models, dataclasses, tuples and sets into plain JSON values."""
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, (set, frozenset)):
        items = [jsonable(v) for v in value]
        try:
            return sorted(items)
        except TypeError:
            return items
    dump = getattr(value, "model_dump", None)  # pydantic v2
    if callable(dump):
        return jsonable(dump(mode="json"))
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return jsonable(dataclasses.asdict(value))
    return str(value)


# --------------------------------------------------------------------------- #
# Cursors


def _fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def encode_cursor(offset: int, fingerprint: str) -> str:
    raw = f"1:{offset}:{fingerprint}".encode("ascii")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> Tuple[int, str]:
    """Return ``(offset, fingerprint)``; raise :class:`CursorError` for anything else."""
    bad = CursorError(
        f"Invalid cursor {cursor[:40]!r}. Pass the next_cursor value from the previous result "
        "exactly as it was given, or call the tool without a cursor to start from the beginning."
    )
    text = cursor.strip()
    try:
        raw = base64.b64decode(text + "=" * (-len(text) % 4), altchars=b"-_", validate=True).decode("ascii")
    except (binascii.Error, ValueError, UnicodeDecodeError):
        raise bad from None
    parts = raw.split(":")
    if len(parts) != 3 or parts[0] != "1" or not parts[1].isdigit():
        raise bad
    return int(parts[1]), parts[2]


# --------------------------------------------------------------------------- #
# Shrinking one value


def _cut_strings(value: Any, limit: int) -> Any:
    if isinstance(value, str):
        if len(value) <= limit:
            return value
        return value[:limit] + f"... [{len(value) - limit} more characters cut]"
    if isinstance(value, dict):
        return {k: _cut_strings(v, limit) for k, v in value.items()}
    if isinstance(value, list):
        return [_cut_strings(v, limit) for v in value]
    return value


def _cut_lists(value: Any, limit: int) -> Any:
    if isinstance(value, dict):
        return {k: _cut_lists(v, limit) for k, v in value.items()}
    if isinstance(value, list):
        head = [_cut_lists(v, limit) for v in value[:limit]]
        if len(value) > limit:
            head.append(f"... [{len(value) - limit} more items cut]")
        return head
    return value


def _longest(value: Any, kind: type) -> int:
    if isinstance(value, kind) and kind is str:
        return len(value)
    best = len(value) if isinstance(value, kind) else 0
    children = value.values() if isinstance(value, dict) else value if isinstance(value, list) else ()
    for child in children:
        best = max(best, _longest(child, kind))
    return best


def _largest_fit(lo: int, hi: int, fits: Callable[[int], bool]) -> Optional[int]:
    """Largest n in [lo, hi] with fits(n), assuming fits is monotonic; None if none fits."""
    if not fits(lo):
        return None
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if fits(mid):
            lo = mid
        else:
            hi = mid - 1
    return lo


def shrink(value: Any, fits: Callable[[Any], bool]) -> Tuple[Any, bool]:
    """Cut long strings, then long lists, until ``fits(value)``. Returns (value, was_cut).

    Every key stays, so the shape the caller expects is kept.
    """
    if fits(value):
        return value, False
    longest = _longest(value, str)
    if longest > 0:
        limit = _largest_fit(0, longest, lambda n: fits(_cut_strings(value, n)))
        if limit is not None:
            return _cut_strings(value, limit), True
    value = _cut_strings(value, 0)
    longest = _longest(value, list)
    limit = _largest_fit(0, longest, lambda n: fits(_cut_lists(value, n)))
    if limit is not None:
        return _cut_lists(value, limit), True
    return _cut_lists(value, 0), True


# --------------------------------------------------------------------------- #
# Locating the list to page through


def _get(value: Any, path: Path) -> Any:
    for key in path:
        value = value[key]
    return value


def _replace(value: Any, path: Path, new: Any) -> Any:
    if not path:
        return new
    out = dict(value)
    out[path[0]] = _replace(value[path[0]], path[1:], new)
    return out


def _find_list(value: Any, path: Path = ()) -> Optional[Path]:
    """Path (through dict keys only) to the list with the largest JSON size."""
    if isinstance(value, list):
        return path
    if not isinstance(value, dict):
        return None
    best: Optional[Path] = None
    best_size = -1
    for key, child in value.items():
        found = _find_list(child, path + (key,))
        if found is None:
            continue
        size = len(json.dumps(_get(value, found[len(path):]), ensure_ascii=False, default=str))
        if size > best_size:
            best, best_size = found, size
    return best


# --------------------------------------------------------------------------- #


class Cap:
    """Keep tool results under ``max_tokens``, paging through long lists and text.

    ``max_tokens``: the budget for one result. Defaults to the ``TOOLCAP_MAX_TOKENS``
    environment variable, else 20,000 (Claude Code rejects results over 25,000).
    ``count``: a function from text to token count; defaults to :func:`estimate_tokens`.
    Pass a real tokenizer (``lambda s: len(enc.encode(s))``) for a tighter fit.
    ``indent``: JSON indentation of results; ``None`` (compact) uses the fewest tokens.
    """

    def __init__(
        self,
        max_tokens: Optional[int] = None,
        *,
        count: Optional[Callable[[str], int]] = None,
        indent: Optional[int] = None,
        cursor_param: str = "cursor",
    ):
        if max_tokens is None:
            env = os.environ.get("TOOLCAP_MAX_TOKENS", "").strip()
            if env and not env.isdigit():
                raise ValueError(f"TOOLCAP_MAX_TOKENS must be a whole number, got {env!r}")
            max_tokens = int(env) if env else DEFAULT_MAX_TOKENS
        if max_tokens < MIN_TOKENS:
            raise ValueError(f"max_tokens must be at least {MIN_TOKENS}, got {max_tokens}")
        if not cursor_param.isidentifier():
            raise ValueError(f"cursor_param must be a valid parameter name, got {cursor_param!r}")
        self.max_tokens = max_tokens
        self.count = count or estimate_tokens
        self.indent = indent
        self.cursor_param = cursor_param

    # ------------------------------------------------------------------ helpers

    def fits(self, text: str) -> bool:
        return self.count(text) <= self.max_tokens

    def _dumps(self, value: Any) -> str:
        if self.indent is None:
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
        return json.dumps(value, ensure_ascii=False, indent=self.indent, default=str)

    def _again(self, cursor: str) -> str:
        return (f"Call this tool again with the same arguments and {self.cursor_param}="
                f'"{cursor}" for the next page.')

    # ------------------------------------------------------------------ public

    def page(self, data: Any, cursor: Optional[str] = "", *, key: Union[str, Sequence[str], None] = None) -> str:
        """Return ``data`` as text that fits the budget.

        Lists (or the largest list inside a dict, or the one at ``key``, a dotted path
        such as ``"data.items"``) are split into pages of whole items; text is split
        at line breaks. Each page says how to get the next one. Pass the ``cursor``
        the model sends back to get that page. Results that already fit are returned
        unchanged (text as is, everything else as JSON).
        """
        return self._fit(data, cursor or "", key, paginate=True)

    def trim(self, data: Any, *, key: Union[str, Sequence[str], None] = None) -> str:
        """Like :meth:`page`, but without cursors: the rest is cut, with a note saying how much."""
        return self._fit(data, "", key, paginate=False)

    def paginate(self, fn: Optional[Callable] = None, *, key: Union[str, Sequence[str], None] = None):
        """Decorator: run the tool, then return :meth:`page` of its result.

        Adds a ``cursor: str = ""`` parameter (named ``cursor_param``) to the tool's
        signature unless it has one, and mentions it in the docstring, so MCP
        frameworks show it to the model. Works on sync and async functions; put it
        below ``@mcp.tool()``.
        """
        if fn is None:
            return lambda f: self.paginate(f, key=key)
        return _wrap(self, fn, key)

    # ------------------------------------------------------------------ fitting

    def _fit(self, data: Any, cursor: str, key: Any, paginate: bool) -> str:
        offset, seen = decode_cursor(cursor) if cursor else (0, None)
        if isinstance(data, str):
            return self._fit_text(data, offset, seen, paginate)
        value = jsonable(data)
        full = self._dumps(value)
        if offset == 0 and self.fits(full):
            return full
        path: Optional[Path]
        if key is not None:
            path = tuple(key.split(".")) if isinstance(key, str) else tuple(key)
            try:
                target = _get(value, path)
            except (KeyError, TypeError, IndexError):
                raise KeyError(f"toolcap: no value at key {'.'.join(path)!r}") from None
            if not isinstance(target, list):
                raise TypeError(f"toolcap: the value at key {'.'.join(path)!r} is not a list")
        else:
            path = _find_list(value)
        if path is None:  # nothing to page through: cut it down instead
            return self._dumps(self._shrink_all(value))
        return self._fit_list(value, path, offset, seen, _fingerprint(full), paginate)

    def _shrink_all(self, value: Any) -> Any:
        out, _ = shrink(value, lambda v: self.fits(self._dumps(v)))
        return out

    def _meta_key(self, value: Any, path: Path) -> Tuple[Any, Path, str]:
        """Where pagination info goes: next to the list, without clobbering a real key."""
        if not path:  # a bare list: wrap it
            return {"items": value}, ("items",), "page"
        name = "page"
        while name in value:
            name = "_" + name
        return value, path, name

    def _fit_list(self, value: Any, path: Path, offset: int, seen: Optional[str],
                  fp: str, paginate: bool) -> str:
        container, path, meta_key = self._meta_key(value, path)
        items: List[Any] = _get(container, path)
        total = len(items)
        changed = seen is not None and seen != fp
        state = {"container": container}

        def render(n: int, cut_item: Any = None) -> str:
            shown = items[offset:offset + n] if cut_item is None else [cut_item]
            end = offset + len(shown)
            meta = {"items": f"{offset + 1}-{end} of {total}" if shown else f"0 of {total}",
                    "total": total}
            notes = []
            if changed:
                notes.append("The results changed since the previous page, so items may be "
                             "repeated or missed; call without a cursor to start over.")
            if cut_item is not None:
                notes.append(f"Item {offset + 1} was too large and was cut to fit.")
            if end < total:
                if paginate:
                    nxt = encode_cursor(end, fp)
                    meta["next_cursor"] = nxt
                    notes.append(f"Showing items {offset + 1}-{end} of {total}. " + self._again(nxt))
                else:
                    notes.append(f"Showing the first {end} of {total} items; {total - end} more "
                                 "were left out to fit the size limit. Ask for fewer results "
                                 "(filters, a smaller limit) to see the rest.")
            elif offset >= total:
                notes.append("No more items." if total else "No items.")
            else:
                meta["next_cursor"] = None
                if offset:
                    notes.append(f"Showing items {offset + 1}-{end} of {total}. This is the last page.")
            if notes:
                meta["note"] = " ".join(notes)
            out = _replace(state["container"], path, shown)
            if isinstance(out, dict):
                out = dict(out)
                out[meta_key] = meta
            return self._dumps(out)

        if not self.fits(render(0)):
            # The fields around the list are over budget on their own: cut them to half
            # the budget, leaving the other half for the items.
            half = self.max_tokens // 2
            base, _ = shrink(_replace(container, path, []),
                             lambda b: self.count(self._dumps(dict(b, **{meta_key: {}}))) <= half)
            state["container"] = _replace(base, path, items)
        if offset >= total:
            return render(0)

        remaining = total - offset
        # Grow the page exponentially, then binary-search the edge, so the cost is
        # proportional to the page size rather than to the whole result.
        hi = 1
        while hi < remaining and self.fits(render(hi)):
            hi = min(hi * 2, remaining)
        if self.fits(render(hi)):
            return render(hi)
        n = _largest_fit(1, hi, lambda k: self.fits(render(k)))
        if n is not None:
            return render(n)
        # Not even one whole item fits: cut that item down so the page still moves on.
        cut, _ = shrink(items[offset], lambda v: self.fits(render(1, cut_item=v)))
        return render(1, cut_item=cut)

    def _fit_text(self, text: str, offset: int, seen: Optional[str], paginate: bool) -> str:
        if offset == 0 and self.fits(text):
            return text
        fp = _fingerprint(text)
        total = len(text)
        changed = seen is not None and seen != fp

        def footer(end: int) -> str:
            notes = []
            if changed:
                notes.append("The text changed since the previous page; call without a cursor to "
                             "start over.")
            if offset >= total:
                notes.append("No more text.")
            elif end < total:
                where = f"characters {offset + 1}-{end} of {total}"
                if paginate:
                    notes.append(f"Output cut: showing {where}. " + self._again(encode_cursor(end, fp)))
                else:
                    notes.append(f"Output cut: showing {where}; the rest was left out to fit the "
                                 "size limit.")
            elif offset:
                notes.append(f"Showing characters {offset + 1}-{end} of {total}. This is the end.")
            return "\n\n[" + " ".join(notes) + "]" if notes else ""

        if offset >= total:
            return footer(offset).lstrip("\n")

        def render(end: int) -> str:
            return text[offset:end] + footer(end)

        end = _largest_fit(offset, total, lambda e: self.fits(render(e)))
        if end is None or end == offset:
            raise ValueError("toolcap: max_tokens is too small to hold even the page footer")
        if end < total:
            end = _break_at(text, offset, end)
        return render(end)


def _break_at(text: str, start: int, end: int) -> int:
    """Move ``end`` back to a line break (or else a space) if one is near the end of the page."""
    window = max(1, (end - start) // 5)
    for sep in ("\n", " "):
        at = text.rfind(sep, end - window, end)
        if at > start:
            return at + 1
    return end


# --------------------------------------------------------------------------- #
# Decorator


def _wrap(cap: Cap, fn: Callable, key: Any) -> Callable:
    name = cap.cursor_param
    sig = inspect.signature(fn)
    try:
        hints = typing.get_type_hints(fn, include_extras=True)
    except Exception:  # unresolvable forward references: keep the raw annotations
        hints = dict(getattr(fn, "__annotations__", {}))
    params = [p.replace(annotation=hints.get(p.name, p.annotation)) for p in sig.parameters.values()]
    added = name not in sig.parameters
    if added:
        new = inspect.Parameter(name, inspect.Parameter.KEYWORD_ONLY, default="", annotation=str)
        # Keep keyword-only parameters after positional ones, and **kwargs last.
        idx = len(params)
        for i, p in enumerate(params):
            if p.kind == inspect.Parameter.VAR_KEYWORD:
                idx = i
                break
        params.insert(idx, new)
    # No return annotation: with ``-> str`` the MCP SDKs would also send the page as
    # structuredContent ({"result": "..."}), doubling its size on the wire.
    new_sig = sig.replace(parameters=params, return_annotation=inspect.Signature.empty)

    def split(args: tuple, kwargs: dict) -> Tuple[tuple, dict, str]:
        if added:
            cursor = kwargs.pop(name, "")
        else:
            bound = sig.bind_partial(*args, **kwargs)
            cursor = bound.arguments.get(name, sig.parameters[name].default)
            if cursor is inspect.Parameter.empty:
                cursor = ""
        return args, kwargs, cursor or ""

    if inspect.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            args, kwargs, cursor = split(args, kwargs)
            if cursor:
                decode_cursor(cursor)  # reject a bad cursor before doing the work
            return cap.page(await fn(*args, **kwargs), cursor, key=key)
    else:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            args, kwargs, cursor = split(args, kwargs)
            if cursor:
                decode_cursor(cursor)
            return cap.page(fn(*args, **kwargs), cursor, key=key)

    wrapper.__signature__ = new_sig  # type: ignore[attr-defined]
    annotations = {p.name: p.annotation for p in params if p.annotation is not inspect.Parameter.empty}
    wrapper.__annotations__ = annotations
    del wrapper.__wrapped__  # frameworks must see the new signature, not the original
    doc = inspect.getdoc(fn) or ""
    hint = (f"Long results come in pages: if a result has a next_cursor, call again with the same "
            f"arguments and {name} set to it.")
    wrapper.__doc__ = f"{doc}\n\n{hint}" if doc else hint
    return wrapper
