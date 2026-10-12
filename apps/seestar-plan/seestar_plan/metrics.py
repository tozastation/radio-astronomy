"""Prometheus が取りに来た時刻で、窓・星・月の値を計算して返す。"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Callable, Iterator
from zoneinfo import ZoneInfo

from prometheus_client.core import GaugeMetricFamily, Metric
from prometheus_client.registry import Collector

from seestar_plan.catalog import CATALOG, Target
from seestar_plan.plan import Site, build_plan
from seestar_plan.sky import (
    SYNODIC_MONTH_DAYS,
    angular_separation_deg,
    horizontal,
    moon_horizontal,
    moon_illumination_ratio,
    moon_phase_ratio,
    moon_radec_deg,
    next_altitude_crossing,
    next_phase,
    sun_horizontal,
)
from seestar_plan.astro import AstroForecast
from seestar_plan.weather import Forecast

JST = ZoneInfo("Asia/Tokyo")
DARK_SUN_ALT_DEG = -18.0
TARGET_LABELS = ["target", "name", "kind"]
WINDOW_LABELS = ["window", "window_name"]


def _gauge(name: str, documentation: str, labels: list[str] | None = None) -> GaugeMetricFamily:
    return GaugeMetricFamily(name, documentation, labels=labels or [])


class PlanCollector(Collector):
    def __init__(
        self,
        site_loader: Callable[[], Site],
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        targets: tuple[Target, ...] = CATALOG,
        forecast: Callable[[], tuple[Forecast | None, bool]] | None = None,
        astro: Callable[[], tuple[AstroForecast | None, bool]] | None = None,
    ) -> None:
        self._site_loader = site_loader
        self._clock = clock
        self._targets = targets
        self._forecast = forecast
        self._astro = astro

    def describe(self) -> list[Metric]:
        return []

    def collect(self) -> Iterator[Metric]:
        began = time.perf_counter()
        now = self._clock()
        site = self._site_loader()
        lat, lon = site.latitude_deg, site.longitude_deg

        sun = sun_horizontal(now, lat, lon)
        sun_alt = _gauge("seestar_sun_altitude_degrees", "太陽の高度")
        sun_alt.add_metric([], sun.alt_deg)
        dark = _gauge("seestar_sky_dark", "天文薄明が終わっていれば 1（太陽高度が -18 度より下）")
        dark.add_metric([], 1.0 if sun.alt_deg < DARK_SUN_ALT_DEG else 0.0)

        plan = build_plan(now, site, self._targets)
        night_start = _gauge("seestar_night_start_timestamp_seconds", "今夜の暗い時間の始まり（Unix 時刻）")
        night_start.add_metric([], plan.night_start.timestamp())
        night_end = _gauge("seestar_night_end_timestamp_seconds", "今夜の暗い時間の終わり（Unix 時刻）")
        night_end.add_metric([], plan.night_end.timestamp())

        floor = _gauge("seestar_window_floor_degrees", "窓の下限仰角", WINDOW_LABELS)
        ceiling = _gauge("seestar_window_ceiling_degrees", "窓の上限仰角", WINDOW_LABELS)
        az_from = _gauge("seestar_window_azimuth_from_degrees", "窓の方位の始まり（時計回り）", WINDOW_LABELS)
        az_to = _gauge("seestar_window_azimuth_to_degrees", "窓の方位の終わり（時計回り）", WINDOW_LABELS)
        for window in site.windows:
            labels = [window.key, window.name]
            floor.add_metric(labels, window.floor_deg)
            ceiling.add_metric(labels, window.ceiling_deg)
            az_from.add_metric(labels, window.az_from_deg)
            az_to.add_metric(labels, window.az_to_deg)

        moon_ra, moon_dec = moon_radec_deg(now)
        alt = _gauge("seestar_target_altitude_degrees", "星の高度", TARGET_LABELS)
        az = _gauge("seestar_target_azimuth_degrees", "星の方位（北 0 度・東 90 度）", TARGET_LABELS)
        separation = _gauge("seestar_target_moon_separation_degrees", "星と月の離角", TARGET_LABELS)
        positions = {}
        for target in self._targets:
            labels = [target.key, target.name, target.kind]
            ra, dec = target.radec_deg(now)
            position = horizontal(now, lat, lon, ra, dec)
            positions[target.key] = position
            alt.add_metric(labels, position.alt_deg)
            az.add_metric(labels, position.az_deg)
            separation.add_metric(labels, angular_separation_deg(ra, dec, moon_ra, moon_dec))

        both = TARGET_LABELS + WINDOW_LABELS
        in_window = _gauge("seestar_target_in_window", "いま窓の中にいれば 1（明るさは問わない）", both)
        start = _gauge(
            "seestar_target_window_start_timestamp_seconds",
            "今夜の暗い時間で、いま入っている窓か次の窓の始まり（Unix 時刻）",
            both,
        )
        end = _gauge("seestar_target_window_end_timestamp_seconds", "その窓の終わり（Unix 時刻）", both)
        duration = _gauge("seestar_target_window_duration_seconds", "その窓の長さ", both)
        for item in plan.targets:
            labels = [item.target.key, item.target.name, item.target.kind, item.window.key, item.window.name]
            inside = item.window.contains(positions[item.target.key])
            in_window.add_metric(labels, 1.0 if inside else 0.0)
            interval = item.upcoming(now)
            if interval is None:
                continue
            start.add_metric(labels, interval.start.timestamp())
            end.add_metric(labels, interval.end.timestamp())
            duration.add_metric(labels, (interval.end - interval.start).total_seconds())

        moon = moon_horizontal(now, lat, lon)
        phase = moon_phase_ratio(now)
        moon_alt = _gauge("seestar_moon_altitude_degrees", "月の高度（地表から見た値）")
        moon_alt.add_metric([], moon.alt_deg)
        moon_az = _gauge("seestar_moon_azimuth_degrees", "月の方位")
        moon_az.add_metric([], moon.az_deg)
        illumination = _gauge("seestar_moon_illumination_ratio", "月の輝面比（0 から 1）")
        illumination.add_metric([], moon_illumination_ratio(now))
        phase_metric = _gauge("seestar_moon_phase_ratio", "月相。0 が新月、0.25 が上弦、0.5 が満月、0.75 が下弦")
        phase_metric.add_metric([], phase)
        age = _gauge("seestar_moon_age_days", "新月からの日数（黄経差から換算）")
        age.add_metric([], phase * SYNODIC_MONTH_DAYS)
        moon_in_window = _gauge("seestar_moon_in_window", "月が窓の中にいれば 1", WINDOW_LABELS)
        for window in site.windows:
            moon_in_window.add_metric([window.key, window.name], 1.0 if window.contains(moon) else 0.0)

        next_new = _gauge("seestar_moon_next_new_timestamp_seconds", "次の新月（Unix 時刻）")
        next_new.add_metric([], next_phase(now, 0.0).timestamp())
        next_full = _gauge("seestar_moon_next_full_timestamp_seconds", "次の満月（Unix 時刻）")
        next_full.add_metric([], next_phase(now, 0.5).timestamp())
        moonrise = _gauge("seestar_moon_next_rise_timestamp_seconds", "次の月の出（Unix 時刻）")
        rise_at = next_altitude_crossing(now, lat, lon, rising=True)
        if rise_at is not None:
            moonrise.add_metric([], rise_at.timestamp())
        moonset = _gauge("seestar_moon_next_set_timestamp_seconds", "次の月の入り（Unix 時刻）")
        set_at = next_altitude_crossing(now, lat, lon, rising=False)
        if set_at is not None:
            moonset.add_metric([], set_at.timestamp())

        yield from self._weather(now, plan)
        yield from self._astro_weather(now, plan)

        compute = _gauge("seestar_plan_compute_seconds", "この応答の計算にかかった時間")
        compute.add_metric([], time.perf_counter() - began)

        yield from (
            sun_alt, dark, night_start, night_end,
            floor, ceiling, az_from, az_to,
            alt, az, separation, in_window, start, end, duration,
            moon_alt, moon_az, illumination, phase_metric, age, moon_in_window,
            next_new, next_full, moonrise, moonset,
            compute,
        )

    def _weather(self, now: datetime, plan) -> Iterator[Metric]:
        if self._forecast is None:
            return
        forecast, last_ok = self._forecast()
        up = _gauge("seestar_weather_forecast_up", "直近の予報の取得に成功していれば 1")
        up.add_metric([], 1.0 if last_ok else 0.0)
        yield up
        if forecast is None:
            return
        fetched = _gauge("seestar_weather_forecast_fetch_timestamp_seconds", "手元の予報を取った時刻（Unix 時刻）")
        fetched.add_metric([], forecast.fetched_at.timestamp())
        yield fetched

        current = forecast.at(now)
        if current is not None:
            cloud = _gauge("seestar_weather_cloud_cover_percent", "いまの時間帯の雲量", ["layer"])
            for layer, value in (
                ("total", current.cloud_cover),
                ("low", current.cloud_cover_low),
                ("mid", current.cloud_cover_mid),
                ("high", current.cloud_cover_high),
            ):
                cloud.add_metric([layer], value)
            yield cloud
            for name, documentation, value in (
                ("seestar_weather_precipitation_probability_percent", "いまの時間帯の降水確率", current.precipitation_probability),
                ("seestar_weather_relative_humidity_percent", "いまの時間帯の相対湿度（地上 2 m）", current.relative_humidity),
                ("seestar_weather_temperature_celsius", "いまの時間帯の気温（地上 2 m）", current.temperature),
                ("seestar_weather_dew_point_celsius", "いまの時間帯の露点（地上 2 m）", current.dew_point),
                ("seestar_weather_wind_speed_meters_per_second", "いまの時間帯の風速（地上 10 m）", current.wind_speed),
                ("seestar_weather_visibility_meters", "いまの時間帯の視程", current.visibility),
            ):
                gauge = _gauge(name, documentation)
                gauge.add_metric([], value)
                yield gauge

        slot_labels = ["slot", "hour"]
        tonight_cloud = _gauge(
            "seestar_weather_tonight_cloud_cover_percent", "今夜の暗い時間にかかる 1 時間ごとの雲量", slot_labels
        )
        tonight_rain = _gauge(
            "seestar_weather_tonight_precipitation_probability_percent",
            "今夜の暗い時間にかかる 1 時間ごとの降水確率",
            slot_labels,
        )
        for index, hour in enumerate(forecast.overlapping(plan.night_start, plan.night_end)):
            labels = [f"{index:02d}", f"{hour.start.astimezone(JST):%H}時"]
            tonight_cloud.add_metric(labels, hour.cloud_cover)
            tonight_rain.add_metric(labels, hour.precipitation_probability)
        clear = _gauge("seestar_weather_tonight_clear_seconds", "今夜の暗い時間のうち、晴れの予報の長さ")
        clear.add_metric([], forecast.clear_seconds(plan.night_start, plan.night_end))
        yield from (tonight_cloud, tonight_rain, clear)

        target_clear = _gauge(
            "seestar_target_window_clear_seconds",
            "いま入っている窓か次の窓の、これから先のうち晴れの予報の長さ",
            TARGET_LABELS + WINDOW_LABELS,
        )
        for item in plan.targets:
            interval = item.upcoming(now)
            if interval is None:
                continue
            target_clear.add_metric(
                [item.target.key, item.target.name, item.target.kind, item.window.key, item.window.name],
                forecast.clear_seconds(max(interval.start, now), interval.end),
            )
        yield target_clear

    def _astro_weather(self, now: datetime, plan) -> Iterator[Metric]:
        if self._astro is None:
            return
        forecast, last_ok = self._astro()
        up = _gauge("seestar_weather_astro_forecast_up", "直近の 7Timer! 予報の取得に成功していれば 1")
        up.add_metric([], 1.0 if last_ok else 0.0)
        yield up
        if forecast is None:
            return
        fetched = _gauge(
            "seestar_weather_astro_forecast_fetch_timestamp_seconds", "手元の 7Timer! 予報を取った時刻（Unix 時刻）"
        )
        fetched.add_metric([], forecast.fetched_at.timestamp())
        yield fetched

        current = forecast.at(now)
        if current is not None:
            for name, documentation, value in (
                ("seestar_weather_seeing_index", "いまの時間帯のシーイングの段階（1 が最良、8 が最悪）", current.seeing_index),
                ("seestar_weather_seeing_arcseconds", "いまの時間帯のシーイング（段階の代表値）", current.seeing_arcsec),
                ("seestar_weather_transparency_index", "いまの時間帯の透明度の段階（1 が最良、8 が最悪）", current.transparency_index),
                (
                    "seestar_weather_transparency_magnitudes_per_airmass",
                    "いまの時間帯の 1 エアマスあたりの減光（段階の代表値）",
                    current.transparency_mag,
                ),
            ):
                if value is None:
                    continue
                gauge = _gauge(name, documentation)
                gauge.add_metric([], float(value))
                yield gauge

        slot_labels = ["slot", "hour"]
        seeing = _gauge(
            "seestar_weather_tonight_seeing_arcseconds", "今夜の暗い時間にかかる 3 時間ごとのシーイング", slot_labels
        )
        transparency = _gauge(
            "seestar_weather_tonight_transparency_index", "今夜の暗い時間にかかる 3 時間ごとの透明度の段階", slot_labels
        )
        for index, slot in enumerate(forecast.overlapping(plan.night_start, plan.night_end)):
            labels = [f"{index:02d}", f"{slot.at.astimezone(JST):%H}時"]
            if slot.seeing_arcsec is not None:
                seeing.add_metric(labels, slot.seeing_arcsec)
            if slot.transparency_index is not None:
                transparency.add_metric(labels, float(slot.transparency_index))
        yield from (seeing, transparency)
