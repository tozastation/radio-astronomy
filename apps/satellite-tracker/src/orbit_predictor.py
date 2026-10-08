from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple
import numpy as np
from skyfield.api import EarthSatellite, load, wgs84
from skyfield.timelib import Time


SPEED_OF_LIGHT_M_S = 299792458.0


@dataclass
class SatellitePosition:
    elevation_deg: float
    azimuth_deg: float
    range_km: float
    radial_velocity_km_s: float
    doppler_shift_hz: float


@dataclass
class PassInfo:
    satellite_name: str
    aos_time: datetime
    tca_time: datetime
    los_time: datetime
    max_elevation_deg: float
    frequency_hz: float


class OrbitPredictor:
    def __init__(
        self,
        observer_lat: float,
        observer_lon: float,
        observer_elev_m: float = 0.0,
        balcony_facing: str = "NORTH",
        min_elevation_deg: float = 10.0,
    ):
        self.observer_lat = observer_lat
        self.observer_lon = observer_lon
        self.observer_elev_m = observer_elev_m
        self.balcony_facing = balcony_facing.upper()
        self.min_elevation_deg = min_elevation_deg

        # Skyfield timescale & observer location
        self.ts = load.timescale()
        self.location = wgs84.latlon(
            latitude_degrees=self.observer_lat,
            longitude_degrees=self.observer_lon,
            elevation_m=self.observer_elev_m,
        )

    def is_in_view(self, elevation_deg: float, azimuth_deg: float) -> bool:
        """指定した仰角・方位角がベランダの視界内にあるかを判定する"""
        if elevation_deg < self.min_elevation_deg:
            return False

        if self.balcony_facing == "NORTH":
            # 北向きベランダ: 方位角 270°〜360° または 0°〜90° (西〜北〜東)
            az = azimuth_deg % 360.0
            return (az >= 270.0) or (az <= 90.0)

        # 全天開放の場合
        return True

    def calculate_position(
        self,
        satellite_name: str,
        tle_line1: str,
        tle_line2: str,
        frequency_hz: float,
        timestamp: datetime,
    ) -> SatellitePosition:
        """指定時刻における衛星の仰角、方位角、距離、視線速度、ドップラー偏移を算出する"""
        sat = EarthSatellite(tle_line1, tle_line2, satellite_name, self.ts)
        t = self.ts.from_datetime(timestamp)

        diff = sat - self.location
        topocentric = diff.at(t)

        el, az, d = topocentric.altaz()
        elevation_deg = float(el.degrees)
        azimuth_deg = float(az.degrees)
        range_km = float(d.km)

        # 視線速度 (Radial velocity) の計算
        # 微小時間 dt 前後の距離差分から v_r = dr / dt を数値微分で高精度計算
        dt_sec = 0.5
        t_plus = self.ts.from_datetime(timestamp + timedelta(seconds=dt_sec))
        t_minus = self.ts.from_datetime(timestamp - timedelta(seconds=dt_sec))

        d_plus = (sat - self.location).at(t_plus).distance().km
        d_minus = (sat - self.location).at(t_minus).distance().km

        radial_velocity_km_s = (d_plus - d_minus) / (2.0 * dt_sec)

        # ドップラーシフト: Delta_f = - f0 * (vr / c)
        vr_m_s = radial_velocity_km_s * 1000.0
        doppler_shift_hz = - frequency_hz * (vr_m_s / SPEED_OF_LIGHT_M_S)

        return SatellitePosition(
            elevation_deg=elevation_deg,
            azimuth_deg=azimuth_deg,
            range_km=range_km,
            radial_velocity_km_s=radial_velocity_km_s,
            doppler_shift_hz=doppler_shift_hz,
        )

    def get_next_pass(
        self,
        satellite_name: str,
        tle_line1: str,
        tle_line2: str,
        frequency_hz: float,
        start_time: datetime,
        search_hours: float = 24.0,
        step_seconds: int = 20,
    ) -> Optional[PassInfo]:
        """北天視界内に現れる次期パス（AOS, TCA, LOS）を探索する"""
        current_time = start_time
        end_time = start_time + timedelta(hours=search_hours)

        in_pass = False
        aos_time: Optional[datetime] = None
        tca_time: Optional[datetime] = None
        max_el = -90.0

        while current_time <= end_time:
            pos = self.calculate_position(
                satellite_name=satellite_name,
                tle_line1=tle_line1,
                tle_line2=tle_line2,
                frequency_hz=frequency_hz,
                timestamp=current_time,
            )

            is_visible = self.is_in_view(pos.elevation_deg, pos.azimuth_deg)

            if not in_pass and is_visible:
                # パス開始 (AOS)
                in_pass = True
                aos_time = current_time
                max_el = pos.elevation_deg
                tca_time = current_time
            elif in_pass and is_visible:
                # パス進行中
                if pos.elevation_deg > max_el:
                    max_el = pos.elevation_deg
                    tca_time = current_time
            elif in_pass and not is_visible:
                # パス終了 (LOS)
                los_time = current_time
                assert aos_time is not None
                assert tca_time is not None
                return PassInfo(
                    satellite_name=satellite_name,
                    aos_time=aos_time,
                    tca_time=tca_time,
                    los_time=los_time,
                    max_elevation_deg=max_el,
                    frequency_hz=frequency_hz,
                )

            current_time += timedelta(seconds=step_seconds)

        return None
