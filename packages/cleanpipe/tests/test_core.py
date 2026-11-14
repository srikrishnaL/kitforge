import json
import time

import pytest

from cleanpipe import is_message, split

REQ = b'{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
RES = b'{"jsonrpc": "2.0", "id": 1, "result": {"tools": []}}'


def test_clean_line_passes_byte_for_byte():
    assert split(RES + b"\n") == ([RES], [])
    assert split(REQ + b"\r\n") == ([REQ], [])


@pytest.mark.parametrize("line", [
    b"Server started on stdio\n",
    b"[INFO] connected\n",                       # starts with '[' but is not a batch
    b'{"level": "info", "event": "startup"}\n',  # structured log: JSON, not JSON-RPC
    b'{"jsonrpc": "1.0", "id": 1, "result": 1}\n',
    b'{"jsonrpc": "2.0"}\n',
    b"[]\n",
    b"[1, 2]\n",
    b"\x1b[32mINFO\x1b[0m ready\n",
    b"bad \xff\xfe bytes\n",
])
def test_noise_lines(line):
    messages, noise = split(line)
    assert messages == []
    assert noise == [line.rstrip(b"\r\n")]


def test_blank_lines_are_dropped():
    assert split(b"\n") == ([], [])
    assert split(b"   \r\n") == ([], [])


def test_log_printed_without_newline_before_a_message():
    assert split(b"Loading model... " + RES + b"\n") == ([RES], [b"Loading model..."])


def test_text_after_a_message():
    assert split(RES + b" done.\n") == ([RES], [b"done."])


def test_two_messages_on_one_line():
    assert split(RES + REQ + b"\n") == ([RES, REQ], [])


def test_batch_is_a_message():
    batch = b"[" + REQ + b", " + RES + b"]"
    assert split(batch + b"\n") == ([batch], [])
    assert split(b"x " + batch + b"\n") == ([batch], [b"x"])


def test_bom_is_removed():
    assert split(b"\xef\xbb\xbf" + RES + b"\n") == ([RES], [])


def test_message_inside_a_log_object_is_not_forwarded():
    line = b'{"level": "debug", "sent": ' + RES + b"}\n"
    assert split(line) == ([], [line.rstrip()])


def test_message_with_non_ascii_text_is_kept_exactly():
    msg = '{"jsonrpc":"2.0","id":2,"result":{"text":"naïve ✓"}}'.encode()
    assert split(b"\xff junk " + msg + b"\n") == ([msg], [b"\xff junk"])


def test_notifications_and_errors_are_messages():
    assert is_message({"jsonrpc": "2.0", "method": "notifications/progress"})
    assert is_message({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "x"}})
    assert not is_message({"jsonrpc": "2.0", "id": 1})
    assert not is_message({"jsonrpc": "2.0", "method": 5})
    assert not is_message([{"jsonrpc": "2.0", "method": "a"}, {"level": "info"}])


def test_brace_heavy_noise_stays_fast():
    line = b'{"a' * 200_000 + b"\n"
    start = time.monotonic()
    messages, noise = split(line)
    assert messages == [] and noise == [line.rstrip()]
    assert time.monotonic() - start < 2


def test_big_message_is_fast():
    msg = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"text": "x" * 5_000_000}}).encode()
    start = time.monotonic()
    assert split(msg + b"\n") == ([msg], [])
    assert time.monotonic() - start < 2
