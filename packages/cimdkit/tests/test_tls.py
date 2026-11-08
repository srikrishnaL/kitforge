"""The real transport against a local TLS server: pinning, SNI, limits, no redirects."""

import http.server
import shutil
import ssl
import subprocess
import threading

import pytest

from cimdkit import ClientResolver, FetchError, TransientFetchError, fetch_document

pytestmark = pytest.mark.skipif(shutil.which("openssl") is None, reason="needs openssl")

HOST = "client.test"
CID = f"https://{HOST}:{{port}}/oauth/client.json"


@pytest.fixture(scope="module")
def cert(tmp_path_factory):
    d = tmp_path_factory.mktemp("tls")
    key, crt = d / "key.pem", d / "cert.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                    "-keyout", str(key), "-out", str(crt), "-subj", f"/CN={HOST}",
                    "-addext", f"subjectAltName=DNS:{HOST}"],
                   check=True, capture_output=True)
    return str(crt), str(key)


@pytest.fixture
def server(cert):
    routes = {}
    seen = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append({"path": self.path, "host": self.headers["Host"],
                         "accept": self.headers["Accept"], "sni": getattr(
                             self.connection, "server_hostname", None)})
            status, headers, body = routes.get(self.path, (404, {}, b"nope"))
            self.send_response(status)
            for k, v in headers.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(*cert)
    sni = []
    ctx.sni_callback = lambda sock, name, c: sni.append(name)
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    port = httpd.server_address[1]
    client_ctx = ssl.create_default_context(cafile=cert[0])
    yield {"port": port, "routes": routes, "seen": seen, "sni": sni,
           "cid": CID.format(port=port),
           "opts": {"resolver": lambda h, p: ["127.0.0.1"], "allow_loopback": True,
                    "ssl_context": client_ctx, "timeout": 3}}
    httpd.shutdown()
    httpd.server_close()


def doc_for(cid):
    import json
    return json.dumps({"client_id": cid, "client_name": "TLS test",
                       "redirect_uris": ["http://127.0.0.1/cb"]}).encode()


def test_fetches_over_pinned_tls_with_sni(server):
    server["routes"]["/oauth/client.json"] = (
        200, {"Content-Type": "application/json", "Cache-Control": "max-age=60"},
        doc_for(server["cid"]))
    result = fetch_document(server["cid"], **server["opts"])
    assert result.document["client_name"] == "TLS test"
    assert result.headers["cache-control"] == "max-age=60"
    assert server["sni"] == [HOST]
    assert server["seen"][0]["host"] == f"{HOST}:{server['port']}"
    assert server["seen"][0]["accept"] == "application/json"


def test_resolver_end_to_end(server):
    server["routes"]["/oauth/client.json"] = (
        200, {"Content-Type": "application/json"}, doc_for(server["cid"]))
    meta = ClientResolver(**server["opts"]).resolve(server["cid"])
    assert meta.check_redirect_uri("http://127.0.0.1:4242/cb")


def test_redirect_is_not_followed(server):
    server["routes"]["/oauth/client.json"] = (302, {"Location": "/elsewhere"}, b"")
    with pytest.raises(FetchError, match="redirects"):
        fetch_document(server["cid"], **server["opts"])
    assert [s["path"] for s in server["seen"]] == ["/oauth/client.json"]


def test_declared_length_over_limit_is_refused(server):
    server["routes"]["/oauth/client.json"] = (
        200, {"Content-Type": "application/json"}, b" " * 6000 + doc_for(server["cid"]))
    with pytest.raises(FetchError, match="limit is 5120"):
        fetch_document(server["cid"], **server["opts"])


def test_certificate_for_another_host_is_refused(server):
    other = server["cid"].replace(HOST, "other.test")
    with pytest.raises(FetchError, match="certificate"):
        fetch_document(other, **server["opts"])


def test_server_error_is_transient(server):
    server["routes"]["/oauth/client.json"] = (503, {}, b"down")
    with pytest.raises(TransientFetchError):
        fetch_document(server["cid"], **server["opts"])


def test_untrusted_certificate_is_refused(server):
    opts = dict(server["opts"], ssl_context=ssl.create_default_context())
    with pytest.raises(FetchError, match="certificate"):
        fetch_document(server["cid"], **opts)
