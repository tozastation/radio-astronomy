#!/usr/bin/env python3
"""
KubeEdge Edged Metrics Proxy (http://127.0.0.1:10350 -> http://0.0.0.0:19095)
Enables Prometheus to scrape KubeEdge cAdvisor metrics across cluster networks.
"""

import os
import ssl
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "19095"))
TARGET_URL_BASE = os.environ.get("TARGET_URL_BASE", "http://127.0.0.1:10350").rstrip("/")


def _create_unverified_ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


ssl_ctx = _create_unverified_ssl_context()


class ProxyHandler(BaseHTTPRequestHandler):
    target_url_base: str = TARGET_URL_BASE

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Connection", "close")
        self.end_headers()

    def do_GET(self):
        target_url = f"{self.target_url_base}{self.path}"
        try:
            req = urllib.request.Request(target_url)
            open_kwargs = {"timeout": 10}
            if target_url.startswith("https://"):
                open_kwargs["context"] = ssl_ctx
            with urllib.request.urlopen(req, **open_kwargs) as resp:
                data = resp.read()
                print(f"[Proxy] GET {self.path} -> {resp.status} ({len(data)} bytes)", flush=True)
                self.send_response(resp.status)
                self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(data)
        except Exception as e:
            print(f"[Proxy Error] GET {self.path}: {e}", flush=True)
            self.send_error(502, f"Bad Gateway: {e}")

    def log_message(self, format, *args):
        pass


def create_server(host: str = "0.0.0.0", port: int = LISTEN_PORT, target_url_base: str = TARGET_URL_BASE) -> ThreadingHTTPServer:
    """Create a proxy HTTP server forwarding requests to target_url_base."""

    class ConfiguredProxyHandler(ProxyHandler):
        pass

    ConfiguredProxyHandler.target_url_base = target_url_base.rstrip("/")
    return ThreadingHTTPServer((host, port), ConfiguredProxyHandler)


if __name__ == "__main__":
    httpd = create_server("0.0.0.0", LISTEN_PORT, TARGET_URL_BASE)
    print(f"KubeEdge Metrics Proxy listening on 0.0.0.0:{LISTEN_PORT} -> {TARGET_URL_BASE}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.server_close()
