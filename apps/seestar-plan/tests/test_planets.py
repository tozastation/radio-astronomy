from datetime import datetime, timezone

import pytest

from seestar_plan.planets import planet_radec_deg

# 参照値は PyEphem 4.2 の地心・J2000 の赤経・赤緯（a_ra, a_dec）。
REFERENCE = {
    "venus": (211.47, -20.80),
    "mars": (130.62, 19.47),
    "jupiter": (143.77, 15.03),
    "saturn": (10.53, 1.58),
    "uranus": (62.99, 20.96),
    "neptune": (2.55, -0.44),
}


@pytest.mark.parametrize("body", sorted(REFERENCE))
def test_planet_positions_match_reference(body):
    ra, dec = planet_radec_deg(body, datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc))
    ref_ra, ref_dec = REFERENCE[body]
    assert abs((ra - ref_ra + 180.0) % 360.0 - 180.0) < 0.3
    assert abs(dec - ref_dec) < 0.3
