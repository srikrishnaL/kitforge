"""Tell JSON-RPC messages apart from everything else a server writes to stdout.

MCP over stdio sends one JSON-RPC message (or batch) per line. A server that also prints a
banner, a log line or a progress dot on stdout breaks that stream. ``split`` takes one line
of stdout and returns the JSON-RPC messages in it and the rest (the "noise"), so the messages
can go to the client and the noise somewhere harmless.
"""

from __future__ import annotations

import json
import re
from typing import Any, List, Tuple

_DECODER = json.JSONDecoder()
_BOM = "\ufeff"
# Places where a message can start inside a noisy line: '{' then a key, or '[' then '{'.
_START = re.compile(r'\{\s*"|\[\s*\{')
# Give up looking for messages in a line after this many false starts (keeps a huge line of
# brace-heavy noise from costing quadratic time); the rest of the line is noise.
MAX_ATTEMPTS = 200


def is_message(obj: Any) -> bool:
    """True for a JSON-RPC 2.0 request, notification, response or error, or a batch of them."""
    if isinstance(obj, list):
        return bool(obj) and all(_is_single(item) for item in obj)
    return _is_single(obj)


def _is_single(obj: Any) -> bool:
    if not isinstance(obj, dict) or obj.get("jsonrpc") != "2.0":
        return False
    if isinstance(obj.get("method"), str):
        return True
    return "id" in obj and ("result" in obj or "error" in obj)


def _encode(text: str) -> bytes:
    return text.encode("utf-8", "surrogateescape")


def split(line: bytes) -> Tuple[List[bytes], List[bytes]]:
    """Split one line of a server's stdout into (messages, noise).

    Each message is returned byte for byte as the server wrote it, without the line ending.
    A line that is exactly one message (the normal case) is checked with one ``json.loads``.
    Otherwise every message inside the line is kept (a log line printed without a newline
    right before a message, two messages on one line) and the text around them is noise.
    Blank lines are dropped. JSON that is not JSON-RPC 2.0, such as a structured log line, is
    noise.
    """
    raw = line.rstrip(b"\r\n")
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    if not raw.strip():
        return [], []
    try:
        if is_message(json.loads(raw)):
            return [raw.strip()], []
    except ValueError:
        pass

    text = raw.decode("utf-8", "surrogateescape").replace(_BOM, "")
    messages: List[bytes] = []
    noise: List[str] = []
    pos = 0  # start of text not yet assigned to a message or to noise
    search = 0
    misses = 0
    while misses < MAX_ATTEMPTS:
        found = _START.search(text, search)
        if not found:
            break
        start = found.start()
        try:
            obj, end = _DECODER.raw_decode(text, start)
        except ValueError:
            misses += 1
            search = start + 1
            continue
        if is_message(obj):
            noise.append(text[pos:start])
            messages.append(_encode(text[start:end]))
            pos = end
        else:
            misses += 1
        # Skip over whatever JSON was there: an object inside a log line is not a message.
        search = end
    noise.append(text[pos:])
    junk = [_encode(part.strip()) for part in noise if part.strip()]
    return messages, junk
