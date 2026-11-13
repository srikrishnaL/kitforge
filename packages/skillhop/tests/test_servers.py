import json

import pytest

from skillhop.servers import (ServerNotFound, ServerSpec, all_configured, expand, find_configured,
                              label_for_url, resolve)


def test_expand():
    env = {"A": "1"}
    assert expand("x-${A}-${B:-two}", env) == "x-1-two"
    with pytest.raises(ServerNotFound, match="B is not set"):
        expand("${B}", env)


def test_finds_servers_in_client_configs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SKILLHOP_HOME", str(tmp_path / "home"))
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {
        "docs": {"type": "http", "url": "https://docs.example.com/mcp",
                 "headers": {"Authorization": "Bearer ${DOCS_TOKEN}"}}}}))
    home = tmp_path / "home"
    (home / ".cursor").mkdir(parents=True)
    (home / ".cursor" / "mcp.json").write_text(json.dumps({"mcpServers": {
        "local": {"command": "npx", "args": ["-y", "@acme/mcp"], "env": {"K": "v"}},
        "docs": {"url": "https://shadowed.example.com"}}}))
    (home / ".claude.json").write_text(json.dumps({"projects": {str(tmp_path): {"mcpServers": {
        "proj": {"command": "uvx", "args": ["proj-mcp"]}}}}}))
    (tmp_path / ".vscode").mkdir()
    (tmp_path / ".vscode" / "mcp.json").write_text(json.dumps({"servers": {"old": {"type": "sse",
                                                                              "url": "https://x/sse"}}}))
    docs = find_configured("docs")
    assert docs.url == "https://docs.example.com/mcp" and docs.headers["Authorization"] == "Bearer ${DOCS_TOKEN}"
    assert docs.save() == {"label": "docs", "config": "docs"}  # nothing secret on disk
    local = find_configured("local")
    assert local.command == ["npx", "-y", "@acme/mcp"] and local.env == {"K": "v"}
    assert find_configured("proj").command == ["uvx", "proj-mcp"]
    with pytest.raises(ServerNotFound, match="old SSE transport"):
        find_configured("old")
    with pytest.raises(ServerNotFound, match="skillhop servers"):
        find_configured("nope")
    names = [n for n, _, _ in all_configured()]
    assert names.count("docs") == 1 and {"docs", "local", "proj", "old"} <= set(names)


def test_resolve():
    assert resolve("https://mcp.example.com/mcp", None, {}, {}).label == "mcp.example.com"
    spec = resolve(None, "npx -y '@acme/mcp server'", {}, {"A": "b"})
    assert spec.command == ["npx", "-y", "@acme/mcp server"] and spec.env == {"A": "b"}
    with pytest.raises(ServerNotFound):
        resolve(None, None, {}, {})
    assert label_for_url("https://a.b/c") == "a.b"


def test_saved_spec_round_trip_needs_secrets_again():
    spec = ServerSpec(label="x", url="https://x/mcp", headers={"Authorization": "Bearer abc",
                                                               "X-Org": "${ORG}"})
    saved = spec.save()
    assert saved["headers"] == {"Authorization": None, "X-Org": "${ORG}"}
    with pytest.raises(ServerNotFound, match="Authorization"):
        ServerSpec.load(saved)
    again = ServerSpec.load(saved, headers={"Authorization": "Bearer abc"})
    assert again.headers == {"Authorization": "Bearer abc", "X-Org": "${ORG}"}
