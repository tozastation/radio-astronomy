from datetime import datetime, timezone

from prometheus_client import CollectorRegistry, generate_latest

from seestar_plan.metrics import PlanCollector
from seestar_plan.plan import Site

NIGHT = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)


def _registry() -> CollectorRegistry:
    registry = CollectorRegistry()
    registry.register(PlanCollector(Site, clock=lambda: NIGHT))
    return registry


def test_exposition_has_window_and_moon_series():
    text = generate_latest(_registry()).decode()
    assert 'seestar_target_altitude_degrees{kind="deep_sky",name="北極星",target="polaris"}' in text
    assert 'seestar_window_floor_degrees{window="east",window_name="東の短辺"} 20.0' in text
    assert "seestar_moon_illumination_ratio " in text
    assert "seestar_moon_next_full_timestamp_seconds " in text


def test_values_are_consistent():
    registry = _registry()

    def value(metric, **labels):
        return registry.get_sample_value(metric, labels)

    assert value("seestar_sky_dark") == 1.0
    polaris = {"target": "polaris", "name": "北極星", "kind": "deep_sky", "window": "north", "window_name": "北の長辺"}
    assert value("seestar_target_in_window", **polaris) == 1.0
    start = value("seestar_target_window_start_timestamp_seconds", **polaris)
    end = value("seestar_target_window_end_timestamp_seconds", **polaris)
    assert start <= NIGHT.timestamp() < end
    assert value("seestar_target_window_duration_seconds", **polaris) == end - start
    assert 0.0 <= value("seestar_moon_illumination_ratio") <= 1.0
