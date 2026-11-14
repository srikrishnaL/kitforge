"""Start a stdio MCP server, do the handshake, and report anything on stdout that isn't JSON-RPC."""

from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import List, Optional

from .core import split
from .pipe import resolve

PROTOCOL_VERSION = "2025-06-18"


@dataclass
class Report:
    noise: List[str] = field(default_factory=list)  # stdout lines (or parts) that aren't JSON-RPC
    handshake: bool = False  # the server answered initialize
    tools: Optional[int] = None  # number of tools, if tools/list was answered
    exit_code: Optional[int] = None  # set if the server exited during the check
    error: str = ""

    @property
    def clean(self) -> bool:
        return self.handshake and not self.noise and not self.error


def _request(id_: int, method: str, params: Optional[dict] = None) -> bytes:
    msg = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        msg["params"] = params
    return json.dumps(msg).encode() + b"\n"


def check(command: List[str], timeout: float = 10.0, settle: float = 0.5) -> Report:
    """Run the handshake and tools/list against ``command``; collect stdout noise.

    ``timeout`` bounds each reply; ``settle`` is how long to keep listening after the last
    reply, to catch output a server prints once it is up.
    """
    report = Report()
    try:
        proc = subprocess.Popen(resolve(command), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, bufsize=0)
    except (OSError, ValueError) as exc:
        report.error = f"can't start the server: {exc}"
        return report

    lines: "queue.Queue[Optional[bytes]]" = queue.Queue()

    def reader() -> None:
        assert proc.stdout
        for line in iter(proc.stdout.readline, b""):
            lines.put(line)
        lines.put(None)

    threading.Thread(target=reader, daemon=True).start()

    def send(data: bytes) -> bool:
        try:
            assert proc.stdin
            proc.stdin.write(data)
            proc.stdin.flush()
            return True
        except (OSError, ValueError):
            return False

    def wait_for(id_: int, limit: float) -> Optional[dict]:
        deadline = time.monotonic() + limit
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                return None
            try:
                line = lines.get(timeout=left)
            except queue.Empty:
                return None
            if line is None:
                report.exit_code = proc.wait()
                return None
            messages, junk = split(line)
            report.noise += [j.decode("utf-8", "replace") for j in junk]
            for raw in messages:
                msg = json.loads(raw)
                for item in (msg if isinstance(msg, list) else [msg]):
                    if item.get("id") == id_ and "method" not in item:
                        return item

    try:
        init = {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                "clientInfo": {"name": "cleanpipe-check", "version": "1"}}
        if send(_request(1, "initialize", init)):
            reply = wait_for(1, timeout)
            if reply is not None and "result" in reply:
                report.handshake = True
                send(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}).encode() + b"\n")
                if (reply["result"].get("capabilities") or {}).get("tools") is not None:
                    send(_request(2, "tools/list", {}))
                    tools = wait_for(2, timeout)
                    if tools is not None and "result" in tools:
                        report.tools = len(tools["result"].get("tools") or [])
            elif reply is not None:
                report.error = f"initialize failed: {reply.get('error')}"
        if not report.handshake and not report.error:
            if report.exit_code is not None:
                report.error = f"the server exited with code {report.exit_code} before answering initialize"
            else:
                report.error = f"no answer to initialize within {timeout:g}s"
        if report.exit_code is None:
            wait_for(-1, settle)  # listen a little longer for late output
    finally:
        if proc.poll() is None:
            try:
                assert proc.stdin
                proc.stdin.close()
            except OSError:
                pass
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
    return report
