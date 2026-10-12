import json
from datetime import datetime, timezone

from seestar_plan.plan import Site, build_plan
from seestar_plan.sky import Window

NIGHT = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)


def test_sample_night_has_long_northern_targets():
    plan = build_plan(NIGHT, Site())
    north = {item.target.key: item for item in plan.targets if item.window.key == "north"}
    for key in ("polaris", "m52"):
        interval = north[key].upcoming(NIGHT)
        assert interval is not None
        assert interval.minutes >= 30


def test_each_window_gets_every_target():
    plan = build_plan(NIGHT, Site())
    keys = {(item.window.key, item.target.key) for item in plan.targets}
    assert len(keys) == 12


def test_high_floor_hides_everything():
    site = Site(windows=(Window("north", "北", 270, 90, 89, 89),))
    plan = build_plan(NIGHT, site)
    assert all(item.upcoming(NIGHT) is None for item in plan.targets)


def test_site_file_replaces_windows(tmp_path):
    path = tmp_path / "site.json"
    path.write_text(
        json.dumps(
            {
                "latitude_deg": 35.0,
                "windows": [
                    {"key": "east", "name": "東", "az_from_deg": 0, "az_to_deg": 180, "floor_deg": 32, "ceiling_deg": 80}
                ],
            }
        ),
        encoding="utf-8",
    )
    site = Site.from_file(str(path))
    assert site.latitude_deg == 35.0
    assert [window.key for window in site.windows] == ["east"]
    assert site.windows[0].floor_deg == 32
