"""Rewrite MCP tool results so clients that only read text blocks still see the data.

The MCP spec lets a tool return more than text: ``structuredContent``, ``resource_link``
blocks, embedded resources (``type: "resource"``, text or base64 blob) and audio. Many
clients handle only ``text`` and ``image`` blocks. They show an empty result, drop the block,
print a raw struct, or fail the whole call ("Unsupported response item type: resource_link").

:func:`flatten` turns a ``tools/call`` result into one every client can read:

* ``structuredContent`` with no text block: its JSON is added as a text block, as the spec
  asks servers to do (structuredContent itself is kept);
* ``resource_link``: a text block naming the link (and, given its contents, the contents);
* embedded text resource: a text block with the resource's text;
* embedded blob resource: an ``image`` block for images, otherwise a short text note;
* ``audio``: a short text note.

``text`` and ``image`` blocks are never changed, and a result that needs nothing is returned
as the same object.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

PLAIN = ("text", "image")
DEFAULT_MAX_TEXT = 100_000  # characters of resource text put inline, per block


@dataclass
class Change:
    """One rewrite: ``index`` is the block's position in the original ``content`` (-1 for
    the text added from ``structuredContent``)."""

    index: int
    kind: str
    note: str

    def __str__(self) -> str:
        where = "structuredContent" if self.index < 0 else f"content[{self.index}]"
        return f"{where}: {self.kind} -> {self.note}"


@dataclass
class Flattened:
    result: Any
    changes: List[Change] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.changes)


# A reader gets a resource_link's uri and returns that resource's ``contents`` list
# (as in a resources/read result), or None when it can't be read.
Reader = Callable[[str], Optional[List[dict]]]


def needs_flattening(result: Any, *, keep: bool = False) -> bool:
    """True when :func:`flatten` would change ``result``."""
    return flatten(result, keep=keep).changed


def links(result: Any) -> List[str]:
    """The uris of the ``resource_link`` blocks in a tool result, in order."""
    content = result.get("content") if isinstance(result, dict) else None
    if not isinstance(content, list):
        return []
    return [b["uri"] for b in content
            if isinstance(b, dict) and b.get("type") == "resource_link"
            and isinstance(b.get("uri"), str)]


def flatten(result: Any, *, keep: bool = False, read: Optional[Reader] = None,
            max_text: int = DEFAULT_MAX_TEXT) -> Flattened:
    """Return ``result`` rewritten for text-only clients, and what was changed.

    ``keep=True`` leaves the original blocks in place and adds the text after them, for
    clients that only ignore unknown blocks (instead of failing on them). ``read`` is used to
    put the contents of ``resource_link`` targets inline; without it a link becomes a text
    line naming it. ``max_text`` caps the characters of resource text put inline per block.
    """
    if not isinstance(result, dict):
        return Flattened(result)
    content = result.get("content")
    if content is None:
        content = []
    if not isinstance(content, list):
        return Flattened(result)

    changes: List[Change] = []
    out: List[Any] = []
    for i, block in enumerate(content):
        kind = block.get("type") if isinstance(block, dict) else None
        if kind in PLAIN or not isinstance(block, dict) or not isinstance(kind, str):
            out.append(block)
            continue
        new = _convert(block, kind, read, max_text)
        if new is None:  # a type we don't know how to say better: leave it
            out.append(block)
            continue
        blocks, note = new
        if keep:
            out.append(block)
        out.extend(blocks)
        changes.append(Change(i, kind, note))

    structured = result.get("structuredContent")
    if structured is not None and not any(
            isinstance(b, dict) and b.get("type") == "text" for b in content):
        text = json.dumps(structured, ensure_ascii=False)
        out.append(_text(text))
        changes.append(Change(-1, "structuredContent", f"text block ({len(text)} chars)"))

    if not changes:
        return Flattened(result)
    return Flattened(dict(result, content=out), changes)


# -- block conversions -------------------------------------------------------------------

def _convert(block: dict, kind: str, read: Optional[Reader], max_text: int):
    if kind == "resource_link":
        return _link(block, read, max_text)
    if kind == "resource":
        res = block.get("resource")
        if not isinstance(res, dict):
            return None
        blocks = _contents([res], max_text)
        return blocks, _describe(blocks)
    if kind == "audio":
        size = _size(block.get("data"))
        return [_text(f"[audio: {block.get('mimeType') or 'unknown type'}"
                      f"{_bytes(size)}, not shown]")], "text note"
    return None


def _link(block: dict, read: Optional[Reader], max_text: int):
    uri = block.get("uri")
    if not isinstance(uri, str):
        return None
    head = _link_line(block)
    contents = None
    if read is not None:
        try:
            contents = read(uri)
        except Exception:  # a failed read must never lose the link itself
            contents = None
    if not contents:
        return [_text(head)], "text line"
    body = _contents(contents, max_text, default_uri=uri)
    if body and body[0].get("type") == "text":
        body[0] = _text(head + "\n\n" + body[0]["text"])
    else:
        body.insert(0, _text(head))
    return body, "inline contents" + _describe(body, prefix=": ")


def _link_line(block: dict) -> str:
    name = block.get("title") or block.get("name")
    line = f"Resource: {name} <{block['uri']}>" if name else f"Resource: <{block['uri']}>"
    extras = [str(x) for x in (block.get("mimeType"), _bytes(block.get("size")).strip(", "))
              if x]
    if extras:
        line += f" ({', '.join(extras)})"
    if isinstance(block.get("description"), str) and block["description"].strip():
        line += f"\n{block['description'].strip()}"
    return line


def _contents(contents: List[Any], max_text: int, default_uri: str = "") -> List[dict]:
    blocks: List[dict] = []
    for res in contents:
        if not isinstance(res, dict):
            continue
        uri = res.get("uri") or default_uri
        mime = res.get("mimeType")
        if isinstance(res.get("text"), str):
            text = res["text"]
            if len(text) > max_text:
                text = (text[:max_text] + f"\n[... {len(res['text']) - max_text} more "
                        f"characters of {uri} not shown]")
            # a link's single resource is already named by the line above it
            label = (f"{uri} ({mime})" if mime else uri) if uri else ""
            named = label and (not default_uri or len(contents) > 1)
            blocks.append(_text(f"{label}:\n{text}" if named else text))
        elif isinstance(res.get("blob"), str):
            if isinstance(mime, str) and mime.startswith("image/") and _valid_b64(res["blob"]):
                blocks.append({"type": "image", "data": res["blob"], "mimeType": mime})
            else:
                blocks.append(_text(f"[binary resource {uri or '(no uri)'}: "
                                    f"{mime or 'unknown type'}{_bytes(_size(res['blob']))}, "
                                    "not shown]"))
    return blocks


def _describe(blocks: List[dict], prefix: str = "") -> str:
    if not blocks:
        return prefix + "nothing"
    kinds = [b.get("type", "?") for b in blocks]
    return prefix + " + ".join(f"{k} block" for k in kinds)


def _text(text: str) -> Dict[str, str]:
    return {"type": "text", "text": text}


def _size(data: Any) -> Optional[int]:
    if not isinstance(data, str):
        return None
    pad = data.count("=", max(0, len(data) - 2))
    return max(0, len(data) * 3 // 4 - pad)


def _bytes(size: Any) -> str:
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        return ""
    return f", {size:,} bytes"


def _valid_b64(data: str) -> bool:
    try:
        base64.b64decode(data, validate=True)
        return True
    except (binascii.Error, ValueError):
        return False
