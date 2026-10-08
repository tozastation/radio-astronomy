import os
import sys
import pytest
from unittest.mock import patch
import requests

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))
from tle_fetcher import fetch_satellite_tles

SAMPLE_CELESTRAK_TLE_TEXT = """ISS (ZARYA)
1 25544U 98067A   24002.50000000  .00017000  00000+0  10280-3 0  9002
2 25544  51.6400  50.1000 0005000  40.1000 320.1000 15.50000000400002
CAS-4A
1 42761U 17034B   24002.50000000  .00001600  00000+0  10100-3 0  9002
2 42761  43.0000  60.1000 0012000  50.1000 310.1000 15.10000000300002
"""

DEFAULT_SATS = {
    "ISS": {
        "line1": "1 25544U 98067A   24001.50000000  .00016717  00000+0  10270-3 0  9001",
        "line2": "2 25544  51.6400  50.0000 0005000  40.0000 320.0000 15.50000000400001",
        "freq_hz": 437550000.0,
    },
    "CAS-4A": {
        "line1": "1 42761U 17034B   24001.50000000  .00001500  00000+0  10000-3 0  9001",
        "line2": "2 42761  43.0000  60.0000 0012000  50.0000 310.0000 15.10000000300001",
        "freq_hz": 435220000.0,
    },
}


def test_fetch_satellite_tles_online_success():
    with patch("requests.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.text = SAMPLE_CELESTRAK_TLE_TEXT

        result = fetch_satellite_tles(DEFAULT_SATS)

        assert "ISS" in result
        assert "CAS-4A" in result
        # 最新のTLEデータに更新されていること
        assert "24002.50000000" in result["ISS"]["line1"]
        assert "24002.50000000" in result["CAS-4A"]["line1"]
        assert result["ISS"]["freq_hz"] == 437550000.0


def test_fetch_satellite_tles_offline_fallback():
    with patch("requests.get", side_effect=requests.RequestException("Network unreachable")):
        # ネットワーク障害時はデフォルトのTLEに安全にフォールバックすること
        result = fetch_satellite_tles(DEFAULT_SATS)

        assert result == DEFAULT_SATS
        assert "24001.50000000" in result["ISS"]["line1"]
