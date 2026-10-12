from datetime import datetime, timezone

from seestar_plan.catalog import CATALOG
from seestar_plan.sky import (
    culminates_north,
    culmination_altitude_deg,
    horizontal,
    Horizontal,
    Window,
    moon_horizontal,
    moon_illumination_ratio,
    next_altitude_crossing,
    next_phase,
    sun_horizontal,
    visible_intervals,
)

LAT = 35.6895
LON = 139.6917
NIGHT = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
NORTH = Window("north", "北の長辺", 270, 90, 10, 85)
EAST = Window("east", "東の短辺", 45, 135, 20, 85)


def test_culmination_side_matches_declination():
    assert culminates_north(41.3, LAT)
    assert culminates_north(61.6, LAT)
    assert not culminates_north(24.1, LAT)
    assert not culminates_north(-5.4, LAT)
    assert abs(culmination_altitude_deg(41.3, LAT) - (90.0 - (41.3 - LAT))) < 1e-9
    assert abs(culmination_altitude_deg(-5.4, LAT) - (90.0 - (LAT - (-5.4)))) < 1e-9


def test_polaris_stays_near_latitude_and_north():
    polaris = CATALOG[0]
    position = horizontal(NIGHT, LAT, LON, *polaris.radec_deg(NIGHT))
    assert abs(position.alt_deg - LAT) < 2.0
    assert position.az_deg < 15 or position.az_deg > 345


def test_sun_is_far_below_horizon_on_sample_night():
    sun = sun_horizontal(NIGHT, LAT, LON)
    assert sun.alt_deg < -30.0


def test_north_window_wraps_through_north():
    assert NORTH.contains(Horizontal(40, 0))
    assert NORTH.contains(Horizontal(40, 270))
    assert NORTH.contains(Horizontal(40, 90))
    assert not NORTH.contains(Horizontal(40, 180))
    assert not NORTH.contains(Horizontal(9.9, 0))
    assert not NORTH.contains(Horizontal(85.1, 0))


def test_east_window_has_its_own_floor():
    assert EAST.contains(Horizontal(30, 120))
    assert not EAST.contains(Horizontal(15, 90))
    assert not EAST.contains(Horizontal(30, 300))


def test_visible_intervals_cover_a_known_hour():
    # 北極星は北の窓の高度・方位に一晩とどまる。
    polaris = CATALOG[0]
    start = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
    end = datetime(2026, 10, 12, 13, 0, tzinfo=timezone.utc)
    intervals = visible_intervals(start, end, LAT, LON, *polaris.radec_deg(start), NORTH)
    assert len(intervals) == 1
    assert abs(intervals[0].minutes - 60) < 1.5


# 参照値は PyEphem 4.2（気圧 0、地表から見た値）で求めた。
MOON_AT = datetime(2026, 10, 12, 8, 0, tzinfo=timezone.utc)


def test_moon_position_matches_reference():
    moon = moon_horizontal(MOON_AT, LAT, LON)
    assert abs(moon.alt_deg - 6.26) < 0.5
    assert abs(moon.az_deg - 240.40) < 0.5
    assert abs(moon_illumination_ratio(MOON_AT) - 0.0313) < 0.01


def test_next_new_and_full_moon_match_reference():
    new_moon = datetime(2026, 11, 9, 7, 2, tzinfo=timezone.utc)
    full_moon = datetime(2026, 10, 26, 4, 12, tzinfo=timezone.utc)
    assert abs((next_phase(MOON_AT, 0.0) - new_moon).total_seconds()) < 3600
    assert abs((next_phase(MOON_AT, 0.5) - full_moon).total_seconds()) < 3600


def test_moonrise_and_moonset_match_reference():
    rise = datetime(2026, 10, 12, 23, 16, tzinfo=timezone.utc)
    moonset = datetime(2026, 10, 12, 8, 37, tzinfo=timezone.utc)
    assert abs((next_altitude_crossing(MOON_AT, LAT, LON, rising=True) - rise).total_seconds()) < 600
    assert abs((next_altitude_crossing(MOON_AT, LAT, LON, rising=False) - moonset).total_seconds()) < 600
