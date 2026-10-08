import os
import sys
import pytest
from prometheus_client import CollectorRegistry, generate_latest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))
from metrics_exporter import MetricsExporter


def test_metrics_exporter_updates_and_generates_text():
    registry = CollectorRegistry()
    exporter = MetricsExporter(registry=registry)

    # 1. 待機中状態
    exporter.set_tracking_status("CAS-4A", active=False)
    exporter.set_next_pass("CAS-4A", aos_timestamp=1704110400.0)

    output1 = generate_latest(registry).decode("utf-8")
    assert 'satellite_tracking_active{satellite="CAS-4A"} 0.0' in output1
    assert 'satellite_next_aos_timestamp_seconds{satellite="CAS-4A"}' in output1
    assert exporter.next_aos_timestamp.labels(satellite="CAS-4A")._value.get() == 1704110400.0

    # 2. パス突入・追尾中状態
    exporter.set_tracking_status("CAS-4A", active=True)
    exporter.update_orbit_metrics(
        satellite_name="CAS-4A",
        elevation_deg=35.5,
        azimuth_deg=42.0,
        doppler_predicted_hz=5200.0,
    )
    exporter.update_rf_metrics(
        satellite_name="CAS-4A",
        rssi_dbm=-68.5,
        snr_db=16.2,
        doppler_measured_hz=5180.0,
    )
    exporter.record_pass_completed("CAS-4A", status="completed")

    output2 = generate_latest(registry).decode("utf-8")
    assert 'satellite_tracking_active{satellite="CAS-4A"} 1.0' in output2
    assert 'satellite_elevation_degrees{satellite="CAS-4A"} 35.5' in output2
    assert 'satellite_azimuth_degrees{satellite="CAS-4A"} 42.0' in output2
    assert 'satellite_doppler_predicted_hz{satellite="CAS-4A"} 5200.0' in output2
    assert 'satellite_doppler_measured_hz{satellite="CAS-4A"} 5180.0' in output2
    assert 'satellite_rssi_dbm{satellite="CAS-4A"} -68.5' in output2
    assert 'satellite_snr_db{satellite="CAS-4A"} 16.2' in output2
    assert 'satellite_passes_total{satellite="CAS-4A",status="completed"} 1.0' in output2
