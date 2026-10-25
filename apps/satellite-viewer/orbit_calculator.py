#!/usr/bin/env python3
"""
SGP4 Orbit Calculator and Balcony Sky Window determination.
Computes Azimuth, Elevation, Range, and Sky Window visibility.
"""

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import math
from typing import List, Optional, Tuple
from sgp4.api import Satrec, WGS72


@dataclass
class TrajectoryPoint:
    t: int                # 経過時間（秒）
    timestamp_utc: str    # ISO 8601 UTC
    az: float             # 方位角（度: 0〜360, 北=0, 東=90）
    el: float             # 仰角（度: -90〜90）
    range_km: float       # 観測者からの斜距離（km）
    in_window: bool       # 北向きベランダのスカイウィンドウ内か
    has_image: bool = False  # 復調画像が存在する区間か


class OrbitCalculator:
    # WGS84 楕円体定数
    WGS84_A = 6378137.0                      # 地球長半径 [m]
    WGS84_F = 1.0 / 298.257223563            # 扁平率
    WGS84_E2 = 2 * WGS84_F - WGS84_F ** 2   # 離心率の2乗

    def __init__(
        self,
        lat_deg: float = 35.6895,
        lon_deg: float = 139.6917,
        alt_m: float = 30.0,
        window_az_min: float = 250.0,
        window_az_max: float = 110.0,
        window_el_min: float = 25.0,
        window_el_max: float = 60.0,
    ):
        self.lat_deg = lat_deg
        self.lon_deg = lon_deg
        self.alt_m = alt_m
        self.lat_rad = math.radians(lat_deg)
        self.lon_rad = math.radians(lon_deg)

        self.window_az_min = window_az_min
        self.window_az_max = window_az_max
        self.window_el_min = window_el_min
        self.window_el_max = window_el_max

        # 観測地点の ECEF 座標を事前計算 [km]
        self.obs_ecef_km = self._geodetic_to_ecef(self.lat_rad, self.lon_rad, self.alt_m)

    def is_in_sky_window(self, az_deg: float, el_deg: float) -> bool:
        """
        北向きベランダの開口窓（スカイウィンドウ）内にあるかを判定。
        - 方位角: 250°〜360° または 0°〜110°（西〜北〜東）
        - 仰角: 25°〜60°（手すり下限〜上階スラブ庇上限）
        """
        if not (self.window_el_min <= el_deg <= self.window_el_max):
            return False

        # 方位角判定（0度をまたぐ区間 [250, 360] ∪ [0, 110]）
        norm_az = az_deg % 360.0
        if self.window_az_min <= norm_az or norm_az <= self.window_az_max:
            return True
        return False

    def calculate_trajectory(
        self,
        tle_line1: str,
        tle_line2: str,
        start_time: datetime,
        duration_seconds: int,
        step_seconds: int = 1
    ) -> List[TrajectoryPoint]:
        """
        指定された TLE と開始時刻から、1秒ごとの Az/El/Range 軌道時系列を計算。
        """
        satrec = Satrec.twoline2rv(tle_line1.strip(), tle_line2.strip(), WGS72)
        points: List[TrajectoryPoint] = []

        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=timezone.utc)

        for sec in range(0, duration_seconds + 1, step_seconds):
            current_dt = start_time + timedelta(seconds=sec)
            jd, fr = self._datetime_to_jd(current_dt)

            error_code, r_teme, v_teme = satrec.sgp4(jd, fr)
            if error_code != 0:
                continue

            # r_teme は km 単位の [X, Y, Z]
            # TEME から ECEF への回転
            gmst = self._calc_gmst_rad(jd, fr)
            r_ecef = self._teme_to_ecef(r_teme, gmst)

            # 観測者基準の Topocentric ENU 座標系へ変換
            az_deg, el_deg, range_km = self._ecef_to_az_el_range(r_ecef, self.obs_ecef_km)
            in_window = self.is_in_sky_window(az_deg, el_deg)

            points.append(
                TrajectoryPoint(
                    t=sec,
                    timestamp_utc=current_dt.isoformat(),
                    az=round(az_deg, 2),
                    el=round(el_deg, 2),
                    range_km=round(range_km, 1),
                    in_window=in_window
                )
            )

        return points

    def _geodetic_to_ecef(self, lat_rad: float, lon_rad: float, alt_m: float) -> Tuple[float, float, float]:
        """測地緯度・経度・標高から ECEF [km] を算出"""
        sin_lat = math.sin(lat_rad)
        cos_lat = math.cos(lat_rad)
        sin_lon = math.sin(lon_rad)
        cos_lon = math.cos(lon_rad)

        n = self.WGS84_A / math.sqrt(1.0 - self.WGS84_E2 * sin_lat ** 2)
        x = (n + alt_m) * cos_lat * cos_lon / 1000.0
        y = (n + alt_m) * cos_lat * sin_lon / 1000.0
        z = (n * (1.0 - self.WGS84_E2) + alt_m) * sin_lat / 1000.0
        return (x, y, z)

    @staticmethod
    def _datetime_to_jd(dt: datetime) -> Tuple[float, float]:
        """UTC datetime を SGP4 形式のユリウス日 (jd, fraction) に変換"""
        year = dt.year
        month = dt.month
        day = dt.day
        hour = dt.hour
        minute = dt.minute
        second = dt.second + dt.microsecond / 1e6

        if month <= 2:
            year -= 1
            month += 12

        a = math.floor(year / 100)
        b = 2 - a + math.floor(a / 4)
        jd_day = math.floor(365.25 * (year + 4716)) + math.floor(30.6001 * (month + 1)) + day + b - 1524.5
        day_fraction = (hour + minute / 60.0 + second / 3600.0) / 24.0

        return jd_day, day_fraction

    @staticmethod
    def _calc_gmst_rad(jd: float, fr: float) -> float:
        """グリニッジ平均恒星時 (GMST) [rad] の算出"""
        t_ut1 = (jd + fr - 2451545.0) / 36525.0
        gmst_sec = (
            24110.54841
            + 8640184.812866 * t_ut1
            + 0.093104 * (t_ut1 ** 2)
            - 6.2e-6 * (t_ut1 ** 3)
        )
        gmst_sec += (fr * 86400.0) * 1.002737909350795
        gmst_rad = ((gmst_sec % 86400.0) / 86400.0) * 2.0 * math.pi
        return gmst_rad

    @staticmethod
    def _teme_to_ecef(r_teme: Tuple[float, float, float], gmst_rad: float) -> Tuple[float, float, float]:
        """TEME 座標から ECEF 座標への Z 軸回転 [km]"""
        cos_g = math.cos(gmst_rad)
        sin_g = math.sin(gmst_rad)
        x_ecef = r_teme[0] * cos_g + r_teme[1] * sin_g
        y_ecef = -r_teme[0] * sin_g + r_teme[1] * cos_g
        z_ecef = r_teme[2]
        return (x_ecef, y_ecef, z_ecef)

    def _ecef_to_az_el_range(
        self,
        sat_ecef_km: Tuple[float, float, float],
        obs_ecef_km: Tuple[float, float, float]
    ) -> Tuple[float, float, float]:
        """ECEF 差分ベクトルから観測者局所 ENU を経由して Az, El, Range を算出"""
        dx = sat_ecef_km[0] - obs_ecef_km[0]
        dy = sat_ecef_km[1] - obs_ecef_km[1]
        dz = sat_ecef_km[2] - obs_ecef_km[2]

        sin_lat = math.sin(self.lat_rad)
        cos_lat = math.cos(self.lat_rad)
        sin_lon = math.sin(self.lon_rad)
        cos_lon = math.cos(self.lon_rad)

        # ENU 座標
        e = -sin_lon * dx + cos_lon * dy
        n = -sin_lat * cos_lon * dx - sin_lat * sin_lon * dy + cos_lat * dz
        u = cos_lat * cos_lon * dx + cos_lat * sin_lon * dy + sin_lat * dz

        range_km = math.sqrt(e ** 2 + n ** 2 + u ** 2)
        if range_km < 1e-6:
            return 0.0, 90.0, 0.0

        el_deg = math.degrees(math.asin(max(-1.0, min(1.0, u / range_km))))
        az_deg = math.degrees(math.atan2(e, n)) % 360.0

        return az_deg, el_deg, range_km
