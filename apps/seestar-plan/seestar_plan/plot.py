"""今夜の高度曲線と、取れる時刻の表を一枚の PNG にする。"""

from __future__ import annotations

import io

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib import font_manager

from seestar_plan.plan import JST, NightPlan, format_interval
from seestar_plan.sky import STEP, horizontal

FONT_PATH = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
COLORS = ("#1d4ed8", "#0f766e", "#b45309", "#7c3aed", "#be123c", "#334155")


def _font() -> font_manager.FontProperties:
    return font_manager.FontProperties(fname=FONT_PATH)


def render_png(plan: NightPlan) -> bytes:
    font = _font()
    figure, (table_axis, chart_axis) = plt.subplots(
        2,
        1,
        figsize=(11, 8),
        gridspec_kw={"height_ratios": [1, 2]},
        constrained_layout=True,
    )
    figure.patch.set_facecolor("white")
    table_axis.axis("off")
    table_axis.set_title(plan.sentence, fontproperties=font, loc="left", fontsize=14, pad=8)

    rows = []
    for item in plan.targets:
        if item.intervals:
            when = "、".join(format_interval(window) for window in item.intervals)
        else:
            when = "この夜は窓の外"
        rows.append([item.target.name, when])
    table = table_axis.table(cellText=rows, colLabels=["星", "北の窓に入る時刻（日本時間）"], loc="center")
    table.auto_set_font_size(False)
    table.set_fontsize(11)
    table.scale(1, 1.4)
    for cell in table.get_celld().values():
        cell.get_text().set_fontproperties(font)

    start = plan.night_start
    moments: list[datetime] = []
    cursor = start
    while cursor <= plan.night_end:
        moments.append(cursor)
        cursor += STEP * 5

    for index, item in enumerate(plan.targets):
        altitudes = [
            horizontal(
                moment,
                plan.site.latitude_deg,
                plan.site.longitude_deg,
                item.target.ra_deg,
                item.target.dec_deg,
            ).alt_deg
            for moment in moments
        ]
        chart_axis.plot(
            moments,
            altitudes,
            color=COLORS[index % len(COLORS)],
            linewidth=1.6,
            label=item.target.name,
        )

    chart_axis.axhspan(plan.site.alt_ridge_deg, plan.site.alt_max_deg, color="#bae6fd", alpha=0.45, zorder=0)
    chart_axis.axhline(plan.site.alt_ridge_deg, color="#0369a1", linewidth=1)
    chart_axis.axhline(plan.site.alt_max_deg, color="#0369a1", linewidth=1, linestyle="--")
    chart_axis.set_ylim(-20, 95)
    chart_axis.set_xlim(start, plan.night_end)
    chart_axis.set_ylabel("高度（度）", fontproperties=font)
    chart_axis.set_xlabel("日本時間", fontproperties=font)
    chart_axis.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=JST))
    chart_axis.grid(True, axis="y", alpha=0.3)
    legend = chart_axis.legend(prop=font, ncol=3, loc="upper right")
    for text in legend.get_texts():
        text.set_fontproperties(font)
    for label in chart_axis.get_xticklabels() + chart_axis.get_yticklabels():
        label.set_fontproperties(font)

    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", dpi=120)
    plt.close(figure)
    return buffer.getvalue()


def render_page(plan: NightPlan) -> str:
    rows = []
    for item in plan.targets:
        if item.intervals:
            when = "、".join(format_interval(window) for window in item.intervals)
        else:
            when = "この夜は窓の外"
        rows.append(f"<tr><td>{item.target.name}</td><td>{when}</td></tr>")
    night = (
        f"{plan.night_start.astimezone(JST).strftime('%m/%d %H:%M')}"
        f" – {plan.night_end.astimezone(JST).strftime('%m/%d %H:%M')}"
    )
    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<title>今夜の北の窓</title>
<style>
body {{ font-family: "Noto Sans CJK JP", sans-serif; margin: 2rem; color: #0f172a; }}
table {{ border-collapse: collapse; margin: 1rem 0 1.5rem; }}
td, th {{ border-bottom: 1px solid #cbd5e1; padding: 0.4rem 1rem; text-align: left; }}
img {{ max-width: 100%; height: auto; }}
p.note {{ color: #475569; }}
</style>
</head>
<body>
<h1>{plan.sentence}</h1>
<p class="note">天文薄明の夜 {night}。操作は SeeStar の公式アプリ。置く位置は北側長辺の中央、ガラスのすぐ内側。</p>
<table>
<thead><tr><th>星</th><th>北の窓に入る時刻（日本時間）</th></tr></thead>
<tbody>
{''.join(rows)}
</tbody>
</table>
<img src="/plan.png" alt="高度曲線">
</body>
</html>
"""
