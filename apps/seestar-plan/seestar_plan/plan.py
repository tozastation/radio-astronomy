"""場所の設定と、今夜の窓ごと・星ごとの区間。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

from seestar_plan.catalog import CATALOG, Target
from seestar_plan.sky import Interval, Window, astronomical_night, visible_intervals

# 東の床は向かいの棟の屋上の仰角、方位は短辺の手すりから見える範囲。
# どちらも現地で測るまでは、見込みの控えめな側を置く。
DEFAULT_WINDOWS: tuple[Window, ...] = (
    Window("north", "北の長辺", 270.0, 90.0, 10.0, 85.0),
    Window("east", "東の短辺", 45.0, 135.0, 20.0, 85.0),
)


@dataclass(frozen=True)
class Site:
    latitude_deg: float = 35.6895
    longitude_deg: float = 139.6917
    windows: tuple[Window, ...] = field(default=DEFAULT_WINDOWS)

    @classmethod
    def from_file(cls, path: str) -> "Site":
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
        base = cls()
        windows = tuple(
            Window(
                key=str(item["key"]),
                name=str(item.get("name", item["key"])),
                az_from_deg=float(item["az_from_deg"]),
                az_to_deg=float(item["az_to_deg"]),
                floor_deg=float(item["floor_deg"]),
                ceiling_deg=float(item["ceiling_deg"]),
            )
            for item in raw.get("windows", [])
        )
        return cls(
            latitude_deg=float(raw.get("latitude_deg", base.latitude_deg)),
            longitude_deg=float(raw.get("longitude_deg", base.longitude_deg)),
            windows=windows or base.windows,
        )


@dataclass(frozen=True)
class TargetPlan:
    target: Target
    window: Window
    intervals: tuple[Interval, ...]

    def upcoming(self, now: datetime) -> Interval | None:
        """いま入っている区間か、次に入る区間。"""
        for interval in self.intervals:
            if interval.end > now:
                return interval
        return None


@dataclass(frozen=True)
class NightPlan:
    site: Site
    night_start: datetime
    night_end: datetime
    targets: tuple[TargetPlan, ...]


def build_plan(now: datetime, site: Site, targets: tuple[Target, ...] = CATALOG) -> NightPlan:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    night_start, night_end = astronomical_night(now, site.latitude_deg, site.longitude_deg)
    # 惑星が一晩で動く角度は 1 度ほどなので、夜の中ほどの位置で一晩を代表させる。
    midnight = night_start + (night_end - night_start) / 2
    coordinates = {target.key: target.radec_deg(midnight) for target in targets}
    planned = tuple(
        TargetPlan(
            target,
            window,
            tuple(
                visible_intervals(
                    night_start,
                    night_end,
                    site.latitude_deg,
                    site.longitude_deg,
                    *coordinates[target.key],
                    window,
                )
            ),
        )
        for window in site.windows
        for target in targets
    )
    return NightPlan(site, night_start, night_end, planned)
