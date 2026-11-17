import os
from unittest.mock import MagicMock
from tle_resolver import (
    DEFAULT_FUNCUBE_TLE,
    DEFAULT_ISS_TLE,
    DEFAULT_METEOR_M2_3_TLE,
    DEFAULT_METEOR_M2_4_TLE,
    parse_tle_epoch,
    resolve_fallback_tle,
    resolve_latest_tle,
)


def test_parse_tle_epoch_ordering():
    ep_old = parse_tle_epoch("1 25544U 98067A   26294.04791053  .00009575  00000+0  18188-3 0  9996")
    ep_new = parse_tle_epoch(DEFAULT_ISS_TLE[0])
    assert ep_new > ep_old
    assert ep_new >= 2026319.0


def test_resolve_fallback_tle_all_targets():
    assert resolve_fallback_tle("ISS (ZARYA)") == DEFAULT_ISS_TLE
    assert resolve_fallback_tle("FUNCUBE-1 (AO-73)") == DEFAULT_FUNCUBE_TLE
    assert resolve_fallback_tle("METEOR-M2 3") == DEFAULT_METEOR_M2_3_TLE
    assert resolve_fallback_tle("METEOR-M2 4") == DEFAULT_METEOR_M2_4_TLE
    assert resolve_fallback_tle("UNKNOWN-SAT") is None


def test_resolve_latest_tle_ignores_older_file_and_picks_newer_file(tmp_path):
    old_file = tmp_path / "old_tles.txt"
    old_file.write_text(
        "ISS (ZARYA)\n"
        "1 25544U 98067A   26200.00000000  .00009575  00000+0  18188-3 0  9994\n"
        "2 25544  51.6327  37.2623 0006759 289.5321  70.4938 15.48967427591263\n",
        encoding="utf-8",
    )
    tle = resolve_latest_tle("ISS (ZARYA)", tle_path=str(old_file), allow_live_fetch=False)
    assert tle is not None
    assert parse_tle_epoch(tle[0]) >= parse_tle_epoch(DEFAULT_ISS_TLE[0])

    new_file = tmp_path / "new_tles.txt"
    new_file.write_text(
        "AO-73\n"
        "1 39444U 13066AE  26350.50000000  .00008247  00000+0  48531-3 0  9997\n"
        "2 39444  97.8476 294.8869 0036272  48.8187 311.6161 15.11350389702564\n",
        encoding="utf-8",
    )
    # 衛星名が "AO-73" でも NORAD ID (39444) で一致し、より新しい Epoch 26350.5 が選ばれること
    tle_fc = resolve_latest_tle("FUNCUBE-1 (AO-73)", tle_path=str(new_file), allow_live_fetch=False)
    assert tle_fc is not None
    assert "26350.50000000" in tle_fc[0]


def test_resolve_latest_tle_fetches_from_s3_cache(tmp_path, monkeypatch):
    cache_path = tmp_path / "analyzer_tles.txt"
    monkeypatch.setattr("tle_resolver.LIVE_TLE_CACHE_PATH", cache_path)

    s3_body = (
        "ISS (ZARYA)\n"
        "1 25544U 98067A   26351.25000000  .00009214  00000+0  17269-3 0  9998\n"
        "2 25544  51.6321 269.2481 0007362  30.2971 329.8442 15.49402183595279\n"
    ).encode("utf-8")
    mock_body = MagicMock()
    mock_body.read.return_value = s3_body
    mock_s3 = MagicMock()
    mock_s3.get_object.return_value = {"Body": mock_body}

    tle = resolve_latest_tle(
        "ISS (ZARYA)",
        s3_client=mock_s3,
        bucket="satellite-recordings",
        allow_live_fetch=False,
    )
    assert tle is not None
    assert "26351.25000000" in tle[0]
    assert cache_path.exists()
