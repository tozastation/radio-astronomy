"""第一段階の固定カタログ。赤経は時、赤緯は度（J2000 の計画用概位）。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Target:
    key: str
    name: str
    ra_hours: float
    dec_deg: float

    @property
    def ra_deg(self) -> float:
        return self.ra_hours * 15.0


# docs/superpowers/specs/2026-10-12-seestar-sky-window-design.md 第4節
CATALOG: tuple[Target, ...] = (
    Target("polaris", "北極星", 2 + 32 / 60, 89.3),
    Target("m52", "M52", 23 + 24 / 60, 61.6),
    Target("ngc869", "二重星団", 2 + 19 / 60, 57.2),
    Target("m31", "M31", 0 + 43 / 60, 41.3),
    Target("m45", "M45", 3 + 47 / 60, 24.1),
    Target("m42", "M42", 5 + 35 / 60, -5.4),
)
