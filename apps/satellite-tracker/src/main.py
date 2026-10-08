import os
import signal
import sys
import time
import logging
from datetime import datetime, timezone
from typing import Dict, Any

from orbit_predictor import OrbitPredictor
from sdr_collector import SDRCollector
from metrics_exporter import MetricsExporter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("satellite-tracker")

# フォールバック用の代表的衛星TLEデータ
DEFAULT_SATELLITES = {
    "ISS": {
        "line1": "1 25544U 98067A   24001.50000000  .00016717  00000+0  10270-3 0  9001",
        "line2": "2 25544  51.6400  50.0000 0005000  40.0000 320.0000 15.50000000400001",
        "freq_hz": 437550000.0,
    },
    "CAS-4A": {
        "line1": "1 42761U 17034B   24001.50000000  .00001500  00000+0  10000-3 0  9001",
        "line2": "2 42761  43.0000  60.0000 0012000  50.0000 310.0000 15.10000000300001",
        "freq_hz": 435220000.0,
    },
}


def run_tracker():
    lat = float(os.getenv("OBSERVER_LATITUDE", "35.6895"))
    lon = float(os.getenv("OBSERVER_LONGITUDE", "139.6917"))
    elev_m = float(os.getenv("OBSERVER_ELEVATION_M", "30.0"))
    balcony_facing = os.getenv("BALCONY_FACING", "NORTH")
    mock_sdr = os.getenv("MOCK_SDR", "false").lower() in ("true", "1", "yes")
    metrics_port = int(os.getenv("METRICS_PORT", "9100"))
    update_interval = float(os.getenv("UPDATE_INTERVAL_SEC", "2.0"))

    logger.info(
        f"Starting satellite-tracker (Observer: Lat={lat}, Lon={lon}, Elev={elev_m}m, Facing={balcony_facing}, MockSDR={mock_sdr})"
    )

    predictor = OrbitPredictor(
        observer_lat=lat,
        observer_lon=lon,
        observer_elev_m=elev_m,
        balcony_facing=balcony_facing,
    )
    collector = SDRCollector(mock_sdr=mock_sdr)
    exporter = MetricsExporter()

    # Prometheus HTTP サーバー起動
    exporter.start_server(port=metrics_port)
    logger.info(f"Prometheus metrics HTTP server listening on :{metrics_port}/metrics")

    # 終了シグナルハンドラ
    running = True

    def sig_handler(signum, frame):
        nonlocal running
        logger.info(f"Received signal {signum}, shutting down...")
        running = False

    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    collector.start()

    active_satellite = None
    pass_in_progress = False

    while running:
        now_utc = datetime.now(timezone.utc)

        # ターゲット衛星の選定（北天視界に入っている衛星を探索）
        current_tracked = None

        for sat_name, sat_data in DEFAULT_SATELLITES.items():
            pos = predictor.calculate_position(
                satellite_name=sat_name,
                tle_line1=sat_data["line1"],
                tle_line2=sat_data["line2"],
                frequency_hz=sat_data["freq_hz"],
                timestamp=now_utc,
            )

            is_visible = predictor.is_in_view(pos.elevation_deg, pos.azimuth_deg)

            if is_visible:
                current_tracked = (sat_name, sat_data, pos)
                break
            else:
                exporter.set_tracking_status(sat_name, active=False)
                # 次回パス予定の更新
                next_pass = predictor.get_next_pass(
                    satellite_name=sat_name,
                    tle_line1=sat_data["line1"],
                    tle_line2=sat_data["line2"],
                    frequency_hz=sat_data["freq_hz"],
                    start_time=now_utc,
                    search_hours=12.0,
                )
                if next_pass:
                    exporter.set_next_pass(sat_name, next_pass.aos_time.timestamp())

        if current_tracked:
            sat_name, sat_data, pos = current_tracked
            if not pass_in_progress or active_satellite != sat_name:
                logger.info(f"Satellite AOS entered: {sat_name} (El: {pos.elevation_deg:.1f}°, Az: {pos.azimuth_deg:.1f}°)")
                pass_in_progress = True
                active_satellite = sat_name

            exporter.set_tracking_status(sat_name, active=True)
            exporter.update_orbit_metrics(
                satellite_name=sat_name,
                elevation_deg=pos.elevation_deg,
                azimuth_deg=pos.azimuth_deg,
                doppler_predicted_hz=pos.doppler_shift_hz,
            )

            # SDR RF 測定
            spec = collector.measure_spectrum(
                center_freq_hz=sat_data["freq_hz"],
                expected_doppler_hz=pos.doppler_shift_hz,
            )
            exporter.update_rf_metrics(
                satellite_name=sat_name,
                rssi_dbm=spec.rssi_dbm,
                snr_db=spec.snr_db,
                doppler_measured_hz=spec.measured_doppler_hz,
            )
        else:
            if pass_in_progress and active_satellite:
                logger.info(f"Satellite LOS completed: {active_satellite}")
                exporter.record_pass_completed(active_satellite, status="completed")
                pass_in_progress = False
                active_satellite = None

        time.sleep(update_interval)

    collector.stop()
    logger.info("Satellite tracker shutdown complete.")


if __name__ == "__main__":
    run_tracker()
