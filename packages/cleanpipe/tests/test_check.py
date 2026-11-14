import json
import subprocess
import sys
from pathlib import Path

from cleanpipe import check

SERVER = str(Path(__file__).parent / "servers" / "noisy_server.py")


def test_noisy_server_is_reported():
    report = check([sys.executable, SERVER])
    assert report.handshake and report.tools == 1 and not report.clean
    assert report.noise[0] == "Starting noisy-server v1.0 on stdio"
    assert "[INFO] client connected" in report.noise  # printed after the handshake


def test_clean_server():
    report = check([sys.executable, SERVER, "--clean"])
    assert report.clean and report.tools == 1 and report.noise == []


def test_server_that_never_answers():
    report = check([sys.executable, SERVER, "--silent", "--clean"], timeout=1)
    assert not report.handshake and "no answer to initialize within 1s" in report.error


def test_server_that_exits_early():
    report = check([sys.executable, "-c", "print('oops'); raise SystemExit(4)"], timeout=5)
    assert report.exit_code == 4 and report.noise == ["oops"]
    assert "exited with code 4" in report.error


def test_missing_command():
    assert "can't start the server" in check(["no-such-command-xyz"]).error


def run_cli(*args):
    return subprocess.run([sys.executable, "-m", "cleanpipe", "check", *args],
                          capture_output=True, text=True, timeout=60)


def test_cli_exit_codes_and_output():
    noisy = run_cli("--", sys.executable, SERVER)
    assert noisy.returncode == 1
    assert "handshake: ok, 1 tool(s)" in noisy.stdout
    assert "stdout noise: 5 line(s)" in noisy.stdout
    assert "  | Starting noisy-server v1.0 on stdio" in noisy.stdout

    clean = run_cli("--", sys.executable, SERVER, "--clean")
    assert clean.returncode == 0 and "stdout: clean" in clean.stdout

    silent = run_cli("--timeout", "1", "--", sys.executable, SERVER, "--silent")
    assert silent.returncode == 2 and "error: no answer" in silent.stdout


def test_cli_json():
    res = run_cli("--json", "--", sys.executable, SERVER)
    doc = json.loads(res.stdout)
    assert doc["clean"] is False and doc["handshake"] is True and doc["tools"] == 1
    assert len(doc["noise"]) == 5
