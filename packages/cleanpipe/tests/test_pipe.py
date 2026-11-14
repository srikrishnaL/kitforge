import json
import subprocess
import sys
from pathlib import Path

import pytest

SERVER = str(Path(__file__).parent / "servers" / "noisy_server.py")


class Session:
    """cleanpipe around the noisy test server, driven one request at a time."""

    def __init__(self, *flags, server_flags=()):
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "cleanpipe", *flags, "--", sys.executable, SERVER, *server_flags],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.next_id = 0

    def send(self, method, params=None):
        self.next_id += 1
        msg = {"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params or {}}
        self.proc.stdin.write((json.dumps(msg) + "\n").encode())
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        reply = json.loads(line)  # every stdout line must be JSON-RPC
        assert reply["jsonrpc"] == "2.0" and reply["id"] == self.next_id
        return reply

    def handshake(self):
        result = self.send("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                          "clientInfo": {"name": "t", "version": "1"}})["result"]
        self.proc.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        self.proc.stdin.flush()
        return result

    def close(self):
        self.proc.stdin.close()
        code = self.proc.wait(timeout=30)
        rest = self.proc.stdout.read()
        return code, rest, self.proc.stderr.read()


@pytest.fixture
def make():
    sessions = []

    def factory(*flags, server_flags=()):
        s = Session(*flags, server_flags=server_flags)
        sessions.append(s)
        return s

    yield factory
    for s in sessions:
        if s.proc.poll() is None:
            s.proc.kill()


def test_only_json_rpc_reaches_the_client(make):
    s = make()
    assert s.handshake()["serverInfo"]["name"] == "noisy"
    assert s.send("tools/list")["result"]["tools"][0]["name"] == "echo"
    reply = s.send("tools/call", {"name": "echo", "arguments": {"text": "hi ✓"}})
    assert reply["result"]["content"][0]["text"] == "hi ✓"
    code, rest, err = s.close()
    assert code == 0 and rest == b""
    assert b"Starting noisy-server v1.0 on stdio" in err
    assert b'{"event": "startup", "level": "info", "tools": {"count": 1}}' in err
    assert b"[INFO] client connected" in err
    assert b"calling echo..." in err
    assert b"bad bytes \xff\xfe here" in err
    assert b"cleanpipe: the server wrote non-JSON-RPC output to stdout; moving it to stderr" in err
    assert b"cleanpipe: 6 non-JSON-RPC stdout line(s) moved to stderr" in err


def test_clean_server_gets_no_notes(make):
    s = make(server_flags=["--clean"])
    s.handshake()
    s.send("tools/list")
    code, rest, err = s.close()
    assert code == 0 and rest == b"" and b"cleanpipe" not in err


def test_log_file(make, tmp_path):
    log = tmp_path / "noise.log"
    s = make("--log", str(log), "--quiet")
    s.handshake()
    code, _, err = s.close()
    assert code == 0 and err == b""
    lines = log.read_bytes().splitlines()
    assert lines[0] == b"Starting noisy-server v1.0 on stdio"
    assert b"[INFO] client connected" in lines


def test_drop(make):
    s = make("--drop")
    s.handshake()
    code, _, err = s.close()
    assert code == 0
    assert b"Starting noisy" not in err
    assert b"line(s) dropped" in err


def test_exit_code_is_passed_on(make):
    s = make(server_flags=["--exit", "3"])
    s.handshake()
    code, _, _ = s.close()
    assert code == 3


def test_server_crash_ends_the_pipe(make):
    s = make(server_flags=["--crash", "5"])
    s.handshake()
    assert s.proc.wait(timeout=30) == 5


def test_missing_command():
    res = subprocess.run([sys.executable, "-m", "cleanpipe", "--", "no-such-command-xyz"],
                         capture_output=True, input=b"", timeout=30)
    assert res.returncode == 127
    assert b"can't start no-such-command-xyz" in res.stderr


def test_no_command_is_a_usage_error():
    res = subprocess.run([sys.executable, "-m", "cleanpipe"], capture_output=True, timeout=30)
    assert res.returncode == 2 and b"give the server command after --" in res.stderr


def test_command_without_double_dash(make):
    proc = subprocess.run([sys.executable, "-m", "cleanpipe", "--quiet", sys.executable, SERVER, "--clean"],
                          input=b'{"jsonrpc":"2.0","id":7,"method":"nope"}\n', capture_output=True,
                          timeout=30)
    assert json.loads(proc.stdout)["id"] == 7 and proc.returncode == 0
