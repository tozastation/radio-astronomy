from http.server import BaseHTTPRequestHandler, HTTPServer
import threading
import urllib.error
import urllib.request

from proxy import TARGET_URL_BASE, create_server


class _FakeEdgedHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/metrics/cadvisor":
            body = b"# HELP cadvisor_version_info info\ncadvisor_version_info 1\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

    def log_message(self, format, *args):
        pass


def test_default_target_url_uses_loopback_http():
    assert TARGET_URL_BASE == "http://127.0.0.1:10350"


def test_proxy_forwards_http_cadvisor_metrics():
    upstream = HTTPServer(("127.0.0.1", 0), _FakeEdgedHandler)
    upstream_port = upstream.server_address[1]
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    upstream_thread.start()

    proxy_srv = create_server("127.0.0.1", 0, f"http://127.0.0.1:{upstream_port}")
    proxy_port = proxy_srv.server_address[1]
    proxy_thread = threading.Thread(target=proxy_srv.serve_forever, daemon=True)
    proxy_thread.start()

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{proxy_port}/metrics/cadvisor", timeout=5) as resp:
            assert resp.status == 200
            body = resp.read().decode("utf-8")
            assert "cadvisor_version_info 1" in body

        head_req = urllib.request.Request(f"http://127.0.0.1:{proxy_port}/metrics/cadvisor", method="HEAD")
        with urllib.request.urlopen(head_req, timeout=5) as head_resp:
            assert head_resp.status == 200
    finally:
        proxy_srv.shutdown()
        proxy_srv.server_close()
        upstream.shutdown()
        upstream.server_close()


def test_proxy_returns_502_when_upstream_unreachable():
    # Use an ephemeral port that is closed right away
    tmp = HTTPServer(("127.0.0.1", 0), _FakeEdgedHandler)
    closed_port = tmp.server_address[1]
    tmp.server_close()

    proxy_srv = create_server("127.0.0.1", 0, f"http://127.0.0.1:{closed_port}")
    proxy_port = proxy_srv.server_address[1]
    proxy_thread = threading.Thread(target=proxy_srv.serve_forever, daemon=True)
    proxy_thread.start()

    try:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{proxy_port}/metrics/cadvisor", timeout=5)
            raise AssertionError("Expected HTTPError 502")
        except urllib.error.HTTPError as err:
            assert err.code == 502
    finally:
        proxy_srv.shutdown()
        proxy_srv.server_close()
