from datetime import datetime, timezone

from seestar_plan.plan import SENTENCE_NONE, SENTENCE_STAY, Site, build_plan, plan_as_dict
from seestar_plan.plot import render_png


def test_sample_night_has_a_long_northern_target():
    plan = build_plan(datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc), Site())
    assert plan.sentence == SENTENCE_STAY
    by_name = {item.target.name: item for item in plan.targets}
    assert by_name["北極星"].longest_minutes >= 30
    assert by_name["M52"].longest_minutes >= 30
    payload = plan_as_dict(plan)
    assert payload["sentence"] == SENTENCE_STAY
    assert payload["targets"]


def test_high_floor_hides_everything():
    site = Site(alt_ridge_deg=89, alt_max_deg=89)
    plan = build_plan(datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc), site)
    assert plan.sentence == SENTENCE_NONE
    assert all(item.longest_minutes < 30 for item in plan.targets)


def test_png_is_a_png():
    plan = build_plan(datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc), Site())
    image = render_png(plan)
    assert image.startswith(b"\x89PNG")
    assert len(image) > 1000
