"""施設と住宅のシルエットの絵（SVG）を、箱・円柱・屋根などの形の組み合わせから作る。

使い方: python tools/sprites/silhouette.py <出力フォルダ> [--style color|clay|flat]
  例: python tools/sprites/silhouette.py server/static/themes/default/sprites
  color: 部品ごとの色で塗り、面の向きで濃淡を付ける（上面が明るく、左の側面・右の側面の順に暗い）
  clay: 色を付けず、白い模型のように 1 色の濃淡で面を分ける
  flat: 1 色で塗りつぶす（輪郭だけ）

形は下の SHAPES に、マスの中の位置（u, v は 0〜1。u が増えると右下、v が増えると左下）と
高さ（ピクセル）で書く。決まり（幅 64、絵の下の真ん中がマスの下の頂点）は docs/map-art.md のとおり。
標準ライブラリだけで動く。
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass, field
from pathlib import Path

TILE_W = 64
TILE_H = 32

# 部品の色
WALL = "#ebe5d8"  # 明るい壁
CONCRETE = "#c9c3b8"
WHITE = "#f4f1ea"
CREAM = "#efe3c8"
SAND = "#dccb98"  # 砂岩
BRICK = "#a8604a"
ROOF_TILE = "#b9573b"  # 瓦（赤茶）
ROOF_BROWN = "#8b5a44"
ROOF_SLATE = "#5f6d7c"
ROOF_STEEL = "#6c7a89"
METAL = "#aab0b6"
IRON = "#55514d"
RUST = "#8a4b3c"
DARK = "#555b63"
COAL = "#34363a"
RED = "#c8473a"
YELLOW = "#d9a62a"
TREE = "#3f7d3a"
TRUNK = "#6b4a2b"
WOOD = "#9a7347"
SOIL = "#9b7a4c"
CROP = "#d9bf5a"
ROCK = "#9c917f"
STONE = "#c4bcad"
GLASS = "#9db6c7"
GLASS2 = "#b7c4cc"
COPPER = "#6fa98f"
WATER = "#3d7ea6"
PAVEMENT = "#d9d4c8"  # 岸壁・屋上などの床
CONTAINER_BLUE = "#4f8fb8"

# 基の色と、面ごとの明るさ（上面・屋根の左向き・左の側面・屋根の右向き・右の側面）。
# color は部品の色を使い、base は色のない部品にだけ使う
STYLES = {
    "color": {
        "base": (233, 229, 220),
        **{"top": 1.0, "slope_l": 0.92, "left": 0.82, "slope_r": 0.74, "right": 0.66},
    },
    "clay": {
        "base": (233, 229, 220),
        **{"top": 1.0, "slope_l": 0.94, "left": 0.84, "slope_r": 0.76, "right": 0.68},
    },
    "flat": {
        "base": (52, 60, 72),
        **{"top": 1.0, "slope_l": 1.0, "left": 1.0, "slope_r": 1.0, "right": 1.0},
    },
}


def iso(u: float, v: float, z: float) -> tuple[float, float]:
    """マスの中の位置と高さ → 絵の中の位置（下の頂点が (32, 32) になる仮の座標）。"""
    return 32 + (u - v) * TILE_W / 2, (u + v) * TILE_H / 2 - z


@dataclass
class Face:
    shade: str
    color: str
    points: list[tuple[float, float]] = field(default_factory=list)
    path: str | None = None  # 曲線を含む面（円柱など）
    ymin: float = 0.0  # path の面のいちばん上の y


@dataclass
class Prim:
    """形 1 つ。bounds はマスの中の足元の範囲 (u0, v0, u1, v1)、z0 は下の端の高さ。"""

    bounds: tuple[float, float, float, float]
    z0: float
    faces: list[Face]


def _before(a: Prim, b: Prim) -> bool:
    """a を b より先に（奥に）描かなければならないか。

    足元が u の向きに離れていて v の範囲が重なれば u の小さい方、v の向きも同じ。
    足元が重なれば低い方。斜めに離れているものどうしは決めない。
    """
    au0, av0, au1, av1 = a.bounds
    bu0, bv0, bu1, bv1 = b.bounds
    u_apart = au1 <= bu0 or bu1 <= au0
    v_apart = av1 <= bv0 or bv1 <= av0
    if u_apart and v_apart:
        return au1 <= bu0 and av1 <= bv0
    if u_apart:
        return au1 <= bu0
    if v_apart:
        return av1 <= bv0
    return a.z0 < b.z0


def _depth(p: Prim) -> tuple[float, float]:
    u0, v0, u1, v1 = p.bounds
    return (u0 + u1 + v0 + v1, p.z0)


def order(prims: list[Prim]) -> list[Prim]:
    """奥から手前・下から上の順に並べる。決まらないものは足元の中心が奥のものから。"""
    rest = list(prims)
    out: list[Prim] = []
    while rest:
        free = [p for p in rest if not any(_before(q, p) for q in rest if q is not p)]
        p = min(free or rest, key=_depth)  # 循環したら奥のものから
        rest.remove(p)
        out.append(p)
    return out


def box(
    u0: float,
    v0: float,
    u1: float,
    v1: float,
    z0: float,
    h: float,
    c: str = WALL,
    top: str | None = None,
) -> Prim:
    """直方体。c は側面の色、top は上面の色（なければ c）。"""
    zt = z0 + h
    t = top or c
    return Prim(
        (u0, v0, u1, v1),
        z0,
        [
            Face("left", c, [iso(u0, v1, z0), iso(u1, v1, z0), iso(u1, v1, zt), iso(u0, v1, zt)]),
            Face("right", c, [iso(u1, v0, z0), iso(u1, v1, z0), iso(u1, v1, zt), iso(u1, v0, zt)]),
            Face("top", t, [iso(u0, v0, zt), iso(u1, v0, zt), iso(u1, v1, zt), iso(u0, v1, zt)]),
        ],
    )


def gable(
    u0: float,
    v0: float,
    u1: float,
    v1: float,
    z0: float,
    r: float,
    along: str = "u",
    c: str = ROOF_TILE,
    wall: str = WALL,
) -> Prim:
    """切妻屋根。along の向きに棟が通る。c は屋根、wall は妻壁（三角の壁）の色。"""
    if along == "u":
        vm = (v0 + v1) / 2
        a, b = iso(u0, vm, z0 + r), iso(u1, vm, z0 + r)
        faces = [
            Face("slope_r", c, [iso(u0, v0, z0), iso(u1, v0, z0), b, a]),
            Face("slope_l", c, [iso(u0, v1, z0), iso(u1, v1, z0), b, a]),
            Face("right", wall, [iso(u1, v0, z0), iso(u1, v1, z0), b]),
        ]
    else:
        um = (u0 + u1) / 2
        a, b = iso(um, v0, z0 + r), iso(um, v1, z0 + r)
        faces = [
            Face("slope_l", c, [iso(u0, v0, z0), iso(u0, v1, z0), b, a]),
            Face("slope_r", c, [iso(u1, v0, z0), iso(u1, v1, z0), b, a]),
            Face("left", wall, [iso(u0, v1, z0), iso(u1, v1, z0), b]),
        ]
    return Prim((u0, v0, u1, v1), z0 + 0.1, faces)


def hip(
    u0: float, v0: float, u1: float, v1: float, z0: float, r: float, c: str = ROOF_SLATE
) -> Prim:
    """四角すいの屋根。"""
    apex = iso((u0 + u1) / 2, (v0 + v1) / 2, z0 + r)
    return Prim(
        (u0, v0, u1, v1),
        z0 + 0.1,
        [
            Face("slope_l", c, [iso(u0, v1, z0), iso(u1, v1, z0), apex]),
            Face("slope_r", c, [iso(u1, v0, z0), iso(u1, v1, z0), apex]),
        ],
    )


def _d(*items: object) -> str:
    """SVG のパスの文字列。数は小数 2 桁にする。"""
    return " ".join(f"{x:.2f}" if isinstance(x, float) else str(x) for x in items)


def _ellipse(uc: float, vc: float, r: float, z: float) -> tuple[float, float, float, float]:
    cx, cy = iso(uc, vc, z)
    return cx, cy, r * TILE_W / 2 * math.sqrt(2), r * TILE_H / 2 * math.sqrt(2)


def cylinder(
    uc: float,
    vc: float,
    r: float,
    z0: float,
    h: float,
    r_top: float | None = None,
    cap: str = "flat",
    c: str = METAL,
    cap_c: str | None = None,
) -> Prim:
    """円柱。r_top で上を細く（円すい台に）する。

    cap は上の端の形: flat（平ら）・dome（丸屋根）・cone（とがり屋根）。
    cap_c はその色（なければ c）。
    """
    r_top = r if r_top is None else r_top
    cc = cap_c or c
    cx, yb, rxb, ryb = _ellipse(uc, vc, r, z0)
    _, yt, rxt, ryt = _ellipse(uc, vc, r_top, z0 + h)

    faces = []
    for sign, shade, sweep in ((-1, "left", 1), (1, "right", 0)):
        d = _d("M", cx, yb + ryb, "A", rxb, ryb, 0, 0, sweep, cx + sign * rxb, yb)
        d += " " + _d("L", cx + sign * rxt, yt, "L", cx, yt, "Z")
        faces.append(Face(shade, c, path=d, ymin=yt))
    if cap == "dome":
        top = yt - rxt * 0.75
        for sign, shade, sweep in ((-1, "slope_l", 1), (1, "slope_r", 0)):
            x = cx + sign * rxt
            d = _d("M", x, yt, "Q", x, top, cx, top, "L", cx, yt + ryt)
            d += " " + _d("A", rxt, ryt, 0, 0, sweep, x, yt, "Z")
            faces.append(Face(shade, cc, path=d, ymin=top))
    elif cap == "cone":
        top = yt - rxt * 1.2
        faces += [
            Face("slope_l", cc, [(cx - rxt, yt), (cx, top), (cx, yt + ryt)]),
            Face("slope_r", cc, [(cx + rxt, yt), (cx, top), (cx, yt + ryt)]),
        ]
    else:
        arc = ("A", rxt, ryt, 0, 1, 0)
        d = _d("M", cx - rxt, yt, *arc, cx + rxt, yt, *arc, cx - rxt, yt, "Z")
        faces.append(Face("top", cc, path=d, ymin=yt - ryt))
    return Prim((uc - r, vc - r, uc + r, vc + r), z0, faces)


def cone(uc: float, vc: float, r: float, h: float, z0: float = 0, c: str = TREE) -> Prim:
    """円すい（木の葉の部分や、砂利・石炭の山）。"""
    cx, yb, rx, ry = _ellipse(uc, vc, r, z0)
    _, ya, _, _ = _ellipse(uc, vc, r, z0 + h)
    faces = []
    for sign, shade, sweep in ((-1, "left", 1), (1, "right", 0)):
        x = cx + sign * rx
        d = _d("M", x, yb, "L", cx, ya, "L", cx, yb + ry, "A", rx, ry, 0, 0, sweep, x, yb, "Z")
        faces.append(Face(shade, c, path=d, ymin=ya))
    return Prim((uc - r, vc - r, uc + r, vc + r), z0 + 0.2, faces)


def tree(uc: float, vc: float, r: float, h: float, z0: float = 0) -> list[Prim]:
    """針葉樹: 細い幹と円すい。"""
    return [
        cylinder(uc, vc, r * 0.18, z0, h * 0.25, c=TRUNK),
        cone(uc, vc, r, h * 0.8, z0 + h * 0.2),
    ]


def house(
    u0: float,
    v0: float,
    u1: float,
    v1: float,
    h: float,
    r: float,
    along: str = "u",
    wall: str = CREAM,
    roof: str = ROOF_TILE,
) -> list[Prim]:
    """切妻屋根の家。"""
    return [box(u0, v0, u1, v1, 0, h, wall), gable(u0, v0, u1, v1, h, r, along, roof, wall)]


def hull(
    u0: float,
    v0: float,
    u1: float,
    v1: float,
    z0: float,
    h: float,
    bow: float,
    c: str = METAL,
    top: str | None = None,
) -> Prim:
    """船体（の一段）。u の向きに長く、u1 の側がとがった船首（長さ bow）。

    見えるのは左の側面・船首の左の面・上面だけ（右の面は船首にさえぎられる）。
    """
    zt = z0 + h
    vm = (v0 + v1) / 2
    ub = u1 - bow
    side = [iso(u0, v1, z0), iso(ub, v1, z0), iso(ub, v1, zt), iso(u0, v1, zt)]
    bow_l = [iso(ub, v1, z0), iso(u1, vm, z0), iso(u1, vm, zt), iso(ub, v1, zt)]
    deck = [iso(u0, v0, zt), iso(ub, v0, zt), iso(u1, vm, zt), iso(ub, v1, zt), iso(u0, v1, zt)]
    return Prim(
        (u0, v0, u1, v1),
        z0,
        [Face("left", c, side), Face("slope_r", c, bow_l), Face("top", top or c, deck)],
    )


def _band(cx: float, cy: float, rx: float, ry: float, w: float, vertical: bool) -> str:
    """球の表面の線（手前の半分の楕円の弧を、幅 w の帯にしたもの）。"""
    if vertical:  # 経線: 上の端から右を回って下の端へ
        return (
            _d("M", cx, cy - ry, "A", rx, ry, 0, 0, 1, cx, cy + ry)
            + " "
            + _d("A", max(rx - w, 0.1), ry, 0, 0, 0, cx, cy - ry, "Z")
        )
    # 赤道: 左の端から下を回って右の端へ
    return (
        _d("M", cx - rx, cy, "A", rx, ry, 0, 0, 0, cx + rx, cy)
        + " "
        + _d("A", rx, max(ry - w, 0.1), 0, 0, 1, cx - rx, cy, "Z")
    )


def globe(uc: float, vc: float, z0: float, r: float, c: str = "#4f8fb8", line: str = WHITE) -> Prim:
    """地球儀の球（半径 r ピクセル）。下の端が (uc, vc, z0)。赤道と経線を 1 本ずつ描く。"""
    x, yb = iso(uc, vc, z0)
    cy = yb - r
    faces = []
    for shade, sweep in (("slope_l", 0), ("slope_r", 1)):  # 左の半分・右の半分
        d = _d("M", x, cy - r, "A", r, r, 0, 0, sweep, x, cy + r, "Z")
        faces.append(Face(shade, c, path=d, ymin=cy - r))
    w = r * 0.18
    faces.append(Face("top", line, path=_band(x, cy, r, r / 2, w, False), ymin=cy))
    faces.append(Face("top", line, path=_band(x, cy, r * 0.45, r, w, True), ymin=cy - r))
    ru = r / (TILE_W / 2 * math.sqrt(2))
    return Prim((uc - ru, vc - ru, uc + ru, vc + ru), z0, faces)


def strut(
    a: tuple[float, float, float], b: tuple[float, float, float], w: float = 1.2, c: str = RED
) -> Prim:
    """斜めの細い部材（クレーンの控えなど）。

    a, b は両端の (u, v, z)、w は絵の上の太さ（ピクセル）。
    """
    (xa, ya), (xb, yb) = iso(*a), iso(*b)
    n = math.hypot(xb - xa, yb - ya) or 1.0
    dx, dy = -(yb - ya) / n * w / 2, (xb - xa) / n * w / 2
    pts = [(xa + dx, ya + dy), (xb + dx, yb + dy), (xb - dx, yb - dy), (xa - dx, ya - dy)]
    bounds = (min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1]))
    return Prim(bounds, min(a[2], b[2]), [Face("left", c, pts)])


# --- 形の一覧 ----------------------------------------------------------------

SHAPES: dict[str, list[Prim]] = {
    "facility/farm": [
        box(0.08, 0.08, 0.92, 0.54, 0, 1, SOIL),
        *[box(0.1, v, 0.9, v + 0.06, 1, 2, CROP) for v in (0.1, 0.22, 0.34, 0.46)],
        *house(0.55, 0.58, 0.85, 0.88, 8, 6, "v", BRICK, DARK),
        cylinder(0.25, 0.75, 0.08, 0, 18, cap="dome", cap_c=ROOF_SLATE),
    ],
    "facility/lumber_camp": [
        *tree(0.25, 0.22, 0.1, 30),
        *tree(0.5, 0.15, 0.09, 26),
        *tree(0.18, 0.5, 0.09, 24),
        *house(0.5, 0.45, 0.85, 0.8, 9, 6, "u", WOOD, ROOF_SLATE),
        *[box(0.15, 0.68 + i * 0.07, 0.42, 0.74 + i * 0.07, 0, 4, "#b08350") for i in range(3)],
    ],
    "facility/quarry": [
        # 奥から手前へ段々に切り下げた石の崖と、切り出した石・砂利の山・砕石の小屋
        box(0.08, 0.08, 0.92, 0.3, 0, 24, ROCK, STONE),
        box(0.08, 0.3, 0.92, 0.46, 0, 14, ROCK, STONE),
        box(0.08, 0.46, 0.55, 0.6, 0, 6, ROCK, STONE),
        *[box(u, 0.7, u + 0.1, 0.8, 0, 4, STONE, WHITE) for u in (0.12, 0.26)],
        box(0.18, 0.8, 0.28, 0.9, 0, 4, STONE, WHITE),
        cone(0.48, 0.78, 0.1, 8, c="#b3ab9d"),
        box(0.66, 0.55, 0.9, 0.76, 0, 12, METAL, ROOF_STEEL),
        box(0.66, 0.76, 0.9, 0.9, 0, 3, CONCRETE),
    ],
    "facility/mine": [
        *[box(u, v, u + 0.06, v + 0.06, 0, 34, IRON) for u in (0.18, 0.36) for v in (0.18, 0.36)],
        box(0.15, 0.15, 0.45, 0.45, 34, 4, RUST),
        *house(0.5, 0.45, 0.88, 0.85, 12, 7, "u", WALL, ROOF_SLATE),
        cone(0.25, 0.72, 0.13, 9, c=COAL),
    ],
    "facility/fishing_port": [
        box(0.45, 0.4, 0.98, 0.55, 0, 3, WOOD),
        box(0.45, 0.62, 0.98, 0.77, 0, 3, WOOD),
        *house(0.1, 0.15, 0.4, 0.55, 10, 7, "v", WALL, "#4f8fb8"),
        box(0.12, 0.6, 0.38, 0.85, 0, 7, WALL, "#4fa3c7"),
        box(0.85, 0.25, 0.9, 0.3, 3, 26, YELLOW),
        box(0.62, 0.25, 0.9, 0.29, 29, 2, YELLOW),
    ],
    "facility/steel_mill": [
        # 熱風炉 3 基・高炉・鋳床の建屋・煙突
        *[
            cylinder(u, v, 0.06, 0, 32, cap="dome", c=METAL)
            for u, v in ((0.12, 0.3), (0.2, 0.18), (0.33, 0.12))
        ],
        cylinder(0.86, 0.1, 0.035, 0, 54, c=BRICK),
        box(0.5, 0.15, 0.9, 0.55, 0, 16, CONCRETE),
        gable(0.5, 0.15, 0.9, 0.55, 16, 6, "u", RUST, CONCRETE),
        cylinder(0.32, 0.6, 0.17, 0, 10, c=IRON),
        cylinder(0.32, 0.6, 0.13, 10, 26, r_top=0.09, c=RUST),
        cylinder(0.32, 0.6, 0.05, 36, 12, c=IRON),
        box(0.3, 0.58, 0.62, 0.62, 44, 2, IRON),
        box(0.56, 0.64, 0.88, 0.88, 0, 9, CONCRETE, DARK),
    ],
    "facility/building_materials_plant": [
        *[cylinder(u, 0.2, 0.07, 0, 30, cap="cone", c=METAL) for u in (0.22, 0.36, 0.5)],
        box(0.15, 0.4, 0.88, 0.85, 0, 14, "#d8c7a8", "#c49a6c"),
        box(0.6, 0.45, 0.85, 0.7, 14, 8, "#d8c7a8", "#c49a6c"),
    ],
    "facility/machinery_plant": [
        cylinder(0.2, 0.2, 0.04, 0, 44, c=BRICK),
        box(0.12, 0.3, 0.88, 0.88, 0, 16, WALL),
        *[gable(0.12, v, 0.88, v + 0.14, 16, 7, "u", ROOF_STEEL) for v in (0.3, 0.44, 0.58, 0.72)],
    ],
    "facility/food_factory": [
        cylinder(0.25, 0.25, 0.1, 0, 22, cap="dome", c=WHITE),
        cylinder(0.48, 0.18, 0.1, 0, 22, cap="dome", c=WHITE),
        box(0.15, 0.45, 0.88, 0.88, 0, 15, WALL, "#e08e5a"),
        box(0.62, 0.55, 0.85, 0.8, 15, 5, METAL),
    ],
    "facility/consumer_goods_factory": [
        box(0.15, 0.15, 0.55, 0.45, 0, 26, WALL, "#b57fb3"),
        box(0.15, 0.45, 0.88, 0.88, 0, 14, "#e6dde6", "#b57fb3"),
        *[box(u, 0.6, u + 0.1, 0.7, 14, 4, METAL) for u in (0.3, 0.5, 0.7)],
    ],
    "facility/thermal_power_plant": [
        # ボイラー棟・タービン建屋・紅白の煙突 2 本・石炭の山
        box(0.12, 0.12, 0.42, 0.45, 0, 40, "#7a8088", DARK),
        box(0.42, 0.18, 0.9, 0.55, 0, 20, WALL, "#7d838b"),
        *[cylinder(u, v, 0.045, 0, 52, c=WHITE) for u, v in ((0.6, 0.09), (0.78, 0.09))],
        *[cylinder(u, v, 0.045, 52, 14, c=RED) for u, v in ((0.6, 0.09), (0.78, 0.09))],
        cone(0.7, 0.74, 0.15, 9, c=COAL),
        *[box(u, 0.6, u + 0.05, 0.65, 0, 8, METAL) for u in (0.48, 0.56)],
    ],
    "facility/hydro_power_plant": [
        box(0.1, 0.08, 0.9, 0.2, 0, 26, CONCRETE, WATER),
        box(0.1, 0.2, 0.9, 0.42, 0, 28, CONCRETE),
        box(0.1, 0.42, 0.9, 0.48, 0, 24, CONCRETE),
        *[box(u, 0.48, u + 0.08, 0.68, 0, 10, METAL) for u in (0.25, 0.45, 0.65)],
        box(0.2, 0.68, 0.8, 0.9, 0, 9, WALL, WATER),
    ],
    "facility/government_office": [
        box(0.1, 0.25, 0.9, 0.75, 0, 18, WHITE, "#d9d4c8"),
        box(0.35, 0.3, 0.65, 0.7, 18, 6, WHITE),
        cylinder(0.5, 0.5, 0.13, 24, 6, cap="dome", c=WHITE, cap_c=COPPER),
        box(0.1, 0.75, 0.9, 0.86, 0, 3, CONCRETE),
    ],
    "facility/school": [
        box(0.12, 0.15, 0.45, 0.85, 0, 14, CREAM, "#7f97ad"),
        box(0.45, 0.55, 0.88, 0.85, 0, 14, CREAM, "#7f97ad"),
        box(0.25, 0.4, 0.35, 0.5, 14, 12, WHITE),
        hip(0.25, 0.4, 0.35, 0.5, 26, 7, "#4f6f8f"),
    ],
    "facility/university": [
        box(0.1, 0.12, 0.9, 0.42, 0, 18, BRICK),
        gable(0.1, 0.12, 0.9, 0.42, 18, 7, "u", ROOF_SLATE, BRICK),
        box(0.42, 0.5, 0.58, 0.66, 0, 40, BRICK),
        hip(0.42, 0.5, 0.58, 0.66, 40, 12, ROOF_SLATE),
        box(0.1, 0.55, 0.35, 0.88, 0, 12, BRICK, CONCRETE),
        box(0.65, 0.55, 0.9, 0.88, 0, 12, BRICK, CONCRETE),
    ],
    "facility/bank": [
        # 平らな屋根に軒の出（コーニス）、正面に列柱
        box(0.2, 0.12, 0.85, 0.62, 0, 22, SAND, "#c9b680"),
        box(0.17, 0.09, 0.88, 0.65, 22, 2, "#e6d9ae"),
        box(0.32, 0.22, 0.73, 0.52, 24, 4, SAND, "#c9b680"),
        *[box(u, 0.72, u + 0.06, 0.78, 0, 16, WHITE) for u in (0.22, 0.37, 0.52, 0.67)],
        box(0.17, 0.65, 0.88, 0.82, 16, 4, "#e6d9ae"),
        box(0.14, 0.65, 0.91, 0.88, 0, 2, CONCRETE),
    ],
    # フェーズ3-1 の港・造船所・商社。港と造船所は、右手前の縁を海側の水面にする
    "facility/port": [
        # 岸壁（コンクリートの床）と水面、赤いコンテナクレーン、積んだコンテナ、倉庫、係船柱
        box(0.06, 0.06, 0.76, 0.94, 0, 4, CONCRETE, PAVEMENT),
        box(0.76, 0.04, 0.98, 0.96, 0, 0.5, WATER),
        box(0.1, 0.1, 0.36, 0.38, 4, 9, WALL),
        gable(0.1, 0.1, 0.36, 0.38, 13, 5, "u", ROOF_STEEL, WALL),
        *[
            box(u, v, u + 0.22, v + 0.08, 4 + 4 * k, 4, col)
            for u, v, k, col in (
                (0.1, 0.56, 0, RED),
                (0.1, 0.56, 1, CONTAINER_BLUE),
                (0.1, 0.66, 0, YELLOW),
                (0.1, 0.76, 0, CONTAINER_BLUE),
                (0.1, 0.76, 1, COPPER),
                (0.1, 0.86, 0, RED),
                (0.42, 0.72, 0, COPPER),
                (0.42, 0.82, 0, YELLOW),
            )
        ],
        # クレーン: 4 本の脚の上に、海の上まで張り出す腕。腕の根元に機械室、柱から腕の両端へ控え
        *[box(u, v, u + 0.04, v + 0.04, 4, 30, RED) for u in (0.48, 0.66) for v in (0.34, 0.5)],
        box(0.28, 0.36, 1.0, 0.52, 34, 3, RED),
        box(0.3, 0.38, 0.44, 0.5, 37, 6, WHITE),
        box(0.57, 0.42, 0.61, 0.46, 37, 14, RED),
        strut((0.59, 0.44, 51), (0.98, 0.44, 37)),
        strut((0.59, 0.44, 51), (0.44, 0.44, 43)),
        *[cylinder(0.72, v, 0.022, 4, 2.5, c=IRON) for v in (0.14, 0.42, 0.66, 0.88)],
    ],
    "facility/shipyard": [
        # 乾ドックで組み立て中の船体（赤い船底・とがった船首）と、それをまたぐ黄色い門形クレーンが
        # 吊った船体の部品、奥に加工の建屋、手前に鋼材
        box(0.06, 0.06, 0.82, 0.36, 0, 3, CONCRETE, PAVEMENT),
        box(0.06, 0.64, 0.82, 0.94, 0, 3, CONCRETE, PAVEMENT),
        box(0.06, 0.36, 0.14, 0.64, 0, 3, CONCRETE, PAVEMENT),
        box(0.14, 0.36, 0.82, 0.64, 0, 0.5, "#8d8a84"),
        box(0.82, 0.04, 0.98, 0.96, 0, 0.5, WATER),
        box(0.08, 0.08, 0.38, 0.32, 3, 10, WALL),
        gable(0.08, 0.08, 0.38, 0.32, 13, 5, "u", ROOF_STEEL, WALL),
        hull(0.2, 0.4, 0.78, 0.6, 0.5, 7, 0.16, RED),
        hull(0.2, 0.4, 0.78, 0.6, 7.5, 5.5, 0.16, METAL, "#c3c8cc"),
        box(0.22, 0.43, 0.34, 0.57, 13, 6, WHITE),
        *[box(u, 0.12, u + 0.1, 0.3, 3, 2 + k, ROOF_STEEL) for k, u in enumerate((0.6, 0.7))],
        *[box(0.5, v, 0.56, v + 0.06, 3, 39, YELLOW) for v in (0.12, 0.82)],
        strut((0.53, 0.5, 42), (0.53, 0.5, 25), 0.6, IRON),
        box(0.47, 0.44, 0.59, 0.56, 19, 6, METAL, "#c3c8cc"),
        box(0.48, 0.08, 0.58, 0.92, 42, 6, YELLOW),
        box(0.47, 0.44, 0.59, 0.56, 48, 4, WHITE),
    ],
    "facility/trading": [
        # 白い事務所の塔に横長の窓の帯、屋上の地球儀、正面に旗を 3 本
        box(0.12, 0.64, 0.9, 0.9, 0, 1, CONCRETE, PAVEMENT),
        box(0.14, 0.12, 0.86, 0.62, 0, 6, GLASS),
        box(0.14, 0.12, 0.86, 0.62, 6, 3, WHITE, PAVEMENT),
        *[
            prim
            for k in range(4)
            for prim in (
                box(0.22, 0.16, 0.62, 0.54, 9 + 6 * k, 3.5, GLASS),
                box(0.22, 0.16, 0.62, 0.54, 12.5 + 6 * k, 2.5, WHITE),
            )
        ],
        box(0.2, 0.14, 0.64, 0.56, 33, 2, WHITE, PAVEMENT),
        cylinder(0.42, 0.35, 0.03, 35, 3, c=METAL),
        globe(0.42, 0.35, 38, 5),
        *[box(u, 0.76, u + 0.014, 0.774, 1, 22, METAL) for u in (0.3, 0.5, 0.7)],
        *[
            box(u + 0.014, 0.762, u + 0.14, 0.772, 16.5, 6, col)
            for u, col in ((0.3, RED), (0.5, CONTAINER_BLUE), (0.7, YELLOW))
        ],
    ],
    "housing/village": [
        *house(0.15, 0.15, 0.42, 0.4, 7, 6, "u"),
        *house(0.55, 0.2, 0.8, 0.45, 7, 6, "v", roof=ROOF_BROWN),
        *house(0.25, 0.55, 0.5, 0.8, 7, 6, "v"),
    ],
    "housing/town": [
        *house(0.12, 0.12, 0.42, 0.4, 14, 6, "u", "#e2d5bd"),
        *house(0.55, 0.15, 0.85, 0.42, 10, 6, "v", roof=ROOF_BROWN),
        *house(0.12, 0.55, 0.4, 0.85, 10, 6, "v", roof=ROOF_BROWN),
        *house(0.55, 0.55, 0.85, 0.85, 16, 6, "u", "#e2d5bd"),
    ],
    "housing/city": [
        box(0.12, 0.12, 0.45, 0.45, 0, 36, "#d9cfbf", "#8f969c"),
        box(0.55, 0.12, 0.88, 0.42, 0, 26, "#e3dccd", "#8f969c"),
        box(0.12, 0.55, 0.42, 0.88, 0, 22, "#cfc8bd", "#8f969c"),
        box(0.52, 0.52, 0.88, 0.88, 0, 42, "#e3dccd", "#8f969c"),
    ],
    "housing/metropolis": [
        box(0.1, 0.1, 0.9, 0.9, 0, 6, CONCRETE),
        box(0.15, 0.15, 0.42, 0.42, 6, 58, GLASS2, "#7e8f9a"),
        box(0.58, 0.15, 0.85, 0.4, 6, 40, "#d9cfbf", "#8f969c"),
        box(0.15, 0.58, 0.4, 0.85, 6, 34, "#e3dccd", "#8f969c"),
        box(0.52, 0.52, 0.85, 0.85, 6, 66, GLASS, "#7e8f9a"),
        box(0.62, 0.62, 0.75, 0.75, 72, 8, METAL),
    ],
}


def render(prims: list[Prim], style: dict) -> str:
    prims = order(prims)
    faces = [f for p in prims for f in p.faces]
    # 縦の大きさ: いちばん上の点が絵の上の端に来るよう、全体を下へずらす
    ys = [y for f in faces for _, y in f.points]
    ys += [f.ymin for f in faces if f.path]
    top = min(min(ys), 0.0)
    off = math.ceil(-top) + 1
    height = TILE_H + off
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{TILE_W}" height="{height}" '
        f'viewBox="0 0 {TILE_W} {height}">',
        f'<g transform="translate(0 {off})" stroke-linejoin="round" stroke-width="0.4">',
    ]
    for f in faces:
        color = _color(style, f)
        if f.path:
            out.append(f'<path d="{f.path}" fill="{color}" stroke="{color}"/>')
        else:
            pts = " ".join(f"{x:.2f},{y:.2f}" for x, y in f.points)
            out.append(f'<polygon points="{pts}" fill="{color}" stroke="{color}"/>')
    out += ["</g>", "</svg>"]
    return "\n".join(out) + "\n"


def _color(style: dict, face: Face) -> str:
    k = style[face.shade]
    base = style["base"]
    if style is STYLES["color"]:
        base = tuple(int(face.color[i : i + 2], 16) for i in (1, 3, 5))
    r, g, b = (round(c * k) for c in base)
    return f"#{r:02x}{g:02x}{b:02x}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out", type=Path)
    parser.add_argument("--style", choices=list(STYLES), default="color")
    args = parser.parse_args()
    for name, prims in SHAPES.items():
        path = args.out / f"{name}.svg"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render(prims, STYLES[args.style]), encoding="utf-8")
    print(f"{len(SHAPES)} 枚を {args.out} に書きました")


if __name__ == "__main__":
    main()
