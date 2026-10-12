"""今夜の一文と、星ごとの窓の区間。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from seestar_plan.catalog import CATALOG, Target
from seestar_plan.sky import Interval, astronomical_night, visible_intervals

JST = ZoneInfo("Asia/Tokyo")
SENTENCE_STAY = "今夜は北のガラス際・中央。動かさない。"
SENTENCE_NONE = "今夜は北の窓に、30 分以上とどまる対象がない。"
MIN_MINUTES = 30.0


@dataclass(frozen=True)
class Site:
    latitude_deg: float = 35.6895
    longitude_deg: float = 139.6917
    alt_ridge_deg: float = 10.0
    alt_max_deg: float = 85.0

    @classmethod
    def from_file(cls, path: str) -> "Site":
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
        base = cls()
        return cls(
            latitude_deg=float(raw.get("latitude_deg", base.latitude_deg)),
            longitude_deg=float(raw.get("longitude_deg", base.longitude_deg)),
            alt_ridge_deg=float(raw.get("alt_ridge_deg", base.alt_ridge_deg)),
            alt_max_deg=float(raw.get("alt_max_deg", base.alt_max_deg)),
        )


@dataclass(frozen=True)
class TargetPlan:
    target: Target
    intervals: tuple[Interval, ...]

    @property
    def longest_minutes(self) -> float:
        if not self.intervals:
            return 0.0
        return max(item.minutes for item in self.intervals)


@dataclass(frozen=True)
class NightPlan:
    site: Site
    night_start: datetime
    night_end: datetime
    targets: tuple[TargetPlan, ...]
    sentence: str


def build_plan(now: datetime, site: Site, targets: tuple[Target, ...] = CATALOG) -> NightPlan:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    night_start, night_end = astronomical_night(now, site.latitude_deg, site.longitude_deg)
    planned: list[TargetPlan] = []
    for target in targets:
        intervals = visible_intervals(
            night_start,
            night_end,
            site.latitude_deg,
            site.longitude_deg,
            target.ra_deg,
            target.dec_deg,
            site.alt_ridge_deg,
            site.alt_max_deg,
        )
        planned.append(TargetPlan(target, tuple(intervals)))
    stays = any(item.longest_minutes >= MIN_MINUTES for item in planned)
    sentence = SENTENCE_STAY if stays else SENTENCE_NONE
    return NightPlan(site, night_start, night_end, tuple(planned), sentence)


def format_interval(interval: Interval) -> str:
    start = interval.start.astimezone(JST).strftime("%H:%M")
    end = interval.end.astimezone(JST).strftime("%H:%M")
    return f"{start}–{end}（{interval.minutes:.0f}分）"


def plan_as_dict(plan: NightPlan) -> dict:
    return {
        "sentence": plan.sentence,
        "night_start": plan.night_start.astimezone(JST).isoformat(),
        "night_end": plan.night_end.astimezone(JST).isoformat(),
        "alt_ridge_deg": plan.site.alt_ridge_deg,
        "alt_max_deg": plan.site.alt_max_deg,
        "targets": [
            {
                "name": item.target.name,
                "longest_minutes": round(item.longest_minutes, 1),
                "intervals": [
                    {
                        "start": window.start.astimezone(JST).isoformat(),
                        "end": window.end.astimezone(JST).isoformat(),
                        "minutes": round(window.minutes, 1),
                        "label": format_interval(window),
                    }
                    for window in item.intervals
                ],
            }
            for item in plan.targets
        ],
    }
