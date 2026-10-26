"""生成 8 张分类主题插画（SVG 矢量版）。

为什么不用 AI 位图：这套图要同时出现在 660px 的详情页大图和 108×76 的列表缩略图里，
位图缩到缩略图时 1.5px 的细线会被压成 0.2px，看上去就像"图没生成完"。
矢量图 + non-scaling-stroke 可以让线宽在两种尺寸下都是 1.5 个 CSS 像素，
缩略图里线条照样清楚。

风格：960×612 纸色底，墨绿细线 + 金色点睛，几何抽象，不出现任何文字。

用法：python scripts/gen_topic_svg.py
"""

from __future__ import annotations

import math
import pathlib

INK = "#3b4a42"
INK_SOFT = "#78857c"
GOLD = "#b8873b"
PAPER = "#f1e8db"

W, H = 960, 612

HEAD = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img">
<style>
  .k  {{ fill: none; stroke: {INK}; stroke-width: 1.5; vector-effect: non-scaling-stroke; }}
  .kd {{ fill: none; stroke: {INK}; stroke-width: 1.3; stroke-dasharray: 7 7;
         vector-effect: non-scaling-stroke; opacity: .55; }}
  .ks {{ fill: none; stroke: {INK_SOFT}; stroke-width: 1.1;
         vector-effect: non-scaling-stroke; opacity: .45; }}
  .g  {{ fill: none; stroke: {GOLD}; stroke-width: 1.6; vector-effect: non-scaling-stroke; }}
  .gd {{ fill: none; stroke: {GOLD}; stroke-width: 1.4; stroke-dasharray: 5 7;
         vector-effect: non-scaling-stroke; opacity: .85; }}
  .kb {{ fill: {INK}; opacity: .055; }}
  .gb {{ fill: {GOLD}; }}
</style>
<rect width="{W}" height="{H}" fill="{PAPER}"/>
"""

FOOT = "</svg>\n"


def poly(points, cls="k"):
    pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    return f'  <polyline class="{cls}" points="{pts}"/>\n'


def circle(cx, cy, r, cls="k"):
    return f'  <circle class="{cls}" cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}"/>\n'


def line(x1, y1, x2, y2, cls="k"):
    return f'  <line class="{cls}" x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}"/>\n'


def ring_points(cx, cy, r, n, start=-90):
    return [
        (cx + r * math.cos(math.radians(start + i * 360 / n)),
         cy + r * math.sin(math.radians(start + i * 360 / n)))
        for i in range(n)
    ]


# ---------------------------------------------------------------- 各分类图形


def brand() -> str:
    """品牌动态：影响力从中心一圈圈扩散出去，轨道上有几个落点。"""
    cx, cy = 470, 312
    s = circle(cx, cy, 17, "gb")
    s += circle(cx, cy, 82)
    s += circle(cx, cy, 152, "kd")
    s += circle(cx, cy, 216, "kd")
    for x, y in ring_points(cx, cy, 216, 8)[1:5]:
        s += circle(x, y, 6, "gb")
    s += '  <path class="g" d="M470 312 C 560 268 616 214 706 176"/>\n'
    s += '  <path class="g" d="M706 176 L 686 178 M706 176 L 700 196"/>\n'
    s += circle(cx, cy, 124, "ks")
    return s


def market() -> str:
    """市场数据：一根上升折线压在等距细柱上，末端一个金色落点。"""
    base_y = 486
    s = ""
    heights = [64, 88, 74, 104, 96, 128, 118, 152, 140, 176, 168, 200, 188, 224]
    for i, h in enumerate(heights):
        x = 136 + i * 52
        s += f'  <rect class="kb" x="{x}" y="{base_y - h}" width="22" height="{h}" rx="3"/>\n'
    pts = [(136, 402), (240, 362), (344, 380), (448, 316), (552, 288), (656, 232), (760, 196)]
    s += poly(pts, "k")
    for x, y in pts[:-1]:
        s += circle(x, y, 4.6)
    s += circle(pts[-1][0], pts[-1][1], 9, "gb")
    s += line(120, base_y, 860, base_y, "ks")
    s += line(120, base_y, 120, 176, "ks")
    s += line(120, 250, 860, 250, "kd")
    return s


def safety() -> str:
    """质量安全：六边形护栏 + 内层三角 + 中心合格点。"""
    cx, cy = 480, 306
    s = poly(ring_points(cx, cy, 212, 6, -90), "k")
    s += poly(ring_points(cx, cy, 212, 6, -90) + [ring_points(cx, cy, 212, 6, -90)[0]], "k")
    s += poly(ring_points(cx, cy, 152, 6, -90), "kd")
    s += poly(ring_points(cx, cy, 92, 3, -90) + [ring_points(cx, cy, 92, 3, -90)[0]], "k")
    s += circle(cx, cy, 11, "gb")
    for x, y in ring_points(cx, cy, 212, 6, -90):
        s += circle(x, y, 5, "gb")
    s += circle(cx, cy, 268, "ks")
    return s


def supply() -> str:
    """供应链：四个环相互扣合，环环相扣才成链。"""
    y, w, h, r = 306, 214, 132, 66
    xs = [132, 316, 500, 684]
    s = ""
    for i, x in enumerate(xs):
        cls = "k" if i % 2 == 0 else "g"
        s += (f'  <rect class="{cls}" x="{x}" y="{y - h / 2}" width="{w}" height="{h}" '
              f'rx="{r}" fill="{PAPER}"/>\n')
    s += line(110, 470, 850, 470, "kd")
    s += circle(110, 306, 5, "gb")
    s += circle(850, 306, 5, "gb")
    return s


def cross() -> str:
    """跨境电商：两片市场交叠，光线在交点汇聚后越境而出。"""
    s = circle(366, 250, 176)
    s += circle(688, 392, 226)
    for x0 in (150, 336, 470):
        s += f'  <path class="g" d="M{x0} 612 L 506 332"/>\n'
    s += circle(506, 332, 5.5, "gb")
    s += '  <path class="g" d="M506 332 C 596 300 700 268 812 260"/>\n'
    s += '  <path class="g" d="M812 260 L 790 262 M812 260 L 806 282"/>\n'
    s += circle(506, 332, 46, "ks")
    return s


def policy() -> str:
    """政策法规：三条并行的条款页，最上面一页被逐条划线，右下角落一枚印章。"""
    s = ""
    for dx, dy in ((-26, -26), (-13, -13), (0, 0)):
        s += (f'  <rect class="k" x="{310 + dx}" y="{96 + dy}" width="352" height="420" rx="10" '
              f'fill="{PAPER}"/>\n')
    for i, wdt in enumerate((258, 214, 244, 176, 232)):
        y = 186 + i * 46
        s += line(340, y, 340 + wdt, y, "ks")
    s += line(340, 156, 452, 156, "k")
    s += circle(596, 452, 34, "g")
    s += circle(596, 452, 6, "gb")
    s += circle(596, 452, 52, "gd")
    return s


def global_() -> str:
    """海外动态：一颗经纬球，两地之间一条虚线航线，落点标金。"""
    cx, cy, r = 480, 306, 226
    s = circle(cx, cy, r)
    s += f'  <ellipse class="ks" cx="{cx}" cy="{cy}" rx="{r}" ry="76"/>\n'
    s += f'  <ellipse class="ks" cx="{cx}" cy="{cy}" rx="{r}" ry="150"/>\n'
    s += f'  <ellipse class="ks" cx="{cx}" cy="{cy}" rx="76" ry="{r}"/>\n'
    s += f'  <ellipse class="ks" cx="{cx}" cy="{cy}" rx="150" ry="{r}"/>\n'
    s += line(cx - r, cy, cx + r, cy, "kd")
    s += '  <path class="gd" d="M330 236 Q 480 120 646 300"/>\n'
    s += circle(330, 236, 8, "gb")
    s += circle(646, 300, 8, "gb")
    s += circle(646, 300, 26, "g")
    return s


def industry() -> str:
    """行业动态：一张节点网络，中心密、边缘疏，几条主链路连起来。"""
    nodes = [
        (480, 306), (410, 262), (556, 268), (452, 372), (566, 358),
        (330, 300), (636, 322), (392, 196), (592, 200), (520, 440),
        (300, 396), (676, 424), (250, 236), (720, 250), (368, 462),
        (610, 470), (188, 330), (790, 340), (452, 128), (520, 150),
    ]
    s = ""
    for i, (x1, y1) in enumerate(nodes):
        for x2, y2 in nodes[i + 1:]:
            if math.dist((x1, y1), (x2, y2)) < 132:
                s += line(x1, y1, x2, y2, "ks")
    for i, (x, y) in enumerate(nodes):
        cls = "gb" if i in (0, 7, 13, 17) else "k"
        s += circle(x, y, 9 if cls == "gb" else 4.4, cls)
    return s


TOPICS = {
    "brand": brand,
    "market": market,
    "safety": safety,
    "supply": supply,
    "cross": cross,
    "policy": policy,
    "global": global_,
    "industry": industry,
}


def main() -> None:
    out = pathlib.Path(__file__).resolve().parent.parent / "assets" / "topic"
    out.mkdir(parents=True, exist_ok=True)
    for name, fn in TOPICS.items():
        path = out / f"{name}.svg"
        path.write_text(HEAD + fn() + FOOT, encoding="utf-8")
        print(f"{path.name:>14}  {path.stat().st_size:>5} B")


if __name__ == "__main__":
    main()
