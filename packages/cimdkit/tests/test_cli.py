import json
import subprocess
import sys

from cimdkit.cli import main

from conftest import CID, good_doc


def write(tmp_path, doc):
    p = tmp_path / "client.json"
    p.write_text(json.dumps(doc))
    return str(p)


def test_check_file_ok(tmp_path, capsys):
    assert main(["check", CID, "--file", write(tmp_path, good_doc())]) == 0
    out = capsys.readouterr().out
    assert out.startswith("OK")
    assert "Example Agent" in out


def test_check_redirect_uri(tmp_path, capsys):
    path = write(tmp_path, good_doc())
    assert main(["check", CID, "--file", path, "--redirect-uri",
                 "http://127.0.0.1:50000/callback"]) == 0
    capsys.readouterr()
    assert main(["check", CID, "--file", path, "--redirect-uri",
                 "http://localhost:50000/callback", "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["rejected_redirect_uris"] == ["http://localhost:50000/callback"]


def test_check_json_output(tmp_path, capsys):
    assert main(["check", CID, "--file", write(tmp_path, good_doc()), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] and data["token_endpoint_auth_method"] == "none"


def test_check_fails_with_reason(tmp_path, capsys):
    doc = good_doc(client_id="https://app.example.com/other")
    assert main(["check", CID, "--file", write(tmp_path, doc)]) == 1
    assert "does not exactly match" in capsys.readouterr().err


def test_check_fails_json(tmp_path, capsys):
    assert main(["check", CID, "--file", write(tmp_path, good_doc(client_secret="x")),
                 "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data == {"ok": False, "kind": "invalid_client",
                    "error": "client_secret must not appear in a public metadata document"}


def test_auth_method_option(tmp_path):
    assert main(["check", CID, "--file", write(tmp_path, good_doc()),
                 "--auth-method", "private_key_jwt"]) == 1


def test_blocked_address_without_network(capsys):
    assert main(["check", "https://127.0.0.1/client.json"]) == 1
    assert "special-use" in capsys.readouterr().err


def test_missing_file(capsys):
    assert main(["check", CID, "--file", "/nonexistent/x.json"]) == 1


def test_module_entry_point():
    res = subprocess.run([sys.executable, "-m", "cimdkit", "--version"],
                         capture_output=True, text=True)
    assert res.returncode == 0 and "cimdkit" in res.stdout
