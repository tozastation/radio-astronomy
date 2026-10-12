from datetime import datetime, timezone

from seestar_plan.catalog import CATALOG
from seestar_plan.sky import (
    culminates_north,
    culmination_altitude_deg,
    horizontal,
    in_north_window,
    sun_horizontal,
    visible_intervals,
)

LAT = 35.6895
LON = 139.6917
NIGHT = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)


def test_culmination_side_matches_declination():
    assert culminates_north(41.3, LAT)
    assert culminates_north(61.6, LAT)
    assert not culminates_north(24.1, LAT)
    assert not culminates_north(-5.4, LAT)
    assert abs(culmination_altitude_deg(41.3, LAT) - (90.0 - (41.3 - LAT))) < 1e-9
    assert abs(culmination_altitude_deg(-5.4, LAT) - (90.0 - (LAT - (-5.4)))) < 1e-9


def test_polaris_stays_near_latitude_and_north():
    polaris = CATALOG[0]
    position = horizontal(NIGHT, LAT, LON, polaris.ra_deg, polaris.dec_deg)
    assert abs(position.alt_deg - LAT) < 2.0
    assert position.az_deg < 15 or position.az_deg > 345


def test_sun_is_far_below_horizon_on_sample_night():
    sun = sun_horizontal(NIGHT, LAT, LON)
    assert sun.alt_deg < -30.0


def test_window_rejects_south_and_low_altitude():
    from seestar_plan.sky import Horizontal

    assert in_north_window(Horizontal(40, 0), 10, 85)
    assert in_north_window(Horizontal(40, 270), 10, 85)
    assert in_north_window(Horizontal(40, 90), 10, 85)
    assert not in_north_window(Horizontal(40, 180), 10, 85)
    assert not in_north_window(Horizontal(9.9, 0), 10, 85)
    assert not in_north_window(Horizontal(85.1, 0), 10, 85)


def test_visible_intervals_cover_a_known_hour():
    # 北極星は北の窓の高度・方位に一晩とどまる。
    polaris = CATALOG[0]
    start = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
    end = datetime(2026, 10, 12, 13, 0, tzinfo=timezone.utc)
    intervals = visible_intervals(start, end, LAT, LON, polaris.ra_deg, polaris.dec_deg, 10, 85)
    assert len(intervals) == 1
    assert abs(intervals[0].minutes - 60) < 1.5
