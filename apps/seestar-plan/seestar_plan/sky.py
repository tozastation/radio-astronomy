"""赤経・赤緯と太陽位置から、北の窓に入る時刻を求める。

地平座標は Meeus の恒星時と、黄経から求める太陽の概位を使う。
計画の精度は 1 度程度で足りる。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

NORTH_AZ_WEST = 270.0
NORTH_AZ_EAST = 90.0
STEP = timedelta(minutes=1)


@dataclass(frozen=True)
class Horizontal:
    alt_deg: float
    az_deg: float


@dataclass(frozen=True)
class Interval:
    start: datetime
    end: datetime

    @property
    def minutes(self) -> float:
        return (self.end - self.start).total_seconds() / 60.0


def julian_day(moment: datetime) -> float:
    """世界時のユリウス日。moment はタイムゾーン付き。"""
    utc = moment.astimezone(timezone.utc)
    year, month = utc.year, utc.month
    day = (
        utc.day
        + (utc.hour + (utc.minute + (utc.second + utc.microsecond / 1e6) / 60.0) / 60.0)
        / 24.0
    )
    if month <= 2:
        year -= 1
        month += 12
    century = year // 100
    gregorian = 2 - century + century // 4
    return (
        math.floor(365.25 * (year + 4716))
        + math.floor(30.6001 * (month + 1))
        + day
        + gregorian
        - 1524.5
    )


def local_sidereal_deg(moment: datetime, lon_deg: float) -> float:
    """地方恒星時（度）。東経が正。"""
    jd = julian_day(moment)
    centuries = (jd - 2451545.0) / 36525.0
    gmst = (
        280.46061837
        + 360.98564736629 * (jd - 2451545.0)
        + 0.000387933 * centuries * centuries
        - centuries**3 / 38710000.0
    )
    return (gmst + lon_deg) % 360.0


def horizontal(moment: datetime, lat_deg: float, lon_deg: float, ra_deg: float, dec_deg: float) -> Horizontal:
    """赤道座標を、北 0 度・東 90 度の地平座標へ変換する。"""
    lat = math.radians(lat_deg)
    dec = math.radians(dec_deg)
    hour_angle = math.radians(local_sidereal_deg(moment, lon_deg) - ra_deg)
    sin_alt = math.sin(lat) * math.sin(dec) + math.cos(lat) * math.cos(dec) * math.cos(hour_angle)
    sin_alt = min(1.0, max(-1.0, sin_alt))
    alt = math.asin(sin_alt)
    az = math.atan2(
        -math.cos(dec) * math.sin(hour_angle),
        math.sin(dec) * math.cos(lat) - math.cos(dec) * math.cos(hour_angle) * math.sin(lat),
    )
    return Horizontal(math.degrees(alt), math.degrees(az) % 360.0)


def culminates_north(dec_deg: float, lat_deg: float) -> bool:
    """赤緯が緯度以上なら、最も高くなるとき天頂の北側を通る。"""
    return dec_deg >= lat_deg


def culmination_altitude_deg(dec_deg: float, lat_deg: float) -> float:
    if culminates_north(dec_deg, lat_deg):
        return 90.0 - (dec_deg - lat_deg)
    return 90.0 - (lat_deg - dec_deg)


def _sun_radec_deg(moment: datetime) -> tuple[float, float]:
    """太陽の視赤経・視赤緯（度）。黄経の概略式。"""
    jd = julian_day(moment)
    days = jd - 2451545.0
    mean_longitude = (280.460 + 0.9856474 * days) % 360.0
    mean_anomaly = math.radians((357.528 + 0.9856003 * days) % 360.0)
    ecliptic = math.radians(
        mean_longitude + 1.915 * math.sin(mean_anomaly) + 0.020 * math.sin(2.0 * mean_anomaly)
    )
    obliquity = math.radians(23.439 - 0.0000004 * days)
    ra = math.atan2(math.cos(obliquity) * math.sin(ecliptic), math.cos(ecliptic))
    dec = math.asin(math.sin(obliquity) * math.sin(ecliptic))
    return math.degrees(ra) % 360.0, math.degrees(dec)


def sun_horizontal(moment: datetime, lat_deg: float, lon_deg: float) -> Horizontal:
    ra, dec = _sun_radec_deg(moment)
    return horizontal(moment, lat_deg, lon_deg, ra, dec)


def in_north_window(position: Horizontal, alt_min_deg: float, alt_max_deg: float) -> bool:
    """方位 270 度から 90 度、仰角は床以上かつ天井以下。"""
    azimuth = position.az_deg % 360.0
    azimuth_ok = azimuth >= NORTH_AZ_WEST or azimuth <= NORTH_AZ_EAST
    altitude_ok = alt_min_deg <= position.alt_deg <= alt_max_deg
    return azimuth_ok and altitude_ok


def astronomical_night(
    now: datetime, lat_deg: float, lon_deg: float, sun_alt_limit: float = -18.0
) -> tuple[datetime, datetime]:
    """now を含む、または次に来る、太陽高度が sun_alt_limit 未満の区間。"""
    if now.tzinfo is None:
        raise ValueError("now にはタイムゾーンが必要です")
    probe = (now - timedelta(hours=20)).replace(second=0, microsecond=0)
    end = now + timedelta(hours=36)
    below: list[datetime] = []
    while probe <= end:
        if sun_horizontal(probe, lat_deg, lon_deg).alt_deg < sun_alt_limit:
            below.append(probe)
        probe += STEP
    if not below:
        raise RuntimeError("天文薄明の夜が見つかりません")

    runs: list[tuple[datetime, datetime]] = []
    run_start = below[0]
    previous = below[0]
    for moment in below[1:]:
        if moment - previous > STEP * 1.5:
            runs.append((run_start, previous + STEP))
            run_start = moment
        previous = moment
    runs.append((run_start, previous + STEP))

    for start, stop in runs:
        if start <= now < stop:
            return start, stop
    for start, stop in runs:
        if start > now:
            return start, stop
    return runs[-1]


def visible_intervals(
    start: datetime,
    end: datetime,
    lat_deg: float,
    lon_deg: float,
    ra_deg: float,
    dec_deg: float,
    alt_min_deg: float,
    alt_max_deg: float,
) -> list[Interval]:
    """1 分刻みで窓の中にいる連続区間。終端は最後の内側サンプルの 1 分後。"""
    intervals: list[Interval] = []
    open_at: datetime | None = None
    moment = start
    while moment < end:
        position = horizontal(moment, lat_deg, lon_deg, ra_deg, dec_deg)
        inside = in_north_window(position, alt_min_deg, alt_max_deg)
        if inside and open_at is None:
            open_at = moment
        if not inside and open_at is not None:
            intervals.append(Interval(open_at, moment))
            open_at = None
        moment += STEP
    if open_at is not None:
        intervals.append(Interval(open_at, end))
    return intervals
