"""Where a server comes from: a URL, a command, or a name in an MCP client's config."""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .rpc import Client, HttpTransport, StdioTransport

_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


class ServerNotFound(Exception):
    pass


def expand(value: str, env: Optional[Dict[str, str]] = None) -> str:
    """Expand ``${VAR}`` and ``${VAR:-default}`` the way Claude Code and Cursor configs do."""
    env = os.environ if env is None else env

    def sub(m: "re.Match[str]") -> str:
        if m.group(1) in env:
            return env[m.group(1)]
        if m.group(2) is not None:
            return m.group(2)
        raise ServerNotFound(f"environment variable {m.group(1)} is not set (used as ${{{m.group(1)}}})")

    return _VAR.sub(sub, value)


@dataclass
class ServerSpec:
    """How to reach a server. ``save()`` keeps it in the lock file without secrets."""

    label: str
    url: Optional[str] = None
    command: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    headers: Dict[str, str] = field(default_factory=dict)
    config_name: Optional[str] = None  # found in a client config: look it up again later

    def connect(self, timeout: float = 30.0, show_stderr: bool = False) -> Client:
        if self.url:
            transport: Any = HttpTransport(expand(self.url),
                                           {k: expand(v) for k, v in self.headers.items()}, timeout)
        else:
            transport = StdioTransport([expand(a) for a in self.command],
                                       {k: expand(v) for k, v in self.env.items()},
                                       timeout=timeout, show_stderr=show_stderr)
        client = Client(transport, self.label)
        try:
            client.handshake()
        except BaseException:
            client.close()
            raise
        return client

    def save(self) -> Dict[str, Any]:
        """A form safe to write to disk: only ${VAR} templates, never literal secret values."""
        if self.config_name:
            return {"label": self.label, "config": self.config_name}
        out: Dict[str, Any] = {"label": self.label}
        if self.url:
            out["url"] = self.url
            out["headers"] = {k: (v if _VAR.search(v) else None) for k, v in self.headers.items()}
        else:
            out["command"] = self.command
            out["env"] = {k: (v if _VAR.search(v) else None) for k, v in self.env.items()}
        return out

    @classmethod
    def load(cls, data: Dict[str, Any], headers: Optional[Dict[str, str]] = None,
             env: Optional[Dict[str, str]] = None, search: Optional[Iterable[Path]] = None) -> "ServerSpec":
        if data.get("config"):
            spec = find_configured(data["config"], search)
            spec.label = data.get("label") or spec.label
            return spec
        given_h, given_e = headers or {}, env or {}
        h = {k: (given_h.get(k) if v is None else v) for k, v in (data.get("headers") or {}).items()}
        e = {k: (given_e.get(k) if v is None else v) for k, v in (data.get("env") or {}).items()}
        h.update(given_h)
        e.update(given_e)
        missing = [k for k, v in {**h, **e}.items() if v is None]
        if missing:
            raise ServerNotFound(
                f"server {data.get('label')!r} needs {', '.join(missing)}, which skillhop did not save "
                "because it looked like a secret; pass it again with --header/--env, or use "
                "${VAR} in the value so it is read from the environment")
        return cls(label=data.get("label") or "server", url=data.get("url"),
                   command=list(data.get("command") or []), headers=h, env=e)


def label_for_url(url: str) -> str:
    host = urllib.parse.urlsplit(url).hostname or "server"
    return host


_LAUNCHERS = {"npx", "uvx", "uv", "run", "tool", "bunx", "bun", "pnpm", "dlx", "yarn", "deno", "node",
              "python", "python3", "py", "pipx", "docker", "podman", "exec", "cmd", "sh", "bash"}


def label_for_command(argv: List[str]) -> str:
    """A short name for a server command: the package or script it runs."""
    for a in argv:
        if not a or a.startswith("-"):
            continue
        base = re.split(r"[\\/]", a)[-1] if not a.startswith("@") else a.lstrip("@")
        stem = re.sub(r"\.(exe|cmd|bat)$", "", base.lower())
        if re.sub(r"[\d.]+$", "", stem) in _LAUNCHERS:
            continue
        base = re.sub(r"(?<=.)@[^/@]*$", "", base)  # drop @version
        return re.sub(r"\.(py|js|mjs|cjs|ts|exe)$", "", base) or "server"
    return "server"


def parse_pairs(items: Iterable[str], sep: str, what: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for item in items:
        if sep not in item:
            raise ServerNotFound(f"{what} {item!r} must look like NAME{sep}VALUE")
        k, v = item.split(sep, 1)
        out[k.strip()] = v.strip() if sep == ":" else v
    return out


# --- client configs ----------------------------------------------------------------------

def _home() -> Path:
    return Path(os.environ.get("SKILLHOP_HOME") or Path.home())


def config_files(cwd: Optional[Path] = None) -> List[Tuple[str, Path]]:
    """MCP client config files skillhop reads server definitions from, in lookup order."""
    cwd = cwd or Path.cwd()
    home = _home()
    files = [
        ("Claude Code (project)", cwd / ".mcp.json"),
        ("Claude Code", home / ".claude.json"),
        ("Cursor (project)", cwd / ".cursor" / "mcp.json"),
        ("Cursor", home / ".cursor" / "mcp.json"),
        ("VS Code (project)", cwd / ".vscode" / "mcp.json"),
        ("Gemini CLI (project)", cwd / ".gemini" / "settings.json"),
        ("Gemini CLI", home / ".gemini" / "settings.json"),
        ("Windsurf", home / ".codeium" / "windsurf" / "mcp_config.json"),
        ("Codex", home / ".codex" / "config.toml"),
    ]
    if sys.platform == "darwin":
        files.append(("Claude Desktop", home / "Library" / "Application Support" / "Claude"
                      / "claude_desktop_config.json"))
    elif os.name == "nt":
        appdata = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
        files.append(("Claude Desktop", appdata / "Claude" / "claude_desktop_config.json"))
    else:
        files.append(("Claude Desktop", home / ".config" / "Claude" / "claude_desktop_config.json"))
    return files


def _servers_in(path: Path, cwd: Path) -> Dict[str, Dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    if path.suffix == ".toml":
        try:
            import tomllib  # type: ignore[import-not-found]
        except ImportError:  # Python < 3.11
            return {}
        try:
            data = tomllib.loads(text)
        except Exception:
            return {}
        return {k: v for k, v in (data.get("mcp_servers") or {}).items() if isinstance(v, dict)}
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    found: Dict[str, Dict[str, Any]] = {}
    for key in ("mcpServers", "servers"):
        block = data.get(key)
        if isinstance(block, dict):
            found.update({k: v for k, v in block.items() if isinstance(v, dict)})
    projects = data.get("projects")
    if isinstance(projects, dict):  # ~/.claude.json keeps per-project servers
        proj = projects.get(str(cwd))
        if isinstance(proj, dict) and isinstance(proj.get("mcpServers"), dict):
            found.update({k: v for k, v in proj["mcpServers"].items() if isinstance(v, dict)})
    return found


def spec_from_config(name: str, entry: Dict[str, Any]) -> ServerSpec:
    url = entry.get("url") or entry.get("httpUrl") or entry.get("serverUrl")
    if url:
        if entry.get("type") == "sse" or str(url).rstrip("/").endswith("/sse"):
            raise ServerNotFound(f"server {name!r} uses the old SSE transport, which has no skills "
                                 "extension; use its Streamable HTTP endpoint")
        return ServerSpec(label=name, url=str(url),
                          headers={str(k): str(v) for k, v in (entry.get("headers") or
                                                               entry.get("http_headers") or {}).items()},
                          config_name=name)
    command = entry.get("command")
    if not command:
        raise ServerNotFound(f"server {name!r} has neither a url nor a command")
    argv = [str(command), *[str(a) for a in entry.get("args") or []]]
    return ServerSpec(label=name, command=argv,
                      env={str(k): str(v) for k, v in (entry.get("env") or {}).items()},
                      config_name=name)


def find_configured(name: str, search: Optional[Iterable[Path]] = None,
                    cwd: Optional[Path] = None) -> ServerSpec:
    cwd = cwd or Path.cwd()
    paths = list(search) if search is not None else [p for _, p in config_files(cwd)]
    for path in paths:
        servers = _servers_in(path, cwd)
        if name in servers:
            return spec_from_config(name, servers[name])
    raise ServerNotFound(
        f"no server named {name!r} in your MCP client configs; pass a URL, or --cmd \"<command>\" "
        "(run `skillhop servers` to see the names skillhop can find)")


def all_configured(cwd: Optional[Path] = None) -> List[Tuple[str, str, Path]]:
    cwd = cwd or Path.cwd()
    out = []
    seen = set()
    for client, path in config_files(cwd):
        for name in _servers_in(path, cwd):
            if name not in seen:
                seen.add(name)
                out.append((name, client, path))
    return out


def resolve(server: Optional[str], cmd: Optional[str], headers: Dict[str, str],
            env: Dict[str, str], label: Optional[str] = None,
            search: Optional[Iterable[Path]] = None) -> ServerSpec:
    if cmd:
        argv = shlex.split(cmd, posix=os.name != "nt")
        return ServerSpec(label=label or label_for_command(argv), command=argv, env=env)
    if not server:
        raise ServerNotFound("say which server: a URL, a name from your MCP config, or --cmd")
    if re.match(r"^https?://", server):
        return ServerSpec(label=label or label_for_url(server), url=server, headers=headers)
    spec = find_configured(server, search)
    spec.headers.update(headers)
    spec.env.update(env)
    if label:
        spec.label = label
    return spec
