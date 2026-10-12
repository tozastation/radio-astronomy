"""惑星の地心赤経・赤緯。JPL の近似軌道要素（1800〜2050 年）を使う。

https://ssd.jpl.nasa.gov/planets/approx_pos.html の Table 1。
光行差と J2000 からの歳差は入れない。計画には 1 度未満の誤差で足りる。
"""

from __future__ import annotations

import math
from datetime import datetime

from seestar_plan.sky import OBLIQUITY_J2000_DEG, julian_day

# a [au], e, I [deg], L [deg], 近日点黄経 [deg], 昇交点黄経 [deg] と、それぞれの 1 世紀あたりの変化
ELEMENTS: dict[str, tuple[tuple[float, ...], tuple[float, ...]]] = {
    "venus": (
        (0.72333566, 0.00677672, 3.39467605, 181.97909950, 131.60246718, 76.67984255),
        (0.00000390, -0.00004107, -0.00078890, 58517.81538729, 0.00268329, -0.27769418),
    ),
    "earth": (
        (1.00000261, 0.01671123, -0.00001531, 100.46457166, 102.93768193, 0.0),
        (0.00000562, -0.00004392, -0.01294668, 35999.37244981, 0.32327364, 0.0),
    ),
    "mars": (
        (1.52371034, 0.09339410, 1.84969142, -4.55343205, -23.94362959, 49.55953891),
        (0.00001847, 0.00007882, -0.00813131, 19140.30268499, 0.44441088, -0.29257343),
    ),
    "jupiter": (
        (5.20288700, 0.04838624, 1.30439695, 34.39644051, 14.72847983, 100.47390909),
        (-0.00011607, -0.00013253, -0.00183714, 3034.74612775, 0.21252668, 0.20469106),
    ),
    "saturn": (
        (9.53667594, 0.05386179, 2.48599187, 49.95424423, 92.59887831, 113.66242448),
        (-0.00125060, -0.00050991, 0.00193609, 1222.49362201, -0.41897216, -0.28867794),
    ),
    "uranus": (
        (19.18916464, 0.04725744, 0.77263783, 313.23810451, 170.95427630, 74.01692503),
        (-0.00196176, -0.00004397, -0.00242939, 428.48202785, 0.40805281, 0.04240589),
    ),
    "neptune": (
        (30.06992276, 0.00859048, 1.77004347, -55.12002969, 44.96476227, 131.78422574),
        (0.00026291, 0.00005105, 0.00035372, 218.45945325, -0.32241464, -0.00508664),
    ),
}


def _heliocentric_ecliptic(body: str, centuries: float) -> tuple[float, float, float]:
    base, rate = ELEMENTS[body]
    a, e, inc, mean_longitude, perihelion, node = (b + r * centuries for b, r in zip(base, rate))
    argument = math.radians(perihelion - node)
    anomaly = math.radians((mean_longitude - perihelion + 180.0) % 360.0 - 180.0)
    eccentric = anomaly + e * math.sin(anomaly)
    for _ in range(10):
        eccentric -= (eccentric - e * math.sin(eccentric) - anomaly) / (1.0 - e * math.cos(eccentric))
    x_orbit = a * (math.cos(eccentric) - e)
    y_orbit = a * math.sqrt(1.0 - e * e) * math.sin(eccentric)
    inc, node = math.radians(inc), math.radians(node)
    cw, sw, cn, sn, ci, si = (
        math.cos(argument), math.sin(argument), math.cos(node), math.sin(node), math.cos(inc), math.sin(inc),
    )
    x = (cw * cn - sw * sn * ci) * x_orbit + (-sw * cn - cw * sn * ci) * y_orbit
    y = (cw * sn + sw * cn * ci) * x_orbit + (-sw * sn + cw * cn * ci) * y_orbit
    z = (sw * si) * x_orbit + (cw * si) * y_orbit
    return x, y, z


def planet_radec_deg(body: str, moment: datetime) -> tuple[float, float]:
    """地心の赤経・赤緯（度）。地球の位置は地球と月の重心で代用する。"""
    centuries = (julian_day(moment) - 2451545.0) / 36525.0
    px, py, pz = _heliocentric_ecliptic(body, centuries)
    ex, ey, ez = _heliocentric_ecliptic("earth", centuries)
    x, y, z = px - ex, py - ey, pz - ez
    eps = math.radians(OBLIQUITY_J2000_DEG)
    y_eq = y * math.cos(eps) - z * math.sin(eps)
    z_eq = y * math.sin(eps) + z * math.cos(eps)
    ra = math.degrees(math.atan2(y_eq, x)) % 360.0
    dec = math.degrees(math.atan2(z_eq, math.hypot(x, y_eq)))
    return ra, dec
