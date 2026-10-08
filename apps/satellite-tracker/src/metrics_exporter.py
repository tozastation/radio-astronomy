from typing import Optional
from prometheus_client import CollectorRegistry, Counter, Gauge, start_http_server, REGISTRY


class MetricsExporter:
    def __init__(self, registry: Optional[CollectorRegistry] = None):
        self.registry = registry if registry is not None else REGISTRY

        self.tracking_active = Gauge(
            "satellite_tracking_active",
            "Indicates whether a satellite pass is currently being tracked (1 for active, 0 for idle)",
            ["satellite"],
            registry=self.registry,
        )
        self.elevation = Gauge(
            "satellite_elevation_degrees",
            "Current elevation angle of the satellite in degrees",
            ["satellite"],
            registry=self.registry,
        )
        self.azimuth = Gauge(
            "satellite_azimuth_degrees",
            "Current azimuth angle of the satellite in degrees",
            ["satellite"],
            registry=self.registry,
        )
        self.doppler_predicted = Gauge(
            "satellite_doppler_predicted_hz",
            "Theoretical Doppler frequency shift predicted by SGP4 orbital mechanics in Hz",
            ["satellite"],
            registry=self.registry,
        )
        self.doppler_measured = Gauge(
            "satellite_doppler_measured_hz",
            "Actual Doppler frequency shift measured from FFT peak spectrum in Hz",
            ["satellite"],
            registry=self.registry,
        )
        self.rssi = Gauge(
            "satellite_rssi_dbm",
            "Received signal strength indicator (RSSI) in dBm",
            ["satellite"],
            registry=self.registry,
        )
        self.snr = Gauge(
            "satellite_snr_db",
            "Signal-to-noise ratio in dB relative to estimated noise floor",
            ["satellite"],
            registry=self.registry,
        )
        self.passes_total = Counter(
            "satellite_passes_total",
            "Total number of satellite passes observed",
            ["satellite", "status"],
            registry=self.registry,
        )
        self.next_aos_timestamp = Gauge(
            "satellite_next_aos_timestamp_seconds",
            "Unix timestamp of the next anticipated acquisition of signal (AOS)",
            ["satellite"],
            registry=self.registry,
        )

    def set_tracking_status(self, satellite_name: str, active: bool) -> None:
        self.tracking_active.labels(satellite=satellite_name).set(1.0 if active else 0.0)

    def set_next_pass(self, satellite_name: str, aos_timestamp: float) -> None:
        self.next_aos_timestamp.labels(satellite=satellite_name).set(aos_timestamp)

    def update_orbit_metrics(
        self,
        satellite_name: str,
        elevation_deg: float,
        azimuth_deg: float,
        doppler_predicted_hz: float,
    ) -> None:
        self.elevation.labels(satellite=satellite_name).set(elevation_deg)
        self.azimuth.labels(satellite=satellite_name).set(azimuth_deg)
        self.doppler_predicted.labels(satellite=satellite_name).set(doppler_predicted_hz)

    def update_rf_metrics(
        self,
        satellite_name: str,
        rssi_dbm: float,
        snr_db: float,
        doppler_measured_hz: float,
    ) -> None:
        self.rssi.labels(satellite=satellite_name).set(rssi_dbm)
        self.snr.labels(satellite=satellite_name).set(snr_db)
        self.doppler_measured.labels(satellite=satellite_name).set(doppler_measured_hz)

    def record_pass_completed(self, satellite_name: str, status: str = "completed") -> None:
        self.passes_total.labels(satellite=satellite_name, status=status).inc()

    def start_server(self, port: int = 9100) -> None:
        """Prometheus HTTP エンドポイントを起動する"""
        start_http_server(port, registry=self.registry)
