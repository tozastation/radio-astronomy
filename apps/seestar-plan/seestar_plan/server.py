"""窓・星・月の値を Prometheus の形式で返す。SeeStar へは接続しない。"""

from __future__ import annotations

import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest

from seestar_plan.metrics import PlanCollector
from seestar_plan.plan import Site
from seestar_plan.astro import fetch_astro
from seestar_plan.weather import ForecastCache, fetch_forecast

PORT = int(os.environ.get("PORT", "9101"))


def load_site() -> Site:
    path = os.environ.get("SEESTAR_SITE_FILE")
    if path:
        return Site.from_file(path)
    return Site()


def fetch_site_forecast():
    site = load_site()
    return fetch_forecast(site.latitude_deg, site.longitude_deg)


def fetch_site_astro():
    site = load_site()
    return fetch_astro(site.latitude_deg, site.longitude_deg)


FORECAST = ForecastCache(fetch_site_forecast, name="Open-Meteo")
ASTRO = ForecastCache(fetch_site_astro, interval_seconds=3600.0, name="7Timer!")
REGISTRY = CollectorRegistry()
REGISTRY.register(PlanCollector(load_site, forecast=FORECAST.get, astro=ASTRO.get))


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in ("/healthz", "/health"):
            self._bytes(b"ok", "text/plain; charset=utf-8")
            return
        if path == "/metrics":
            self._bytes(generate_latest(REGISTRY), CONTENT_TYPE_LATEST)
            return
        self.send_error(404)

    def _bytes(self, body: bytes, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        pass


def main() -> None:
    if os.environ.get("WEATHER_FORECAST", "on") != "off":
        FORECAST.start()
        ASTRO.start()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"seestar-plan exporter listening on :{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
