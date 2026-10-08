import os
import sys
from datetime import datetime, timezone, timedelta
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))
from orbit_predictor import (
    OrbitPredictor,
    PassInfo,
    SatellitePosition,
)

# 代表的なISS TLEサンプル
ISS_TLE_LINE1 = "1 25544U 98067A   24001.50000000  .00016717  00000+0  10270-3 0  9001"
ISS_TLE_LINE2 = "2 25544  51.6400  50.0000 0005000  40.0000 320.0000 15.50000000400001"
ISS_FREQ_HZ = 437550000.0  # 437.550 MHz


@pytest.fixture
def predictor():
    # 東京（北緯35.6895, 東経139.6917, 高度30m）、北向きベランダ
    return OrbitPredictor(
        observer_lat=35.6895,
        observer_lon=139.6917,
        observer_elev_m=30.0,
        balcony_facing="NORTH",
        min_elevation_deg=10.0,
    )


def test_is_in_balcony_field_of_view(predictor):
    # 北向きベランダ: 方位角 270°〜360° または 0°〜90°、かつ 仰角 >= 10°
    assert predictor.is_in_view(elevation_deg=45.0, azimuth_deg=0.0) is True     # 真北
    assert predictor.is_in_view(elevation_deg=15.0, azimuth_deg=45.0) is True    # 北東
    assert predictor.is_in_view(elevation_deg=20.0, azimuth_deg=315.0) is True   # 北西
    assert predictor.is_in_view(elevation_deg=10.0, azimuth_deg=90.0) is True    # 東境界
    assert predictor.is_in_view(elevation_deg=10.0, azimuth_deg=270.0) is True   # 西境界

    # 視界外（南側または低仰角）
    assert predictor.is_in_view(elevation_deg=45.0, azimuth_deg=180.0) is False  # 真南 (遮蔽)
    assert predictor.is_in_view(elevation_deg=45.0, azimuth_deg=120.0) is False  # 南東 (遮蔽)
    assert predictor.is_in_view(elevation_deg=45.0, azimuth_deg=240.0) is False  # 南西 (遮蔽)
    assert predictor.is_in_view(elevation_deg=5.0, azimuth_deg=0.0) is False     # 北だが仰角不足 (< 10°)


def test_calculate_position_and_doppler_physics(predictor):
    # 特定時刻での位置とドップラー偏移を計算
    t = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    pos = predictor.calculate_position(
        satellite_name="ISS",
        tle_line1=ISS_TLE_LINE1,
        tle_line2=ISS_TLE_LINE2,
        frequency_hz=ISS_FREQ_HZ,
        timestamp=t,
    )

    assert isinstance(pos, SatellitePosition)
    assert -90.0 <= pos.elevation_deg <= 90.0
    assert 0.0 <= pos.azimuth_deg <= 360.0
    assert pos.range_km > 0.0

    # ドップラーの物理関係式検証: Delta_f = - f0 * (vr / c)
    c = 299792458.0  # m/s
    expected_shift = - ISS_FREQ_HZ * (pos.radial_velocity_km_s * 1000.0 / c)
    assert pytest.approx(pos.doppler_shift_hz, abs=1.0) == expected_shift


def test_get_next_pass(predictor):
    start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    pass_info = predictor.get_next_pass(
        satellite_name="ISS",
        tle_line1=ISS_TLE_LINE1,
        tle_line2=ISS_TLE_LINE2,
        frequency_hz=ISS_FREQ_HZ,
        start_time=start,
        search_hours=24.0,
    )

    if pass_info is not None:
        assert isinstance(pass_info, PassInfo)
        assert pass_info.aos_time < pass_info.tca_time < pass_info.los_time
        assert pass_info.max_elevation_deg >= 10.0
        assert pass_info.frequency_hz == ISS_FREQ_HZ
