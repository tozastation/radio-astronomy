"""赤経・赤緯と太陽・月の位置から、ベランダの窓に入る時刻を求める。

地平座標は Meeus の恒星時、太陽は黄経の概略式、月は天体暦（Astronomical Almanac）の
低精度式を使う。計画の精度は 1 度程度で足りる。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

STEP = timedelta(minutes=1)
SYNODIC_MONTH_DAYS = 29.530588853
OBLIQUITY_J2000_DEG = 23.439


@dataclass(frozen=True)
class Horizontal:
    alt_deg: float
    az_deg: float


@dataclass(frozen=True)
class Window:
    """置き場所から見える空。方位は az_from から時計回りに az_to まで（北 0 度・東 90 度）。"""

    key: str
    name: str
    az_from_deg: float
    az_to_deg: float
    floor_deg: float
    ceiling_deg: float

    def contains(self, position: Horizontal) -> bool:
        azimuth = position.az_deg % 360.0
        start = self.az_from_deg % 360.0
        stop = self.az_to_deg % 360.0
        if start <= stop:
            azimuth_ok = start <= azimuth <= stop
        else:
            azimuth_ok = azimuth >= start or azimuth <= stop
        return azimuth_ok and self.floor_deg <= position.alt_deg <= self.ceiling_deg


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


def _ecliptic_to_radec_deg(longitude_deg: float, latitude_deg: float) -> tuple[float, float]:
    lam = math.radians(longitude_deg)
    beta = math.radians(latitude_deg)
    eps = math.radians(OBLIQUITY_J2000_DEG)
    ra = math.atan2(
        math.sin(lam) * math.cos(eps) - math.tan(beta) * math.sin(eps),
        math.cos(lam),
    )
    dec = math.asin(
        math.sin(beta) * math.cos(eps) + math.cos(beta) * math.sin(eps) * math.sin(lam)
    )
    return math.degrees(ra) % 360.0, math.degrees(dec)


def sun_ecliptic_longitude_deg(moment: datetime) -> float:
    days = julian_day(moment) - 2451545.0
    mean_longitude = (280.460 + 0.9856474 * days) % 360.0
    mean_anomaly = math.radians((357.528 + 0.9856003 * days) % 360.0)
    return (
        mean_longitude + 1.915 * math.sin(mean_anomaly) + 0.020 * math.sin(2.0 * mean_anomaly)
    ) % 360.0


def _sun_radec_deg(moment: datetime) -> tuple[float, float]:
    """太陽の視赤経・視赤緯（度）。"""
    return _ecliptic_to_radec_deg(sun_ecliptic_longitude_deg(moment), 0.0)


def sun_horizontal(moment: datetime, lat_deg: float, lon_deg: float) -> Horizontal:
    ra, dec = _sun_radec_deg(moment)
    return horizontal(moment, lat_deg, lon_deg, ra, dec)


@dataclass(frozen=True)
class MoonEcliptic:
    longitude_deg: float
    latitude_deg: float
    parallax_deg: float


def _sin_deg(value: float) -> float:
    return math.sin(math.radians(value))


def _cos_deg(value: float) -> float:
    return math.cos(math.radians(value))


def moon_ecliptic(moment: datetime) -> MoonEcliptic:
    """地心の黄経・黄緯と赤道地平視差。誤差は黄経 0.3 度、黄緯 0.2 度程度。"""
    t = (julian_day(moment) - 2451545.0) / 36525.0
    longitude = (
        218.32
        + 481267.881 * t
        + 6.29 * _sin_deg(135.0 + 477198.87 * t)
        - 1.27 * _sin_deg(259.3 - 413335.36 * t)
        + 0.66 * _sin_deg(235.7 + 890534.22 * t)
        + 0.21 * _sin_deg(269.9 + 954397.74 * t)
        - 0.19 * _sin_deg(357.5 + 35999.05 * t)
        - 0.11 * _sin_deg(186.5 + 966404.03 * t)
    )
    latitude = (
        5.13 * _sin_deg(93.3 + 483202.02 * t)
        + 0.28 * _sin_deg(228.2 + 960400.89 * t)
        - 0.28 * _sin_deg(318.3 + 6003.15 * t)
        - 0.17 * _sin_deg(217.6 - 407332.21 * t)
    )
    parallax = (
        0.9508
        + 0.0518 * _cos_deg(135.0 + 477198.87 * t)
        + 0.0095 * _cos_deg(259.3 - 413335.36 * t)
        + 0.0078 * _cos_deg(235.7 + 890534.22 * t)
        + 0.0028 * _cos_deg(269.9 + 954397.74 * t)
    )
    return MoonEcliptic(longitude % 360.0, latitude, parallax)


def moon_radec_deg(moment: datetime) -> tuple[float, float]:
    moon = moon_ecliptic(moment)
    return _ecliptic_to_radec_deg(moon.longitude_deg, moon.latitude_deg)


def moon_horizontal(moment: datetime, lat_deg: float, lon_deg: float) -> Horizontal:
    """地表から見た月。視差（約 1 度）だけ地心の高度より低く見える。"""
    moon = moon_ecliptic(moment)
    ra, dec = _ecliptic_to_radec_deg(moon.longitude_deg, moon.latitude_deg)
    geocentric = horizontal(moment, lat_deg, lon_deg, ra, dec)
    alt = geocentric.alt_deg - moon.parallax_deg * math.cos(math.radians(geocentric.alt_deg))
    return Horizontal(alt, geocentric.az_deg)


def moon_phase_ratio(moment: datetime) -> float:
    """太陽から月までの黄経差を 360 度で割った値。0 が新月、0.5 が満月。"""
    elongation = moon_ecliptic(moment).longitude_deg - sun_ecliptic_longitude_deg(moment)
    return (elongation % 360.0) / 360.0


def moon_illumination_ratio(moment: datetime) -> float:
    """輝面比。位相角を 180 度から離角を引いた値で近似する。"""
    moon = moon_ecliptic(moment)
    delta = math.radians(moon.longitude_deg - sun_ecliptic_longitude_deg(moment))
    cos_elongation = math.cos(math.radians(moon.latitude_deg)) * math.cos(delta)
    return (1.0 - cos_elongation) / 2.0


def next_phase(moment: datetime, phase: float, horizon: timedelta = timedelta(days=31)) -> datetime:
    """moment より後で、黄経差が phase（0 は新月、0.5 は満月）を初めて越える時刻。"""
    step = timedelta(hours=1)

    def offset(at: datetime) -> float:
        return (moon_phase_ratio(at) - phase + 0.5) % 1.0 - 0.5

    previous_at = moment
    previous = offset(moment)
    probe = moment + step
    while probe <= moment + horizon:
        current = offset(probe)
        if previous < 0.0 <= current:
            low, high = previous_at, probe
            for _ in range(20):
                middle = low + (high - low) / 2
                if offset(middle) < 0.0:
                    low = middle
                else:
                    high = middle
            return high
        previous_at, previous = probe, current
        probe += step
    raise RuntimeError("月相が見つかりません")


def next_altitude_crossing(
    moment: datetime,
    lat_deg: float,
    lon_deg: float,
    rising: bool,
    horizon: timedelta = timedelta(hours=30),
) -> datetime | None:
    """moment より後で、月の高度が 0 度を上向き（rising）または下向きに越える最初の分。"""
    previous = moon_horizontal(moment, lat_deg, lon_deg).alt_deg
    probe = moment.replace(second=0, microsecond=0) + STEP
    while probe <= moment + horizon:
        current = moon_horizontal(probe, lat_deg, lon_deg).alt_deg
        if rising and previous < 0.0 <= current:
            return probe
        if not rising and previous >= 0.0 > current:
            return probe
        previous = current
        probe += STEP
    return None


def angular_separation_deg(ra1_deg: float, dec1_deg: float, ra2_deg: float, dec2_deg: float) -> float:
    ra1, dec1, ra2, dec2 = map(math.radians, (ra1_deg, dec1_deg, ra2_deg, dec2_deg))
    cos_sep = math.sin(dec1) * math.sin(dec2) + math.cos(dec1) * math.cos(dec2) * math.cos(ra1 - ra2)
    return math.degrees(math.acos(min(1.0, max(-1.0, cos_sep))))


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
    window: Window,
) -> list[Interval]:
    """1 分刻みで窓の中にいる連続区間。終端は最後の内側サンプルの 1 分後。"""
    intervals: list[Interval] = []
    open_at: datetime | None = None
    moment = start
    while moment < end:
        position = horizontal(moment, lat_deg, lon_deg, ra_deg, dec_deg)
        inside = window.contains(position)
        if inside and open_at is None:
            open_at = moment
        if not inside and open_at is not None:
            intervals.append(Interval(open_at, moment))
            open_at = None
        moment += STEP
    if open_at is not None:
        intervals.append(Interval(open_at, end))
    return intervals
