import logging
from typing import Dict, Any
import requests

logger = logging.getLogger(__name__)

CELESTRAK_AMATEUR_TLE_URL = "https://celestrak.org/NORAD/elements/gp.php?GROUP=amateur&FORMAT=tle"


def fetch_satellite_tles(
    default_satellites: Dict[str, Dict[str, Any]],
    url: str = CELESTRAK_AMATEUR_TLE_URL,
    timeout_sec: float = 10.0,
) -> Dict[str, Dict[str, Any]]:
    """Celestrak から最新の衛星 TLE データを取得し、辞書を更新する。
    通信失敗時は安全にデフォルトの TLE データにフォールバックする。
    """
    updated_satellites = {k: v.copy() for k, v in default_satellites.items()}

    try:
        logger.info(f"Fetching live TLEs from Celestrak ({url})...")
        resp = requests.get(url, timeout=timeout_sec)
        resp.raise_for_status()
        raw_text = resp.text

        # 3行フォーマット (Name, Line 1, Line 2) のパース
        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
        tle_map: Dict[str, tuple] = {}

        i = 0
        while i < len(lines) - 2:
            name_line = lines[i]
            l1 = lines[i + 1]
            l2 = lines[i + 2]

            if l1.startswith("1 ") and l2.startswith("2 "):
                tle_map[name_line.upper()] = (l1, l2)
                i += 3
            else:
                i += 1

        # ターゲット衛星のTLEを更新
        updated_count = 0
        for sat_name, sat_data in updated_satellites.items():
            sat_upper = sat_name.upper()
            # 完全一致または前方一致で探索 (例: "ISS" -> "ISS (ZARYA)")
            matched_tle = None
            for tle_name, (l1, l2) in tle_map.items():
                if sat_upper == tle_name or sat_upper in tle_name:
                    matched_tle = (l1, l2)
                    break

            if matched_tle:
                sat_data["line1"] = matched_tle[0]
                sat_data["line2"] = matched_tle[1]
                updated_count += 1
                logger.info(f"Updated live TLE for {sat_name}")

        logger.info(f"Successfully updated {updated_count}/{len(default_satellites)} satellites with live TLEs.")
        return updated_satellites

    except requests.RequestException as e:
        logger.warning(
            f"Failed to fetch live TLEs from Celestrak ({e}). Falling back to static cached TLEs."
        )
        return default_satellites
    except Exception as e:
        logger.error(f"Unexpected error parsing Celestrak TLEs ({e}). Using cached TLEs.")
        return default_satellites
