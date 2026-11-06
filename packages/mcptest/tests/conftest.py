import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

pytest_plugins = ["pytester"]

HERE = Path(__file__).parent
FAKE = str(HERE / "servers" / "fake_server.py")
sys.path.insert(0, str(HERE / "servers"))

import fake_server  # noqa: E402


@pytest.fixture
def fake_cmd():
    """argv that starts the stdio fake server."""
    return [sys.executable, FAKE]


def _http_handler(mode, seen):
    tools = fake_server.GOOD

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_DELETE(self):
            seen.append(("DELETE", dict(self.headers)))
            self.send_response(200)
            self.end_headers()

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            msg = json.loads(self.rfile.read(length))
            seen.append((msg.get("method"), dict(self.headers)))
            if "id" not in msg:
                self.send_response(202)
                self.end_headers()
                return
            method = msg["method"]
            if method == "initialize":
                result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                          "serverInfo": {"name": "fake-http", "version": "1"}}
            elif method == "tools/list":
                result = {"tools": tools}
            elif method == "tools/call":
                args = msg["params"]["arguments"]
                result = {"content": [{"type": "text", "text": str(args["a"] + args["b"])}]}
            else:
                result = None
            body = {"jsonrpc": "2.0", "id": msg["id"]}
            if result is None:
                body["error"] = {"code": -32601, "message": "nope"}
            else:
                body["result"] = result
            self.send_response(200)
            if method == "initialize":
                self.send_header("Mcp-Session-Id", "sess-123")
            if mode == "sse":
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                note = {"jsonrpc": "2.0", "method": "notifications/progress", "params": {"progress": 1}}
                self.wfile.write(f"event: message\ndata: {json.dumps(note)}\n\n".encode())
                self.wfile.write(f"event: message\ndata: {json.dumps(body)}\n\n".encode())
            else:
                data = json.dumps(body).encode()
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

    return Handler


@pytest.fixture(params=["json", "sse"])
def http_server(request):
    """A running fake Streamable HTTP server. Yields (url, seen_requests)."""
    seen = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _http_handler(request.param, seen))
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/mcp", seen
    srv.shutdown()
    srv.server_close()
