"""7Timer! の ASTRO 予報（3 時間ごとのシーイングと透明度）。

https://www.7timer.info/doc.php の「ASTRO」を参照。値は段階（1 が最良）で返る。
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

API_URL = "https://www.7timer.info/bin/astro.php"
HALF_SLOT = timedelta(hours=1, minutes=30)
MISSING = -9999

# 段階ごとの範囲の代表値。シーイングは秒角、透明度は 1 エアマスあたりの減光（等級）。
SEEING_ARCSEC = {1: 0.4, 2: 0.625, 3: 0.875, 4: 1.125, 5: 1.375, 6: 1.75, 7: 2.25, 8: 2.75}
TRANSPARENCY_MAG = {1: 0.25, 2: 0.35, 3: 0.45, 4: 0.55, 5: 0.65, 6: 0.775, 7: 0.925, 8: 1.1}


@dataclass(frozen=True)
class AstroSlot:
    at: datetime
    seeing_index: int | None
    transparency_index: int | None

    @property
    def start(self) -> datetime:
        return self.at - HALF_SLOT

    @property
    def end(self) -> datetime:
        return self.at + HALF_SLOT

    @property
    def seeing_arcsec(self) -> float | None:
        return SEEING_ARCSEC.get(self.seeing_index) if self.seeing_index else None

    @property
    def transparency_mag(self) -> float | None:
        return TRANSPARENCY_MAG.get(self.transparency_index) if self.transparency_index else None


@dataclass(frozen=True)
class AstroForecast:
    fetched_at: datetime
    slots: tuple[AstroSlot, ...]

    def at(self, moment: datetime) -> AstroSlot | None:
        for slot in self.slots:
            if slot.start <= moment < slot.end:
                return slot
        return None

    def overlapping(self, start: datetime, end: datetime) -> tuple[AstroSlot, ...]:
        return tuple(slot for slot in self.slots if slot.start < end and slot.end > start)


def _index(raw) -> int | None:
    if raw is None or raw == MISSING:
        return None
    return int(raw)


def parse_astro(payload: dict, fetched_at: datetime) -> AstroForecast:
    init = datetime.strptime(payload["init"], "%Y%m%d%H").replace(tzinfo=timezone.utc)
    slots = tuple(
        AstroSlot(
            at=init + timedelta(hours=int(row["timepoint"])),
            seeing_index=_index(row.get("seeing")),
            transparency_index=_index(row.get("transparency")),
        )
        for row in payload["dataseries"]
    )
    return AstroForecast(fetched_at, slots)


def fetch_astro(lat_deg: float, lon_deg: float, timeout: float = 20.0) -> AstroForecast:
    query = urllib.parse.urlencode(
        {
            "lon": f"{lon_deg:.2f}",
            "lat": f"{lat_deg:.2f}",
            "ac": 0,
            "unit": "metric",
            "output": "json",
            "tzshift": 0,
        }
    )
    with urllib.request.urlopen(f"{API_URL}?{query}", timeout=timeout) as response:
        payload = json.load(response)
    return parse_astro(payload, datetime.now(timezone.utc))
