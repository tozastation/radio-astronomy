from datetime import datetime, timezone

from prometheus_client import CollectorRegistry

from seestar_plan.astro import parse_astro
from seestar_plan.metrics import PlanCollector
from seestar_plan.plan import Site

NIGHT = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)


def _payload():
    rows = [
        {"timepoint": hour, "seeing": 3, "transparency": 2 if hour < 18 else -9999}
        for hour in range(3, 73, 3)
    ]
    return {"init": "2026101200", "dataseries": rows}


def test_slots_are_centred_on_timepoints():
    forecast = parse_astro(_payload(), NIGHT)
    slot = forecast.at(NIGHT)
    assert slot.at == datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
    assert slot.seeing_arcsec == 0.875
    assert forecast.at(datetime(2026, 10, 12, 13, 29, tzinfo=timezone.utc)) is slot
    assert forecast.at(datetime(2026, 10, 12, 18, 0, tzinfo=timezone.utc)).transparency_index is None


def test_collector_exposes_seeing_and_transparency():
    forecast = parse_astro(_payload(), NIGHT)
    registry = CollectorRegistry()
    registry.register(PlanCollector(Site, clock=lambda: NIGHT, astro=lambda: (forecast, True)))
    assert registry.get_sample_value("seestar_weather_astro_forecast_up") == 1.0
    assert registry.get_sample_value("seestar_weather_seeing_index") == 3.0
    assert registry.get_sample_value("seestar_weather_transparency_index") == 2.0
    assert registry.get_sample_value("seestar_weather_tonight_seeing_arcseconds", {"slot": "00", "hour": "18時"}) == 0.875
