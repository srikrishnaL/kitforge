"""A stdio relay between an MCP client and an MCP server, with hooks to rewrite messages.

MCP over stdio is one JSON-RPC message (or batch) per line. The relay starts the server,
copies lines both ways, and gives each parsed message to a hook that may change it, drop it,
or answer it directly. Lines that are not changed are passed on byte for byte.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import threading
from typing import Any, BinaryIO, Callable, Iterator, List, Optional, Tuple

# A hook gets one message and returns (message to pass on or None, replies to send back).
Hook = Callable[[dict], Tuple[Optional[dict], List[dict]]]


def _dump(msg: Any) -> bytes:
    return json.dumps(msg, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"


def _lines(stream: BinaryIO) -> Iterator[bytes]:
    """Lines from ``stream``, read from its file descriptor when it has one.

    A thread blocked inside a buffered reader holds the reader's lock, and Python aborts at
    exit if a daemon thread still holds it; ``os.read`` takes no lock.
    """
    try:
        fd = stream.fileno()
        read: Callable[[int], bytes] = lambda n: os.read(fd, n)  # noqa: E731
    except (AttributeError, OSError, ValueError):
        read = getattr(stream, "read1", stream.read)
    buf = b""
    while True:
        try:
            chunk = read(65536)
        except (OSError, ValueError):
            break
        if not chunk:
            break
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            yield line + b"\n"
    if buf:
        yield buf


class Relay:
    def __init__(self, command: List[str], *, to_server: Hook, to_client: Hook,
                 stdin: Optional[BinaryIO] = None, stdout: Optional[BinaryIO] = None,
                 name: str = "proxy") -> None:
        if not command:
            raise ValueError("no server command given")
        exe = shutil.which(command[0])  # finds npx.cmd and friends on Windows
        self.command = [exe or command[0]] + list(command[1:])
        self.name = name
        self.to_server = to_server
        self.to_client = to_client
        self.stdin = stdin or sys.stdin.buffer
        self.stdout = stdout or sys.stdout.buffer
        self._lock = threading.Lock()
        self.proc: Optional[subprocess.Popen] = None

    def _write(self, stream: BinaryIO, data: bytes) -> None:
        with self._lock:
            try:
                stream.write(data)
                stream.flush()
            except (BrokenPipeError, OSError, ValueError):
                pass

    def _handle(self, line: bytes, hook: Hook, forward: BinaryIO, back: BinaryIO) -> None:
        line = line if line.endswith(b"\n") else line + b"\n"
        try:
            msg = json.loads(line)
        except ValueError:
            self._write(forward, line)
            return
        batch = isinstance(msg, list)
        out: List[Any] = []
        replies: List[dict] = []
        changed = False
        for item in (msg if batch else [msg]):
            if not isinstance(item, dict):
                out.append(item)
                continue
            try:
                new, extra = hook(item)
            except Exception as exc:  # never break the connection over a rewrite
                print(f"{self.name}: {type(exc).__name__}: {exc}", file=sys.stderr)
                new, extra = item, []
            if new is not item:
                changed = True
            if new is not None:
                out.append(new)
            replies += extra
        if out:
            self._write(forward, _dump(out if batch else out[0]) if changed else line)
        for reply in replies:
            self._write(back, _dump(reply))

    def _pump_server(self) -> None:
        assert self.proc and self.proc.stdout and self.proc.stdin
        for line in _lines(self.proc.stdout):
            if line.strip():
                self._handle(line, self.to_client, self.stdout, self.proc.stdin)

    def _pump_client(self) -> None:
        assert self.proc and self.proc.stdin
        for line in _lines(self.stdin):
            if line.strip():
                self._handle(line, self.to_server, self.proc.stdin, self.stdout)
        try:
            self.proc.stdin.close()
        except OSError:
            pass

    def run(self) -> int:
        try:
            self.proc = subprocess.Popen(self.command, stdin=subprocess.PIPE,
                                         stdout=subprocess.PIPE, bufsize=0)
        except OSError as exc:
            print(f"{self.name}: can't start {self.command[0]}: {exc}", file=sys.stderr)
            return 127
        pump = threading.Thread(target=self._pump_server, daemon=True)
        pump.start()
        threading.Thread(target=self._pump_client, daemon=True).start()

        previous = None
        if hasattr(signal, "SIGTERM") and threading.current_thread() is threading.main_thread():
            def forward(signum, frame):  # pass a shutdown request on to the server
                if self.proc and self.proc.poll() is None:
                    self.proc.terminate()
            previous = signal.signal(signal.SIGTERM, forward)
        try:
            try:
                code = self.proc.wait()
            except KeyboardInterrupt:
                self.proc.terminate()
                code = self.proc.wait()
            pump.join(timeout=5)
        finally:
            if previous is not None:
                signal.signal(signal.SIGTERM, previous)
        return 128 - code if code < 0 else code
