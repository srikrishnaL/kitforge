"""A fake MCP server that serves a folder of skills over the Skills extension (SEP-2640).

Run over stdio:  python fakeskills.py <skills-root>   (options through FAKE_* variables)
Or in-process over HTTP with ``serve_http``.

FAKE_MODE      modern (default) | legacy | picky (modern, rejects 2026-07-28 with -32022 and
               asks for 2099-01-01) | silent-legacy (ignores server/discover)
FAKE_TAMPER    comma-separated relative URIs whose served bytes differ from the digest
FAKE_DYNAMIC   comma-separated skill paths listed as "dynamic"
FAKE_EXTRA     JSON list of raw entries appended to skills/list
FAKE_NOISE     1: print banners and logs to stdout
FAKE_LOG       file to append every received message to (JSON lines)
FAKE_PAGE      page size for skills/list (default 2)
FAKE_NO_EXT    1: don't declare the extension
FAKE_FM        JSON object merged into every entry's frontmatter (to fake a mismatch)
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

MODERN = "2026-07-28"


def _digest(b: bytes) -> str:
    return "sha256:" + hashlib.sha256(b).hexdigest()


class Fake:
    def __init__(self, root: str, env=None):
        self.root = Path(root)
        self.env = dict(os.environ if env is None else env)
        self.mode = self.env.get("FAKE_MODE", "modern")
        self.tamper = {t for t in self.env.get("FAKE_TAMPER", "").split(",") if t}
        self.dynamic = {t for t in self.env.get("FAKE_DYNAMIC", "").split(",") if t}
        self.extra = json.loads(self.env.get("FAKE_EXTRA", "[]"))
        self.page = int(self.env.get("FAKE_PAGE", "2"))
        self.fm_override = json.loads(self.env.get("FAKE_FM", "{}"))
        self.log = self.env.get("FAKE_LOG")
        self.initialized = False

    # --- skills from disk --------------------------------------------------------------
    def entries(self):
        out = []
        for d in sorted(p.parent for p in self.root.rglob("SKILL.md")):
            path = d.relative_to(self.root).as_posix()
            text = (d / "SKILL.md").read_text(encoding="utf-8")
            fm = yaml.safe_load(text.split("---", 2)[1])
            fm.update(self.fm_override)
            uri = f"skill://{path}/SKILL.md"
            if path in self.dynamic:
                res = "dynamic"
            else:
                res = [{"uri": f"skill://{path}/{f.relative_to(d).as_posix()}",
                        "digest": _digest(f.read_bytes()), "size": len(f.read_bytes())}
                       for f in sorted(d.rglob("*")) if f.is_file()]
            out.append({"uri": uri, "frontmatter": fm, "resources": res})
        return out + list(self.extra)

    def read(self, uri: str):
        if not uri.startswith("skill://"):
            return None
        rel = uri[len("skill://"):]
        f = self.root / rel
        if not f.is_file() or ".." in rel.split("/"):
            return None
        data = f.read_bytes()
        if rel in self.tamper:
            data = data + b"\n<!-- injected -->\n"
        try:
            return {"uri": uri, "mimeType": "text/markdown", "text": data.decode("utf-8")}
        except UnicodeDecodeError:
            return {"uri": uri, "mimeType": "application/octet-stream",
                    "blob": base64.b64encode(data).decode()}

    # --- protocol ----------------------------------------------------------------------
    def capabilities(self):
        caps = {"resources": {}}
        if self.env.get("FAKE_NO_EXT") != "1":
            caps["extensions"] = {"io.modelcontextprotocol/skills": {"directoryRead": True}}
        return caps

    def handle(self, msg):
        if self.log:
            with open(self.log, "a") as fh:
                fh.write(json.dumps(msg) + "\n")
        if "id" not in msg:
            if msg.get("method") == "notifications/initialized":
                self.initialized = True
            return None
        method, params, mid = msg.get("method"), msg.get("params") or {}, msg["id"]

        def ok(result):
            if self.mode != "legacy" and self.mode != "silent-legacy":
                result = {"resultType": "complete", **result}
            return {"jsonrpc": "2.0", "id": mid, "result": result}

        def err(code, text, data=None):
            e = {"code": code, "message": text}
            if data is not None:
                e["data"] = data
            return {"jsonrpc": "2.0", "id": mid, "error": e}

        legacy = self.mode in ("legacy", "silent-legacy")
        if method == "server/discover":
            if self.mode == "silent-legacy":
                return "silent"
            if legacy:
                return err(-32601, "Method not found")
        if method == "initialize":
            if not legacy:
                return err(-32601, "Method not found")
            self.initialized = True
            return ok({"protocolVersion": "2025-11-25", "capabilities": self.capabilities(),
                       "serverInfo": {"name": "fake-skills", "version": "1"}})
        if legacy and not self.initialized:
            return err(-32600, "not initialized")
        if not legacy:
            version = (params.get("_meta") or {}).get("io.modelcontextprotocol/protocolVersion")
            want = "2099-01-01" if self.mode == "picky" else MODERN
            if version != want:
                return err(-32022, "Unsupported protocol version", {"supported": [want],
                                                                    "requested": version})
        if method == "server/discover":
            return ok({"supportedVersions": ["2099-01-01" if self.mode == "picky" else MODERN],
                       "capabilities": self.capabilities(),
                       "_meta": {"io.modelcontextprotocol/serverInfo": {"name": "fake-skills",
                                                                        "version": "1"}},
                       "ttlMs": 0, "cacheScope": "public"})
        if method == "skills/list":
            all_ = self.entries()
            start = int(params.get("cursor") or 0)
            page = all_[start:start + self.page]
            res = {"skills": page, "ttlMs": 0, "cacheScope": "public"}
            if start + self.page < len(all_):
                res["nextCursor"] = str(start + self.page)
            return ok(res)
        if method == "skills/get":
            for e in self.entries():
                if e["uri"] == params.get("uri"):
                    return ok({"skill": e, "ttlMs": 0, "cacheScope": "public"})
            return err(-32602, f"No skill is served at {params.get('uri')}")
        if method == "resources/read":
            c = self.read(params.get("uri", ""))
            if c is None:
                return err(-32602, "Resource not found")
            return ok({"contents": [c], "ttlMs": 0, "cacheScope": "public"})
        if method == "resources/directory/read":
            rel = params.get("uri", "")[len("skill://"):]
            d = self.root / rel
            if not d.is_dir():
                return err(-32602, "not a directory resource")
            res = []
            for child in sorted(d.iterdir()):
                curi = f"skill://{rel}/{child.name}"
                res.append({"uri": curi, "name": child.name,
                            "mimeType": "inode/directory" if child.is_dir() else "text/markdown"})
            return ok({"resources": res, "ttlMs": 0, "cacheScope": "public"})
        if method == "ping":
            return ok({})
        return err(-32601, "Method not found")


def main() -> None:
    fake = Fake(sys.argv[1])
    if fake.env.get("FAKE_NOISE") == "1":
        print("Starting fake skills server v1 ...", flush=True)
        print("[INFO] listening on stdio", flush=True)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        resp = fake.handle(json.loads(line))
        if resp in (None, "silent"):
            continue
        if fake.env.get("FAKE_NOISE") == "1":
            print("debug: handled " + str(json.loads(line).get("method")), flush=True)
        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()


def serve_http(root: str, env=None, sse: bool = False):
    """Start the fake server over Streamable HTTP; returns (url, server, requests list)."""
    fake = Fake(root, env if env is not None else {})
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append({"headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
            if fake.mode == "legacy" and body.get("method") not in ("initialize",) \
                    and self.headers.get("Mcp-Session-Id") != "sess-1" and "id" in body:
                if body.get("method") == "server/discover":
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b"Bad Request: no session")
                    return
            resp = fake.handle(body)
            if resp is None:
                self.send_response(202)
                self.end_headers()
                return
            data = json.dumps(resp).encode()
            status = 400 if "error" in resp and resp["error"]["code"] == -32022 else 200
            self.send_response(status)
            if fake.mode == "legacy" and body.get("method") == "initialize":
                self.send_header("Mcp-Session-Id", "sess-1")
            if sse and status == 200:
                payload = (b'event: message\ndata: {"jsonrpc":"2.0","method":"notifications/progress",'
                           b'"params":{}}\n\nevent: message\ndata: ' + data + b"\n\n")
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}/mcp", server, seen


if __name__ == "__main__":
    main()
