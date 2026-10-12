from datetime import datetime, timedelta, timezone

from prometheus_client import CollectorRegistry

from seestar_plan.metrics import PlanCollector
from seestar_plan.plan import Site
from seestar_plan.weather import Forecast, ForecastCache, parse_forecast

NIGHT = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
FIRST = datetime(2026, 10, 12, 0, 0, tzinfo=timezone.utc)


def _payload(cloud):
    count = len(cloud)
    stamps = [int((FIRST + timedelta(hours=i)).timestamp()) for i in range(count)]
    return {
        "hourly": {
            "time": stamps,
            "cloud_cover": cloud,
            "cloud_cover_low": cloud,
            "cloud_cover_mid": [0] * count,
            "cloud_cover_high": [0] * count,
            "precipitation_probability": [0] * count,
            "relative_humidity_2m": [80] * count,
            "temperature_2m": [15.0] * count,
            "dew_point_2m": [12.0] * count,
            "wind_speed_10m": [2.0] * count,
            "visibility": [30000.0] * count,
        }
    }


def _forecast(cloud) -> Forecast:
    return parse_forecast(_payload(cloud), FIRST)


def test_clear_seconds_counts_only_clear_hours():
    forecast = _forecast([0] * 12 + [90] + [10] * 35)
    start = datetime(2026, 10, 12, 12, 30, tzinfo=timezone.utc)
    end = datetime(2026, 10, 12, 14, 0, tzinfo=timezone.utc)
    assert forecast.clear_seconds(start, end) == 3600.0
    assert forecast.at(start).cloud_cover == 90


def test_missing_values_become_nan():
    payload = _payload([0, 0])
    payload["hourly"]["visibility"] = [None, 1.0]
    forecast = parse_forecast(payload, FIRST)
    assert forecast.hours[0].visibility != forecast.hours[0].visibility


def test_cache_keeps_last_forecast_after_failure():
    calls = []

    def fetch():
        calls.append(1)
        if len(calls) > 1:
            raise OSError("offline")
        return _forecast([0] * 48)

    cache = ForecastCache(fetch)
    cache.refresh()
    cache.refresh()
    forecast, ok = cache.get()
    assert forecast is not None
    assert ok is False


def test_collector_exposes_tonight_forecast():
    forecast = _forecast([0] * 72)
    registry = CollectorRegistry()
    registry.register(PlanCollector(Site, clock=lambda: NIGHT, forecast=lambda: (forecast, True)))
    assert registry.get_sample_value("seestar_weather_forecast_up") == 1.0
    assert registry.get_sample_value("seestar_weather_cloud_cover_percent", {"layer": "total"}) == 0.0
    night = registry.get_sample_value("seestar_night_end_timestamp_seconds") - registry.get_sample_value(
        "seestar_night_start_timestamp_seconds"
    )
    assert registry.get_sample_value("seestar_weather_tonight_clear_seconds") == night
    polaris = {"target": "polaris", "name": "北極星", "window": "north", "window_name": "北の長辺"}
    end = registry.get_sample_value("seestar_target_window_end_timestamp_seconds", polaris)
    assert registry.get_sample_value("seestar_target_window_clear_seconds", polaris) == end - NIGHT.timestamp()
    assert registry.get_sample_value("seestar_weather_tonight_cloud_cover_percent", {"slot": "00", "hour": "18時"}) == 0.0
