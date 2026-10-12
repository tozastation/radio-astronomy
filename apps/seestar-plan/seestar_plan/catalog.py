"""計画の対象。恒星・星雲・星団は J2000 の概位、惑星は時刻から位置を求める。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from seestar_plan.planets import planet_radec_deg


@dataclass(frozen=True)
class Target:
    key: str
    name: str
    kind: str
    ra_hours: float = 0.0
    dec_deg: float = 0.0

    @property
    def moves(self) -> bool:
        return self.kind == "planet"

    def radec_deg(self, moment: datetime) -> tuple[float, float]:
        if self.moves:
            return planet_radec_deg(self.key, moment)
        return self.ra_hours * 15.0, self.dec_deg


def _fixed(key: str, name: str, ra_hours: float, dec_deg: float) -> Target:
    return Target(key, name, "deep_sky", ra_hours, dec_deg)


def _planet(key: str, name: str) -> Target:
    return Target(key, name, "planet")


# docs/superpowers/specs/2026-10-12-seestar-sky-window-design.md 第4節
# 水星は太陽から 28 度以内にしか離れず、天文薄明の夜にはほぼ地平線の下なので入れない。
CATALOG: tuple[Target, ...] = (
    _fixed("polaris", "北極星", 2 + 32 / 60, 89.3),
    _fixed("m52", "M52", 23 + 24 / 60, 61.6),
    _fixed("ngc869", "二重星団", 2 + 19 / 60, 57.2),
    _fixed("m31", "M31", 0 + 43 / 60, 41.3),
    _fixed("m45", "M45", 3 + 47 / 60, 24.1),
    _fixed("m42", "M42", 5 + 35 / 60, -5.4),
    _planet("venus", "金星"),
    _planet("mars", "火星"),
    _planet("jupiter", "木星"),
    _planet("saturn", "土星"),
    _planet("uranus", "天王星"),
    _planet("neptune", "海王星"),
)
