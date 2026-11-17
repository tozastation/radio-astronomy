import logging
import os
import time
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from sgp4.api import Satrec

logger = logging.getLogger(__name__)

DEFAULT_ISS_TLE: Tuple[str, str] = (
    "1 25544U 98067A   26319.91560506  .00009214  00000+0  17269-3 0  9996",
    "2 25544  51.6321 269.2481 0007362  30.2971 329.8442 15.49402183595279",
)

DEFAULT_FUNCUBE_TLE: Tuple[str, str] = (
    "1 39444U 13066AE  26319.89930512  .00008247  00000+0  48531-3 0  9992",
    "2 39444  97.8476 294.8869 0036272  48.8187 311.6161 15.11350389702564",
)

DEFAULT_METEOR_M2_3_TLE: Tuple[str, str] = (
    "1 57166U 23091A   26320.21578751 -.00000010  00000+0  14340-4 0  9996",
    "2 57166  98.5893  11.9287 0005071  89.8347 270.3413 14.24056598176153",
)

DEFAULT_METEOR_M2_4_TLE: Tuple[str, str] = (
    "1 59051U 24039A   26320.19173317  .00000028  00000+0  32366-4 0  9996",
    "2 59051  98.7248 277.7315 0008145  89.7937 270.4174 14.22443462140870",
)

DEFAULT_TLES: Dict[str, Tuple[str, str]] = {
    "ISS (ZARYA)": DEFAULT_ISS_TLE,
    "FUNCUBE-1 (AO-73)": DEFAULT_FUNCUBE_TLE,
    "METEOR-M2 3": DEFAULT_METEOR_M2_3_TLE,
    "METEOR-M2 4": DEFAULT_METEOR_M2_4_TLE,
}

SATELLITE_NORAD_IDS: Dict[str, int] = {
    "ISS": 25544,
    "FUNCUBE": 39444,
    "AO-73": 39444,
    "METEOR-M2 3": 57166,
    "METEOR M2-3": 57166,
    "METEOR-M2 4": 59051,
    "METEOR M2-4": 59051,
}

LIVE_TLE_CACHE_PATH = Path("/tmp/satellite-analyzer-tles.txt")
S3_TLE_KEY = "tle/tles.txt"
CELESTRAK_URLS = (
    "https://celestrak.org/NORAD/elements/gp.php?GROUP=amateur&FORMAT=tle",
    "https://celestrak.org/NORAD/elements/gp.php?GROUP=weather&FORMAT=tle",
)


def parse_tle_epoch(line1: str) -> float:
    """TLE Line 1 から Epoch (YYDDD.DDDDDDDD) をパースし比較可能な float 値 (year * 1000 + day) を返す"""
    try:
        parts = line1.strip().split()
        if len(parts) >= 4 and parts[0] == "1":
            epoch_str = parts[3]
            yy = int(epoch_str[:2])
            full_year = 2000 + yy if yy < 57 else 1900 + yy
            day_fraction = float(epoch_str[2:])
            return full_year * 1000.0 + day_fraction
    except Exception:
        pass
    return 0.0


def resolve_norad_id(satellite: str) -> Optional[int]:
    upper = (satellite or "").upper()
    for key, norad_id in SATELLITE_NORAD_IDS.items():
        if key in upper:
            return norad_id
    return None


def resolve_fallback_tle(satellite: str) -> Optional[Tuple[str, str]]:
    """衛星名に応じたフォールバック TLE タプルを返す"""
    upper = (satellite or "").upper()
    if "ISS" in upper:
        return DEFAULT_ISS_TLE
    if "FUNCUBE" in upper or "AO-73" in upper:
        return DEFAULT_FUNCUBE_TLE
    if "METEOR-M2 3" in upper or "METEOR M2-3" in upper:
        return DEFAULT_METEOR_M2_3_TLE
    if "METEOR-M2 4" in upper or "METEOR M2-4" in upper:
        return DEFAULT_METEOR_M2_4_TLE
    return None


def _is_valid_tle(line1: str, line2: str) -> bool:
    try:
        Satrec.twoline2rv(line1.strip(), line2.strip())
        return True
    except Exception:
        return False


def _extract_matching_tles(
    text: str,
    satellite: str,
    norad_id: Optional[int],
) -> List[Tuple[float, Tuple[str, str]]]:
    matches: List[Tuple[float, Tuple[str, str]]] = []
    norm_target = (satellite or "").strip().upper()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    idx = 0
    while idx + 2 < len(lines):
        name_line = lines[idx].upper()
        l1 = lines[idx + 1]
        l2 = lines[idx + 2]
        if l1.startswith("1 ") and l2.startswith("2 "):
            matched = False
            if norad_id is not None and len(l2) >= 7:
                try:
                    if int(l2[2:7].strip()) == norad_id:
                        matched = True
                except ValueError:
                    pass
            if not matched and norm_target and (
                name_line == norm_target
                or norm_target in name_line
                or name_line in norm_target
            ):
                matched = True
            if matched and _is_valid_tle(l1, l2):
                matches.append((parse_tle_epoch(l1), (l1, l2)))
            idx += 3
        else:
            idx += 1
    return matches


def _refresh_cached_tle_text(
    cache_path: Path,
    s3_client=None,
    bucket: Optional[str] = None,
    ttl_sec: int = 21600,
    allow_live_fetch: bool = True,
) -> None:
    if cache_path.is_file():
        try:
            age = time.time() - cache_path.stat().st_mtime
            if age < max(ttl_sec, 7200):
                return
        except OSError:
            pass

    chunks: List[str] = []

    # 1. エッジ (satellite-tracker-rs) がアップロードした最新 S3 TLE キャッシュを優先取得
    if s3_client is not None and bucket:
        try:
            obj = s3_client.get_object(Bucket=bucket, Key=S3_TLE_KEY)
            body = obj["Body"].read().decode("utf-8", errors="replace")
            if body.strip():
                chunks.append(body)
        except Exception:
            pass

    # 2. 必要に応じて CelesTrak から直接取得 (タイムアウト 3秒)
    if allow_live_fetch and not chunks:
        for url in CELESTRAK_URLS:
            try:
                req = urllib.request.Request(
                    url,
                    headers={"User-Agent": "radio-astronomy-satellite-analyzer/1.7"},
                )
                with urllib.request.urlopen(req, timeout=3.0) as resp:
                    text = resp.read().decode("utf-8", errors="replace")
                    if text.strip():
                        chunks.append(text)
            except Exception as exc:
                logger.debug(f"Live TLE fetch skipped/failed for {url}: {exc}")

    if chunks:
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text("\n".join(chunks), encoding="utf-8")
        except OSError as exc:
            logger.debug(f"Could not write TLE cache {cache_path}: {exc}")


def resolve_latest_tle(
    satellite: str,
    explicit_tle: Optional[Tuple[str, str]] = None,
    tle_path: Optional[str] = None,
    s3_client=None,
    bucket: Optional[str] = None,
    allow_live_fetch: Optional[bool] = None,
) -> Optional[Tuple[str, str]]:
    """
    明示指定・ローカルキャッシュ・S3・CelesTrak・組み込みデフォルトから
    最も新しい Epoch を持つ有効な TLE ペアを選択して返す。
    """
    if allow_live_fetch is None:
        env_live = os.getenv("ENABLE_LIVE_TLE_FETCH", "true").lower() in ("true", "1", "yes")
        in_pytest = "PYTEST_CURRENT_TEST" in os.environ
        allow_live_fetch = env_live and not in_pytest

    if explicit_tle and len(explicit_tle) == 2 and _is_valid_tle(explicit_tle[0], explicit_tle[1]):
        return (explicit_tle[0], explicit_tle[1])

    norad_id = resolve_norad_id(satellite)
    found: List[Tuple[float, Tuple[str, str]]] = []

    fallback = resolve_fallback_tle(satellite)
    if fallback and _is_valid_tle(fallback[0], fallback[1]):
        found.append((parse_tle_epoch(fallback[0]), fallback))

    if s3_client is not None or allow_live_fetch:
        _refresh_cached_tle_text(
            LIVE_TLE_CACHE_PATH,
            s3_client=s3_client,
            bucket=bucket,
            allow_live_fetch=bool(allow_live_fetch),
        )

    candidates: List[Path] = []
    if tle_path:
        candidates.append(Path(tle_path))
    env_tle_path = os.getenv("TLE_FILE_PATH")
    if env_tle_path:
        candidates.append(Path(env_tle_path))
    candidates.extend([
        LIVE_TLE_CACHE_PATH,
        Path("apps/ground-station/data/cache/tles.txt"),
        Path("/root/.config/satdump/satdump_tles.txt"),
        Path("/app/assets/satdump_tles.txt"),
        Path("apps/satellite-analyzer/assets/satdump_tles.txt"),
    ])

    for cand in candidates:
        try:
            if cand.is_file():
                text = cand.read_text(encoding="utf-8", errors="replace")
                found.extend(_extract_matching_tles(text, satellite, norad_id))
        except OSError:
            pass

    if not found:
        return None

    found.sort(key=lambda item: item[0], reverse=True)
    return found[0][1]
