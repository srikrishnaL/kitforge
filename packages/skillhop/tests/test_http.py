import base64
import json

import pytest

from fakeskills import serve_http
from skillhop.install import LOCK_NAME
from skillhop.rpc import header_value


@pytest.fixture
def http(skills_root):
    servers = []

    def start(env=None, sse=False):
        url, server, seen = serve_http(str(skills_root), env or {}, sse=sse)
        servers.append(server)
        return url, seen

    yield start
    for s in servers:
        s.shutdown()
        s.server_close()


@pytest.mark.parametrize("sse", [False, True])
def test_install_over_http(run, http, tmp_path, sse):
    url, seen = http(sse=sse)
    code, out, err = run("add", url, "pdf-processing", "--dir", tmp_path / "s", "--yes",
                         "-H", "Authorization: Bearer ${SKILLHOP_TEST_TOKEN:-t0ken}")
    assert code == 0, err
    assert (tmp_path / "s" / "pdf-processing" / "assets" / "logo.png").read_bytes() == \
        b"\x89PNG\r\n\x1a\n\x00\xff\xfe"
    first = seen[0]["headers"]
    assert seen[0]["body"]["method"] == "server/discover"
    assert first["mcp-protocol-version"] == "2026-07-28"
    assert first["mcp-method"] == "server/discover"
    assert first["authorization"] == "Bearer t0ken"
    assert "application/json" in first["accept"] and "text/event-stream" in first["accept"]
    reads = [s for s in seen if s["body"]["method"] == "resources/read"]
    assert reads and all(r["headers"]["mcp-name"] == r["body"]["params"]["uri"] for r in reads)
    # the header template is saved, not the expanded token
    lock = json.loads((tmp_path / "s" / LOCK_NAME).read_text())
    assert lock["skills"]["pdf-processing"]["server"]["headers"] == \
        {"Authorization": "Bearer ${SKILLHOP_TEST_TOKEN:-t0ken}"}
    assert lock["skills"]["pdf-processing"]["server"]["label"] == "127.0.0.1"


def test_legacy_http_server_with_session(run, http, tmp_path):
    url, seen = http({"FAKE_MODE": "legacy"})
    code, out, err = run("add", url, "git-workflow", "--dir", tmp_path / "s", "--yes")
    assert code == 0, err
    methods = [s["body"]["method"] for s in seen]
    assert methods[:3] == ["server/discover", "initialize", "notifications/initialized"]
    later = [s for s in seen if s["body"]["method"] == "skills/list"]
    assert later[0]["headers"]["mcp-session-id"] == "sess-1"
    assert "_meta" not in later[0]["body"]["params"]


def test_unsupported_version_over_http_retries(run, http, tmp_path):
    url, seen = http({"FAKE_MODE": "picky"})
    code, out, err = run("ls", url)
    assert code == 0, err
    assert "initialize" not in [s["body"]["method"] for s in seen]
    assert seen[-1]["headers"]["mcp-protocol-version"] == "2099-01-01"


def test_unreachable_server(run):
    code, _, err = run("ls", "http://127.0.0.1:9/mcp", "--timeout", "2")
    assert code == 2 and "cannot reach" in err


def test_header_value_encoding():
    assert header_value("skill://pdf-processing/SKILL.md") == "skill://pdf-processing/SKILL.md"
    enc = header_value("skill://docs/résumé/SKILL.md")
    assert enc.startswith("=?base64?") and enc.endswith("?=")
    assert base64.b64decode(enc[9:-2]).decode() == "skill://docs/résumé/SKILL.md"
    assert header_value(" padded ").startswith("=?base64?")
    assert header_value("=?base64?literal?=").startswith("=?base64?PT9i")
