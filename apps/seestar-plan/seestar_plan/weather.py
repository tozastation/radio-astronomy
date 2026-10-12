"""Open-Meteo の 1 時間ごとの予報。https://open-meteo.com/en/docs"""

from __future__ import annotations

import json
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

API_URL = "https://api.open-meteo.com/v1/forecast"
HOURLY_FIELDS = (
    "cloud_cover",
    "cloud_cover_low",
    "cloud_cover_mid",
    "cloud_cover_high",
    "precipitation_probability",
    "relative_humidity_2m",
    "temperature_2m",
    "dew_point_2m",
    "wind_speed_10m",
    "visibility",
)
SLOT = timedelta(hours=1)
CLEAR_CLOUD_PERCENT = 30.0
CLEAR_PRECIPITATION_PERCENT = 30.0


@dataclass(frozen=True)
class HourlyWeather:
    start: datetime
    cloud_cover: float
    cloud_cover_low: float
    cloud_cover_mid: float
    cloud_cover_high: float
    precipitation_probability: float
    relative_humidity: float
    temperature: float
    dew_point: float
    wind_speed: float
    visibility: float

    @property
    def end(self) -> datetime:
        return self.start + SLOT

    @property
    def clear(self) -> bool:
        """雲量 30% 未満かつ降水確率 30% 未満を晴れとみなす。"""
        return (
            self.cloud_cover < CLEAR_CLOUD_PERCENT
            and self.precipitation_probability < CLEAR_PRECIPITATION_PERCENT
        )


@dataclass(frozen=True)
class Forecast:
    fetched_at: datetime
    hours: tuple[HourlyWeather, ...]

    def at(self, moment: datetime) -> HourlyWeather | None:
        for hour in self.hours:
            if hour.start <= moment < hour.end:
                return hour
        return None

    def overlapping(self, start: datetime, end: datetime) -> tuple[HourlyWeather, ...]:
        return tuple(hour for hour in self.hours if hour.start < end and hour.end > start)

    def clear_seconds(self, start: datetime, end: datetime) -> float:
        total = 0.0
        for hour in self.overlapping(start, end):
            if hour.clear:
                total += (min(end, hour.end) - max(start, hour.start)).total_seconds()
        return total


def parse_forecast(payload: dict, fetched_at: datetime) -> Forecast:
    hourly = payload["hourly"]

    def value(field: str, index: int) -> float:
        raw = hourly[field][index]
        return float("nan") if raw is None else float(raw)

    hours = tuple(
        HourlyWeather(
            start=datetime.fromtimestamp(stamp, timezone.utc),
            cloud_cover=value("cloud_cover", index),
            cloud_cover_low=value("cloud_cover_low", index),
            cloud_cover_mid=value("cloud_cover_mid", index),
            cloud_cover_high=value("cloud_cover_high", index),
            precipitation_probability=value("precipitation_probability", index),
            relative_humidity=value("relative_humidity_2m", index),
            temperature=value("temperature_2m", index),
            dew_point=value("dew_point_2m", index),
            wind_speed=value("wind_speed_10m", index),
            visibility=value("visibility", index),
        )
        for index, stamp in enumerate(hourly["time"])
    )
    return Forecast(fetched_at, hours)


def fetch_forecast(lat_deg: float, lon_deg: float, timeout: float = 15.0) -> Forecast:
    query = urllib.parse.urlencode(
        {
            "latitude": f"{lat_deg:.4f}",
            "longitude": f"{lon_deg:.4f}",
            "hourly": ",".join(HOURLY_FIELDS),
            "timezone": "UTC",
            "timeformat": "unixtime",
            "wind_speed_unit": "ms",
            "forecast_days": 3,
        }
    )
    with urllib.request.urlopen(f"{API_URL}?{query}", timeout=timeout) as response:
        payload = json.load(response)
    return parse_forecast(payload, datetime.now(timezone.utc))


class ForecastCache:
    """裏のスレッドで予報を取り直し、最後に取れた予報を返す。"""

    def __init__(self, fetch: Callable[[], Forecast], interval_seconds: float = 1800.0) -> None:
        self._fetch = fetch
        self._interval = interval_seconds
        self._lock = threading.Lock()
        self._forecast: Forecast | None = None
        self._last_ok = False

    def refresh(self) -> None:
        try:
            forecast = self._fetch()
        except Exception as error:  # noqa: BLE001
            print(f"[seestar-plan] 予報の取得に失敗: {error}")
            with self._lock:
                self._last_ok = False
            return
        with self._lock:
            self._forecast = forecast
            self._last_ok = True

    def start(self) -> None:
        def loop() -> None:
            while True:
                self.refresh()
                time.sleep(self._interval if self._last_ok else 300.0)

        threading.Thread(target=loop, name="forecast", daemon=True).start()

    def get(self) -> tuple[Forecast | None, bool]:
        with self._lock:
            return self._forecast, self._last_ok
