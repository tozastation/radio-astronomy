#!/usr/bin/env python3
"""
SGP4 Orbit Calculator and Balcony Sky Window determination.
Computes Azimuth, Elevation, Range, and Sky Window visibility.
"""

from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
import math
import os
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Any
from sgp4.api import Satrec, WGS72


DEFAULT_TARGET_TLES: Dict[str, Tuple[str, str]] = {
    "METEOR-M2 3": (
        "1 57166U 23091A   26296.25648911 -.00000011  00000+0  13974-4 0  9991",
        "2 57166  98.5934 348.5131 0004559 145.3247 214.8230 14.24054780172745",
    ),
    "METEOR-M2 4": (
        "1 59051U 24039A   26296.27566674  .00000005  00000+0  21993-4 0  9998",
        "2 59051  98.7198 254.0636 0007418 151.3257 208.8330 14.22441005137473",
    ),
    "ISS (ZARYA)": (
        "1 25544U 98067A   26294.04791053  .00009575  00000+0  18188-3 0  9996",
        "2 25544  51.6327  37.2623 0006759 289.5321  70.4938 15.48967427591263",
    ),
    "FUNCUBE-1 (AO-73)": (
        "1 39444U 13066AE  26294.20736285  .00008239  00000+0  48957-3 0  9993",
        "2 39444  97.8439 268.5385 0036409 132.3370 228.0953 15.10994851698683",
    ),
}


def load_tle_for_satellite(satellite_name: str, tle_path: Optional[str] = None) -> Optional[Tuple[str, str]]:
    """
    指定された衛星名に対応する TLE 2行を取得。
    ローカルファイル、環境変数、または組み込みの DEFAULT_TARGET_TLES から検索。
    """
    candidates = []
    if tle_path:
        candidates.append(Path(tle_path))
    env_path = os.getenv("TLE_FILE_PATH")
    if env_path:
        candidates.append(Path(env_path))
    candidates.extend([
        Path("/root/.config/satdump/satdump_tles.txt"),
        Path("apps/satellite-analyzer/assets/satdump_tles.txt"),
        Path("/assets/satdump_tles.txt"),
        Path("assets/satdump_tles.txt"),
        Path("satdump_tles.txt"),
    ])

    norm_target = satellite_name.strip().upper()
    for cand in candidates:
        try:
            if cand.is_file():
                with open(cand, "r", encoding="utf-8", errors="replace") as f:
                    lines = [line.strip() for line in f if line.strip()]
                    idx = 0
                    while idx < len(lines):
                        cur = lines[idx].upper()
                        if cur == norm_target or cur.startswith(norm_target):
                            if idx + 2 < len(lines):
                                l1 = lines[idx + 1]
                                l2 = lines[idx + 2]
                                if l1.startswith("1 ") and l2.startswith("2 "):
                                    return (l1, l2)
                        idx += 1
        except OSError:
            pass

    # デフォルト辞書から検索
    for name, tles in DEFAULT_TARGET_TLES.items():
        if name.upper() == norm_target or norm_target in name.upper() or name.upper() in norm_target:
            return tles

    return None


@dataclass
class TrajectoryPoint:
    t: int                # 経過時間（秒）
    timestamp_utc: str    # ISO 8601 UTC
    az: float             # 方位角（度: 0〜360, 北=0, 東=90）
    el: float             # 仰角（度: -90〜90）
    range_km: float       # 観測者からの斜距離（km）
    in_window: bool       # 北向きベランダのスカイウィンドウ内か
    has_image: bool = False  # 復調画像が存在する区間か


@dataclass
class FuturePass:
    satellite: str
    aos_time: str
    tca_time: str
    los_time: str
    max_elevation: float
    max_elevation_azimuth: float
    window_duration_seconds: int
    quality_score: str
    success_probability: Optional[float] = None
    history_summary: Optional[str] = None
    time_drift_seconds: Optional[float] = None



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

    def is_in_sky_window(
        self,
        az_deg: float,
        el_deg: float,
        empirical_mask: Optional[Dict[int, Tuple[float, float]]] = None
    ) -> bool:
        """
        北向きベランダの開口窓（スカイウィンドウ）内にあるかを判定。
        empirical_mask が指定されている場合、方位角ビン (10°ステップ: 0〜35) に対応する
        実測境界 (el_min, el_max) を用いて動的に判定。
        未登録方位または empirical_mask=None の場合は、デフォルトの幾何学的境界で判定。
        - 方位角: 250°〜360° または 0°〜110°（西〜北〜東）
        - 仰角: 25°〜60°（手すり下限〜上階スラブ庇上限）
        """
        norm_az = az_deg % 360.0
        az_bin = int(norm_az // 10.0) % 36

        if empirical_mask and az_bin in empirical_mask:
            el_min, el_max = empirical_mask[az_bin]
            return el_min <= el_deg <= el_max

        if not (self.window_el_min <= el_deg <= self.window_el_max):
            return False

        # 方位角判定（0度をまたぐ区間 [250, 360] ∪ [0, 110]）
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

    def find_future_passes(
        self,
        targets: List[Tuple[str, str, str]],  # (name, tle1, tle2)
        start_time: datetime,
        horizon_hours: int = 24,
        step_seconds: int = 30,
        history_stats: Optional[Dict[str, Any]] = None,
        empirical_mask: Optional[Dict[int, Tuple[float, float]]] = None,
        time_drifts: Optional[Dict[str, float]] = None,
    ) -> List[FuturePass]:
        """
        向こう horizon_hours 時間における各衛星のパスを予測し、
        スカイウィンドウ通過諸元および品質スコア・実績連動期待確率・タイムドリフト補正を計算。
        """
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=timezone.utc)

        all_passes: List[FuturePass] = []
        total_steps = int(horizon_hours * 3600 / step_seconds)

        for name, l1, l2 in targets:
            try:
                satrec = Satrec.twoline2rv(l1.strip(), l2.strip(), WGS72)
            except Exception:
                continue

            drift_sec = time_drifts.get(name, 0.0) if time_drifts else 0.0
            recorded_drift = round(drift_sec, 1) if (time_drifts and name in time_drifts) else None

            in_pass = False
            pass_start_dt: Optional[datetime] = None
            max_el = -90.0
            max_el_az = 0.0
            tca_dt: Optional[datetime] = None
            window_duration = 0

            for step in range(total_steps + 1):
                cur_dt = start_time + timedelta(seconds=step * step_seconds)
                # タイムバイアス補正: 観測時刻 cur_dt に対し、衛星の位置を cur_dt - drift_sec で SGP4 伝搬
                eval_dt = cur_dt - timedelta(seconds=drift_sec)
                jd, fr = self._datetime_to_jd(eval_dt)
                err, r_teme, _ = satrec.sgp4(jd, fr)
                if err != 0:
                    continue

                gmst = self._calc_gmst_rad(jd, fr)
                r_ecef = self._teme_to_ecef(r_teme, gmst)
                az_deg, el_deg, _ = self._ecef_to_az_el_range(r_ecef, self.obs_ecef_km)
                in_win = self.is_in_sky_window(az_deg, el_deg, empirical_mask=empirical_mask)

                if el_deg >= 5.0:
                    if not in_pass:
                        in_pass = True
                        pass_start_dt = cur_dt
                        max_el = el_deg
                        max_el_az = az_deg
                        tca_dt = cur_dt
                        window_duration = step_seconds if in_win else 0
                    else:
                        if el_deg > max_el:
                            max_el = el_deg
                            max_el_az = az_deg
                            tca_dt = cur_dt
                        if in_win:
                            window_duration += step_seconds
                else:
                    if in_pass:
                        # パス終了
                        if max_el >= 10.0 and pass_start_dt and tca_dt:
                            quality = self._evaluate_quality(max_el, max_el_az, window_duration)
                            prob, hist_summary = self._evaluate_success_probability(
                                name, max_el, quality, history_stats
                            )
                            all_passes.append(
                                FuturePass(
                                    satellite=name,
                                    aos_time=pass_start_dt.isoformat(),
                                    tca_time=tca_dt.isoformat(),
                                    los_time=cur_dt.isoformat(),
                                    max_elevation=round(max_el, 1),
                                    max_elevation_azimuth=round(max_el_az, 1),
                                    window_duration_seconds=window_duration,
                                    quality_score=quality,
                                    success_probability=prob,
                                    history_summary=hist_summary,
                                    time_drift_seconds=recorded_drift,
                                )
                            )
                        in_pass = False
                        pass_start_dt = None
                        max_el = -90.0
                        tca_dt = None
                        window_duration = 0

            # 境界でパス中の場合
            if in_pass and max_el >= 10.0 and pass_start_dt and tca_dt:
                cur_dt = start_time + timedelta(seconds=total_steps * step_seconds)
                quality = self._evaluate_quality(max_el, max_el_az, window_duration)
                prob, hist_summary = self._evaluate_success_probability(
                    name, max_el, quality, history_stats
                )
                all_passes.append(
                    FuturePass(
                        satellite=name,
                        aos_time=pass_start_dt.isoformat(),
                        tca_time=tca_dt.isoformat(),
                        los_time=cur_dt.isoformat(),
                        max_elevation=round(max_el, 1),
                        max_elevation_azimuth=round(max_el_az, 1),
                        window_duration_seconds=window_duration,
                        quality_score=quality,
                        success_probability=prob,
                        history_summary=hist_summary,
                        time_drift_seconds=recorded_drift,
                    )
                )

        all_passes.sort(key=lambda p: p.aos_time)
        return all_passes

    @staticmethod
    def _evaluate_quality(max_el: float, max_el_az: float, window_dur: int) -> str:
        if window_dur >= 60 and max_el >= 35.0:
            return "EXCELLENT (スカイウィンドウ直撃)"
        elif window_dur > 0:
            return "GOOD (スカイウィンドウ通過)"
        elif max_el >= 20.0 and (max_el_az >= 250.0 or max_el_az <= 110.0):
            return "FAIR (ベランダ視野内)"
        else:
            return "MARGINAL (建物遮蔽)"

    @staticmethod
    def _evaluate_success_probability(
        satellite: str,
        max_el: float,
        quality_score: str,
        history_stats: Optional[Dict[str, Any]] = None,
    ) -> Tuple[float, str]:
        """
        過去の復調実績統計と最大仰角・品質スコアから、推定成功確率(0.0〜1.0)とサマリー文を算出。
        ラプラス平滑化 (Laplace Smoothing) を適用。
        """
        sat_stats = history_stats.get(satellite) if history_stats else None

        # 仰角帯の分類
        if max_el >= 35.0:
            band = "high"
            band_label = "高仰角(>=35°)"
        elif max_el >= 20.0:
            band = "mid"
            band_label = "中仰角(20°〜35°)"
        else:
            band = "low"
            band_label = "低仰角(<20°)"

        # 過去実績が存在する場合
        if sat_stats and sat_stats.get("total_passes", 0) > 0:
            band_info = sat_stats.get("elevation_bands", {}).get(band, {})
            b_total = band_info.get("total", 0)
            b_success = band_info.get("success", 0)

            if b_total > 0:
                # 当該仰角帯の実績に基づくラプラス平滑化確率: (succ + 1) / (total + 2)
                prob = (b_success + 1.0) / (b_total + 2.0)
                raw_rate = (b_success / b_total) * 100.0
                summary = f"同仰角帯実績: {b_success}/{b_total}回成功 ({raw_rate:.0f}%)"
            else:
                # 当該仰角帯は未観測だが全体実績がある場合
                tot = sat_stats.get("total_passes", 0)
                succ = sat_stats.get("successful_passes", 0)
                prob = (succ + 1.0) / (tot + 2.0)
                if band == "low":
                    prob = max(0.05, prob * 0.6)
                elif band == "high":
                    prob = min(0.95, prob * 1.1)
                raw_rate = (succ / tot) * 100.0
                summary = f"全体実績: {succ}/{tot}回成功 ({band_label}は初観測)"
            return round(prob, 2), summary

        # 過去実績がまだない場合の幾何学的事前確率フォールバック
        if "EXCELLENT" in quality_score:
            prob = 0.75
        elif "GOOD" in quality_score:
            prob = 0.50
        elif "FAIR" in quality_score:
            prob = 0.25
        else:
            prob = 0.05

        return round(prob, 2), "実績データ収集中 (未観測)"


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
