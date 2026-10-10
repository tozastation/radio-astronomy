import os
import signal
import sys
import time
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from orbit_predictor import OrbitPredictor
from sdr_collector import SDRCollector
from metrics_exporter import MetricsExporter
from tle_fetcher import fetch_satellite_tles
from audio_spooler import AudioSpooler
from s3_uploader import S3Uploader

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

    # 起動時に Celestrak から最新 TLE を動的取得（失敗時は静的キャッシュに安全フォールバック）
    active_satellites = fetch_satellite_tles(DEFAULT_SATELLITES)

    predictor = OrbitPredictor(
        observer_lat=lat,
        observer_lon=lon,
        observer_elev_m=elev_m,
        balcony_facing=balcony_facing,
    )
    collector = SDRCollector(mock_sdr=mock_sdr)
    exporter = MetricsExporter()
    spooler = AudioSpooler(spool_dir=os.getenv("SPOOL_DIR", "/tmp/spool"))
    uploader = S3Uploader()

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

    active_satellite: Optional[str] = None
    pass_in_progress = False
    current_pass_id: Optional[str] = None
    last_tle_update = time.time()
    tle_refresh_interval = 86400.0  # 24時間ごとにTLEを再取得

    while running:
        now_utc = datetime.now(timezone.utc)

        # 定期的な TLE 更新チェック
        if time.time() - last_tle_update > tle_refresh_interval:
            logger.info("Periodic TLE update triggered.")
            active_satellites = fetch_satellite_tles(DEFAULT_SATELLITES)
            last_tle_update = time.time()

        # 全衛星の現在位置と視界判定
        visible_candidates = []
        earliest_next_aos_sec = float("inf")

        for sat_name, sat_data in active_satellites.items():
            pos = predictor.calculate_position(
                satellite_name=sat_name,
                tle_line1=sat_data["line1"],
                tle_line2=sat_data["line2"],
                frequency_hz=sat_data["freq_hz"],
                timestamp=now_utc,
            )

            is_visible = predictor.is_in_view(pos.elevation_deg, pos.azimuth_deg)

            if is_visible:
                visible_candidates.append((sat_name, sat_data, pos))
            else:
                # 視界外の衛星は待機ステータスと次回パスを更新
                exporter.set_tracking_status(sat_name, active=False)
                next_pass = predictor.get_next_pass(
                    satellite_name=sat_name,
                    tle_line1=sat_data["line1"],
                    tle_line2=sat_data["line2"],
                    frequency_hz=sat_data["freq_hz"],
                    start_time=now_utc,
                    search_hours=12.0,
                )
                if next_pass:
                    aos_ts = next_pass.aos_time.timestamp()
                    exporter.set_next_pass(sat_name, aos_ts)
                    time_to_aos = aos_ts - now_utc.timestamp()
                    if 0 < time_to_aos < earliest_next_aos_sec:
                        earliest_next_aos_sec = time_to_aos

        # 視界内の衛星がある場合、最も仰角の高い衛星を優先追尾
        if visible_candidates:
            # 仰角（pos.elevation_deg）でソート
            visible_candidates.sort(key=lambda x: x[2].elevation_deg, reverse=True)
            chosen_sat_name, chosen_sat_data, chosen_pos = visible_candidates[0]

            # 追尾衛星が切り替わった場合（重複パス時）
            if active_satellite and active_satellite != chosen_sat_name:
                logger.info(f"Switching tracking target from {active_satellite} to {chosen_sat_name}")
                wav_file = spooler.finish_pass()
                if wav_file and os.path.exists(wav_file):
                    s3_key = f"raw/{active_satellite}/{os.path.basename(wav_file)}"
                    uploader.upload_and_cleanup(wav_file, s3_key)
                exporter.record_pass_completed(active_satellite, status="completed")
                exporter.set_tracking_status(active_satellite, active=False)

            if not pass_in_progress or active_satellite != chosen_sat_name:
                logger.info(
                    f"Satellite AOS entered: {chosen_sat_name} (El: {chosen_pos.elevation_deg:.1f}°, Az: {chosen_pos.azimuth_deg:.1f}°)"
                )
                pass_in_progress = True
                active_satellite = chosen_sat_name
                current_pass_id = f"{chosen_sat_name}_{now_utc.strftime('%Y%m%d_%H%M%S')}"
                collector.warmup(center_freq_hz=chosen_sat_data["freq_hz"])
                spooler.start_pass(chosen_sat_name, current_pass_id)

            exporter.set_tracking_status(chosen_sat_name, active=True)
            exporter.update_orbit_metrics(
                satellite_name=chosen_sat_name,
                elevation_deg=chosen_pos.elevation_deg,
                azimuth_deg=chosen_pos.azimuth_deg,
                doppler_predicted_hz=chosen_pos.doppler_shift_hz,
            )

            # SDR RF 測定
            spec = collector.measure_spectrum(
                center_freq_hz=chosen_sat_data["freq_hz"],
                expected_doppler_hz=chosen_pos.doppler_shift_hz,
            )
            exporter.update_rf_metrics(
                satellite_name=chosen_sat_name,
                rssi_dbm=spec.rssi_dbm,
                snr_db=spec.snr_db,
                doppler_measured_hz=spec.measured_doppler_hz,
            )
        else:
            if pass_in_progress and active_satellite:
                logger.info(f"Satellite LOS completed: {active_satellite}")
                wav_file = spooler.finish_pass()
                if wav_file and os.path.exists(wav_file):
                    s3_key = f"raw/{active_satellite}/{os.path.basename(wav_file)}"
                    uploader.upload_and_cleanup(wav_file, s3_key)

                exporter.record_pass_completed(active_satellite, status="completed")
                exporter.set_tracking_status(active_satellite, active=False)
                pass_in_progress = False
                active_satellite = None
                current_pass_id = None

            # 省電力制御: 次回 AOS まで 30 秒以上空いていれば SDR をスタンバイ (給電停止)
            if earliest_next_aos_sec > 30.0:
                if not collector.is_standby:
                    collector.standby()
            elif earliest_next_aos_sec <= 30.0 and collector.is_standby:
                collector.warmup()

        time.sleep(update_interval)

    collector.stop()
    logger.info("Satellite tracker shutdown complete.")


if __name__ == "__main__":
    run_tracker()
