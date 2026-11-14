"""Run a stdio MCP server and let only JSON-RPC messages through on its stdout."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import threading
from typing import BinaryIO, List, Optional

from .core import split

NAME = "cleanpipe"


def resolve(command: List[str]) -> List[str]:
    """The command with its program found on PATH (finds npx.cmd and friends on Windows)."""
    if not command:
        raise ValueError("no server command given")
    exe = shutil.which(command[0])
    return [exe or command[0]] + list(command[1:])


class Pipe:
    """Starts ``command`` and relays stdin and stdout, moving noise off stdout.

    Client to server bytes are copied unchanged. Server stdout is read line by line: JSON-RPC
    messages go to ``stdout``; everything else goes to ``noise`` (stderr by default), or is
    dropped when ``noise`` is None. The server's own stderr is left alone.
    """

    def __init__(self, command: List[str], *, noise: Optional[BinaryIO] = None,
                 drop: bool = False, stdin: Optional[BinaryIO] = None,
                 stdout: Optional[BinaryIO] = None, quiet: bool = False) -> None:
        self.command = resolve(command)
        self.stdin = stdin if stdin is not None else sys.stdin.buffer
        self.stdout = stdout if stdout is not None else sys.stdout.buffer
        self.noise = None if drop else (noise if noise is not None else sys.stderr.buffer)
        self.quiet = quiet
        self.moved = 0
        self.proc: Optional[subprocess.Popen] = None

    def _say(self, text: str) -> None:
        if not self.quiet:
            try:
                sys.stderr.write(f"{NAME}: {text}\n")
                sys.stderr.flush()
            except (OSError, ValueError):
                pass

    def _put(self, stream: Optional[BinaryIO], data: bytes) -> None:
        if stream is None:
            return
        try:
            stream.write(data + b"\n")
            stream.flush()
        except (OSError, ValueError):
            pass

    def _pump_out(self) -> None:
        assert self.proc and self.proc.stdout
        for line in iter(self.proc.stdout.readline, b""):
            messages, junk = split(line)
            for part in junk:
                if self.moved == 0:
                    where = "dropping it" if self.noise is None else "moving it to stderr"
                    self._say(f"the server wrote non-JSON-RPC output to stdout; {where}")
                self.moved += 1
                self._put(self.noise, part)
            for msg in messages:
                self._put(self.stdout, msg)

    def _pump_in(self) -> None:
        assert self.proc and self.proc.stdin
        try:
            # Read the file descriptor directly: a thread blocked inside a buffered reader
            # holds its lock, and Python aborts at exit if a daemon thread holds that lock.
            fd = self.stdin.fileno()
            read = lambda n: os.read(fd, n)  # noqa: E731
        except (AttributeError, OSError, ValueError):
            read = getattr(self.stdin, "read1", self.stdin.read)
        try:
            while True:
                chunk = read(65536)
                if not chunk:
                    break
                self.proc.stdin.write(chunk)
                self.proc.stdin.flush()
        except (OSError, ValueError):
            pass
        try:
            self.proc.stdin.close()
        except (OSError, ValueError):
            pass

    def run(self) -> int:
        try:
            self.proc = subprocess.Popen(self.command, stdin=subprocess.PIPE,
                                         stdout=subprocess.PIPE, bufsize=0)
        except OSError as exc:
            self._say(f"can't start {self.command[0]}: {exc}")
            return 127
        out = threading.Thread(target=self._pump_out, daemon=True)
        out.start()
        threading.Thread(target=self._pump_in, daemon=True).start()

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
            out.join(timeout=5)
        finally:
            if previous is not None:
                signal.signal(signal.SIGTERM, previous)
        if self.moved:
            where = "dropped" if self.noise is None else "moved to stderr"
            self._say(f"{self.moved} non-JSON-RPC stdout line(s) {where}; "
                      "the server should log to stderr (worth reporting upstream)")
        return 128 - code if code < 0 else code


def run(command: List[str], **kwargs) -> int:
    """Run ``command`` behind a :class:`Pipe` and return its exit code."""
    return Pipe(command, **kwargs).run()

