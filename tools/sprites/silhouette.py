"""地形・施設・住宅のシルエットの絵（SVG）を、箱・円柱・屋根などの形の組み合わせから作る。

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
CAR_COLORS = (RED, WHITE, CONTAINER_BLUE, METAL, YELLOW, COAL)
POLICE_BLUE = "#2f4f96"
GOLD = "#d9a62a"
GOTHIC_STONE = "#d6c08a"  # 議事堂の砂岩
GOTHIC_ROOF = "#55646a"
BAROQUE_YELLOW = "#e8c25c"  # 王宮の黄色い壁
PALACE_ROOF = "#868c94"
GRAVEL = "#e4ddcc"  # 王宮の前の砂利
LEAD = "#5f6d78"  # 鉛ぶきの丸屋根
KREMLIN_RED = "#a8483a"
TOWER_GREEN = "#3d7a5c"
PARTY_STONE = "#d4ccb8"
PLAZA = "#d6d0c2"  # 広場の石畳
STORE_PINK = "#d9668f"  # 百貨店の日よけと看板（小さな地図の色）
STORE_STONE = "#e6dac0"  # 百貨店の石張りの壁
PARTS_BLUE = "#5b7fb0"  # 部品工場の屋根（小さな地図の色）
TIRE = "#2b2c2f"
STRIPES = (STORE_PINK, WHITE)  # 縞の日よけ

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
    bleed: float = 0.0  # 縁を外へにじませる幅（地形の継ぎ目を目立たせないため）
    k: float | None = (
        None  # 明るさ（なければ shade で決まる。岩山の面のように向きが自由な面に使う）
    )


@dataclass
class Prim:
    """形 1 つ。bounds はマスの中の足元の範囲 (u0, v0, u1, v1)、z0 は下の端の高さ。"""

    bounds: tuple[float, float, float, float]
    z0: float
    faces: list[Face]
    tier: int = 1  # 描く段: 0 は地面と水面、1 はふつうの形、2 はクレーンの腕のように上を渡るもの


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
    """段（tier）ごとに、低い段から並べる。"""
    return [
        p
        for t in sorted({p.tier for p in prims})
        for p in _order([q for q in prims if q.tier == t])
    ]


def tiered(tier: int, *prims: Prim) -> list[Prim]:
    """形を描く段に置く。足元が長い形（岸壁の床や、上を渡るクレーンの腕）が、ほかの形と
    奥と手前の順を循環させて崩さないように、先か後にまとめて描く。"""
    for p in prims:
        p.tier = tier
    return list(prims)


def _order(prims: list[Prim]) -> list[Prim]:
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


def hipped(
    u0: float, v0: float, u1: float, v1: float, z0: float, r: float, c: str = ROOF_SLATE
) -> Prim:
    """寄棟屋根: 長い向きに棟が通り、四方へ下がる。"""
    if u1 - u0 >= v1 - v0:
        d, vm = (v1 - v0) / 2, (v0 + v1) / 2
        a, b = iso(u0 + d, vm, z0 + r), iso(u1 - d, vm, z0 + r)
        faces = [
            Face("slope_r", c, [iso(u0, v0, z0), iso(u1, v0, z0), b, a]),
            Face("slope_l", c, [iso(u0, v0, z0), iso(u0, v1, z0), a]),
            Face("slope_l", c, [iso(u0, v1, z0), iso(u1, v1, z0), b, a]),
            Face("slope_r", c, [iso(u1, v0, z0), iso(u1, v1, z0), b]),
        ]
    else:
        d, um = (u1 - u0) / 2, (u0 + u1) / 2
        a, b = iso(um, v0 + d, z0 + r), iso(um, v1 - d, z0 + r)
        faces = [
            Face("slope_l", c, [iso(u0, v0, z0), iso(u0, v1, z0), b, a]),
            Face("slope_r", c, [iso(u0, v0, z0), iso(u1, v0, z0), a]),
            Face("slope_r", c, [iso(u1, v0, z0), iso(u1, v1, z0), b, a]),
            Face("slope_l", c, [iso(u0, v1, z0), iso(u1, v1, z0), b]),
        ]
    return Prim((u0, v0, u1, v1), z0 + 0.1, faces)


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


def sawtooth(
    u0: float,
    v0: float,
    u1: float,
    v1: float,
    z0: float,
    r: float,
    along: str = "u",
    c: str = ROOF_SLATE,
    glass: str = GLASS,
    wall: str = WALL,
) -> list[Prim]:
    """のこぎり屋根の歯 1 つ。along の向きに棟が通り、奥の側の縁が高い。

    手前を向いた斜面と、端の三角の壁が見える。縦の窓は奥を向くので、棟の近くの明かり取り
    （斜面の上の 3 割）を glass の色で描く。
    """
    zt = z0 + r
    zg = z0 + r * 0.65
    if along == "u":  # 歯は v の向きに並ぶ。斜面は手前左を向く
        vg = v0 + (v1 - v0) * 0.35
        roof = [iso(u0, v1, z0), iso(u1, v1, z0), iso(u1, vg, zg), iso(u0, vg, zg)]
        sky = [iso(u0, vg, zg), iso(u1, vg, zg), iso(u1, v0, zt), iso(u0, v0, zt)]
        end = Face("right", wall, [iso(u1, v0, z0), iso(u1, v1, z0), iso(u1, v0, zt)])
        slope = "slope_l"
    else:  # 歯は u の向きに並ぶ。斜面は手前右を向く
        ug = u0 + (u1 - u0) * 0.35
        roof = [iso(u1, v0, z0), iso(u1, v1, z0), iso(ug, v1, zg), iso(ug, v0, zg)]
        sky = [iso(ug, v0, zg), iso(ug, v1, zg), iso(u0, v1, zt), iso(u0, v0, zt)]
        end = Face("left", wall, [iso(u0, v1, z0), iso(u1, v1, z0), iso(u0, v1, zt)])
        slope = "slope_r"
    return [Prim((u0, v0, u1, v1), z0 + 0.1, [Face(slope, c, roof), Face(slope, glass, sky), end])]


def car(u: float, v: float, c: str, z0: float = 0, along: str = "u") -> list[Prim]:
    """乗用車: 車体と、一回り小さい客室。(u, v) は足元の奥の角。"""
    du, dv = (0.09, 0.045) if along == "u" else (0.045, 0.09)
    cu, cv = (0.025, 0.006) if along == "u" else (0.006, 0.025)
    return [
        box(u, v, u + du, v + dv, z0, 2.2, c),
        box(u + cu, v + cv, u + du - cu, v + dv - cv, z0 + 2.2, 1.6, GLASS2, c),
    ]


def patrol_car(u: float, v: float, z0: float = 0) -> list[Prim]:
    """パトロールカー: 下が黒・上が白の車体、客室、屋根の赤い灯（u の向きに長い）。"""
    return [
        box(u, v, u + 0.13, v + 0.06, z0, 1.4, COAL),
        box(u, v, u + 0.13, v + 0.06, z0 + 1.4, 1.2, WHITE),
        box(u + 0.035, v + 0.008, u + 0.095, v + 0.052, z0 + 2.6, 1.8, GLASS2, WHITE),
        box(u + 0.055, v + 0.012, u + 0.075, v + 0.048, z0 + 4.4, 0.9, RED),
    ]


def windows(
    u0: float,
    v0: float,
    u1: float,
    v1: float,
    zs: list[float],
    h: float,
    counts: tuple[int, int],
    w: float = 0.045,
    c: str = "#6f8aa3",
) -> list[Prim]:
    """建物 (u0, v0)〜(u1, v1) の見える 2 面（左の側面と右の側面）に並べた窓。

    zs は窓の下の端の高さ（階ごと）、h は窓の高さ、
    counts は左の側面と右の側面の 1 階あたりの窓の数。
    """
    prims = []
    n_left, n_right = counts
    for z in zs:
        for i in range(n_left):
            uc = u0 + (i + 0.5) * (u1 - u0) / n_left
            prims.append(box(uc - w / 2, v1, uc + w / 2, v1 + 0.005, z, h, c))
        for i in range(n_right):
            vc = v0 + (i + 0.5) * (v1 - v0) / n_right
            prims.append(box(u1, vc - w / 2, u1 + 0.005, vc + w / 2, z, h, c))
    return prims


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


def derrick(uc: float, vc: float, half: float, h: float, c: str = IRON) -> list[Prim]:
    """油井のやぐら: 4 本の脚が上ですぼまる鉄骨の塔と、途中の横の梁。"""

    def leg(su: int, sv: int, t: float) -> tuple[float, float, float]:
        k = half * (1 - 0.8 * t)
        return (uc + su * k, vc + sv * k, 1 + (h - 1) * t)

    corners = ((-1, -1), (1, -1), (1, 1), (-1, 1))
    prims = [strut(leg(su, sv, 0), leg(su, sv, 1), 1.0, c) for su, sv in corners]
    for t in (0.3, 0.6):
        for (su, sv), (tu, tv) in zip(corners, corners[1:] + corners[:1], strict=True):
            prims.append(strut(leg(su, sv, t), leg(tu, tv, t), 0.7, c))
    prims.append(box(uc - half * 0.3, vc - half * 0.3, uc + half * 0.3, vc + half * 0.3, h, 2, c))
    return prims


def pumpjack(u: float, v: float, c: str = COAL, weight: str = RED) -> list[Prim]:
    """首を振るポンプ（ポンプジャック）。u の向きに長く、u の大きい側に馬の頭の形の先。"""
    vm = v + 0.035
    return [
        box(u, v, u + 0.36, v + 0.07, 1, 1.5, IRON),
        box(u + 0.01, v + 0.01, u + 0.07, v + 0.06, 2.5, 7, weight),
        strut((u + 0.16, v + 0.005, 2.5), (u + 0.17, vm, 15), 1.0, c),
        strut((u + 0.16, v + 0.065, 2.5), (u + 0.17, vm, 15), 1.0, c),
        strut((u + 0.27, vm, 2.5), (u + 0.18, vm, 14), 0.8, c),
        strut((u + 0.02, vm, 13), (u + 0.32, vm, 16.5), 2.0, c),
        box(u + 0.3, v + 0.015, u + 0.36, v + 0.055, 10.5, 8, c),
        cylinder(u + 0.34, vm, 0.012, 2.5, 8, c=METAL),
    ]


def column(uc: float, vc: float, r: float, h: float, c: str = METAL) -> list[Prim]:
    """製油所の蒸留塔: 細長い円柱と、途中の足場の輪。"""
    rings = [cylinder(uc, vc, r * 1.45, 1 + h * t, 1.2, c=YELLOW) for t in (0.35, 0.7)]
    return [cylinder(uc, vc, r, 1, h, cap="dome", c=c), *rings]


def flame(uc: float, vc: float, z0: float, h: float, c: str = "#f08a24") -> Prim:
    """フレアスタックの先の炎（しずくの形）。"""
    x, y = iso(uc, vc, z0)
    pts = [(x, y), (x - 1.6, y - h * 0.35), (x - 0.3, y - h), (x + 1.6, y - h * 0.4)]
    return Prim((uc - 0.01, vc - 0.01, uc + 0.01, vc + 0.01), z0, [Face("top", c, pts)])


def cow(u: float, v: float, c: str, top: str | None = None) -> list[Prim]:
    """牛: 胴と頭の箱（u の向きを向く）。"""
    return [
        box(u, v, u + 0.09, v + 0.045, 1, 3, c, top),
        box(u + 0.09, v + 0.008, u + 0.12, v + 0.037, 2.5, 2.5, c, top),
    ]


def fence(u0: float, v0: float, u1: float, v1: float, z0: float = 1, c: str = WHITE) -> list[Prim]:
    """四角い囲いの柵: 角と途中の杭、横木 1 本。"""
    prims = []
    n = 6
    for i in range(n + 1):
        t = i / n
        u, v = u0 + (u1 - u0) * t, v0 + (v1 - v0) * t
        for pu, pv in ((u, v0), (u, v1), (u0, v), (u1, v)):
            prims.append(box(pu - 0.01, pv - 0.01, pu + 0.01, pv + 0.01, z0, 4, c))
    for a, b in (
        ((u0, v0), (u1, v0)),
        ((u0, v1), (u1, v1)),
        ((u0, v0), (u0, v1)),
        ((u1, v0), (u1, v1)),
    ):
        prims.append(strut((*a, z0 + 3), (*b, z0 + 3), 0.8, c))
    return prims


def dome(
    uc: float, vc: float, r: float, z0: float, h: float, c: str = COPPER, onion: bool = False
) -> Prim:
    """丸屋根: 足元の半径 r、高さ h ピクセル。

    onion で玉ねぎ形（足元より太くふくらみ、先がとがる）。
    """
    cx, yb, rx, ry = _ellipse(uc, vc, r, z0)
    top = yb - h
    faces = []
    for sign, shade, sweep in ((-1, "slope_l", 1), (1, "slope_r", 0)):
        x = cx + sign * rx
        if onion:
            c1, c2 = (cx + sign * rx * 1.6, yb - h * 0.4), (cx + sign * rx * 0.5, yb - h * 0.7)
        else:
            c1, c2 = (x, yb - h * 0.55), (cx + sign * rx * 0.55, top)
        d = _d("M", x, yb, "C", *c1, *c2, cx, top, "L", cx, yb + ry)
        d += " " + _d("A", rx, ry, 0, 0, sweep, x, yb, "Z")
        faces.append(Face(shade, c, path=d, ymin=top))
    return Prim((uc - r, vc - r, uc + r, vc + r), z0 + 0.1, faces)


def spire(uc: float, vc: float, r: float, z0: float, h: float, c: str = ROOF_SLATE) -> Prim:
    """細くとがった塔の先（円すい）。"""
    return cylinder(uc, vc, r, z0, h, r_top=0.002, c=c)


def flag(u: float, v: float, z0: float, h: float, c: str = RED, length: float = 0.07) -> list[Prim]:
    """旗: 細い旗ざおと、u の向きにはためく布（左の側面が見える）。"""
    return [
        cylinder(u, v, 0.005, z0, h, c=METAL),
        box(u + 0.005, v - 0.003, u + 0.005 + length, v + 0.003, z0 + h - 4.5, 4, c),
    ]


def statue(u: float, v: float, z0: float, h: float, c: str = "#7a6640") -> list[Prim]:
    """人の像: 胴（上が細い円柱）と頭、上げた右腕。h は像の高さ（台座は別）。"""
    return [
        cylinder(u, v, 0.03, z0, h * 0.84, r_top=0.021, c=c),
        box(u + 0.016, v - 0.006, u + 0.032, v + 0.006, z0 + h * 0.55, h * 0.45, c),
        crown(u, v, z0 + h * 0.82, h * 0.08, c),
    ]


def crenels(u0: float, v0: float, u1: float, v1: float, z0: float, c: str) -> list[Prim]:
    """城壁の上の凸凹（狭間）。(u0, v0)〜(u1, v1) の細長い壁の上に、長い向きに並べる。"""
    along_u = (u1 - u0) >= (v1 - v0)
    a0, a1 = (u0, u1) if along_u else (v0, v1)
    n = max(1, round((a1 - a0) / 0.06))
    step = (a1 - a0) / n
    prims = []
    for i in range(n):
        a = a0 + i * step + step * 0.2
        if along_u:
            prims.append(box(a, v0, a + step * 0.5, v1, z0, 1.6, c))
        else:
            prims.append(box(u0, a, u1, a + step * 0.5, z0, 1.6, c))
    return prims


def banners(
    u0: float, u1: float, v: float, z0: float, h: float, n: int, c: str = RED
) -> list[Prim]:
    """建物の正面（左の側面、v の位置）に縦に垂らした布を n 枚。"""
    step = (u1 - u0) / n
    return [
        box(u0 + (i + 0.3) * step, v, u0 + (i + 0.7) * step, v + 0.005, z0, h, c) for i in range(n)
    ]


def ground(c: str) -> Prim:
    """地形の上面: マスのひし形いっぱいの平らな面。縁は隣のマスへ少しにじませる。"""
    pts = [iso(0, 0, 0), iso(1, 0, 0), iso(1, 1, 0), iso(0, 1, 0)]
    return Prim((0, 0, 1, 1), -2, [Face("top", c, pts, bleed=0.6)])


def patch(points: list[tuple[float, float]], c: str, z: float = 0) -> Prim:
    """地面の上の模様（草むら・砂・波など）や、高さ z の水面。points はマスの中の (u, v) の並び。"""
    us, vs = [u for u, _ in points], [v for _, v in points]
    pts = [iso(u, v, z) for u, v in points]
    return Prim((min(us), min(vs), max(us), max(vs)), -1 if z == 0 else 0, [Face("top", c, pts)])


def wave(u: float, v: float, length: float, c: str, w: float = 0.022) -> Prim:
    """波の筋: u の向きの細い帯で、両端を細くする。"""
    return patch(
        [(u, v), (u + length * 0.3, v - w / 2), (u + length, v), (u + length * 0.7, v + w / 2)], c
    )


def crown(uc: float, vc: float, z0: float, r: float, c: str = "#4f9a42") -> Prim:
    """広葉樹の丸い葉のかたまり（半径 r ピクセルの円を左右で塗り分ける）。下の端が (uc, vc, z0)。"""
    x, yb = iso(uc, vc, z0)
    cy = yb - r
    faces = []
    for shade, sweep in (("slope_l", 0), ("slope_r", 1)):
        d = _d("M", x, cy - r, "A", r, r, 0, 0, sweep, x, cy + r, "Z")
        faces.append(Face(shade, c, path=d, ymin=cy - r))
    ru = r / (TILE_W / 2 * math.sqrt(2))
    return Prim((uc - ru, vc - ru, uc + ru, vc + ru), z0 + 0.3, faces)


def broadleaf(uc: float, vc: float, r: float, h: float) -> list[Prim]:
    """広葉樹: 短い幹と丸い葉。r は葉の半径（ピクセル）、h は木の高さ（ピクセル）。"""
    return [cylinder(uc, vc, 0.015, 0, h - 2 * r + 1, c=TRUNK), crown(uc, vc, h - 2 * r, r)]


# 山の帯の色（足元から上へ）。雪をかぶるのは高い峰だけ
MOUNTAIN_ROCK = ["#a29279", "#aa9c88", "#b0a493"]
# 緑の山: 裾は森、中ほどは草地、頂の近くだけ岩
MOUNTAIN_GREEN = ["#4f8c3e", "#5d9a4a", "#86a062", "#a89e88"]
GREEN_PROFILE = [(1.0, 0.0), (0.76, 0.24), (0.5, 0.54), (0.22, 0.83)]

# 岩山の面の明るさ: 0.6 + 0.47 × (光の向き・面の向き)。光は左上から来るので、
# 左を向いた斜面が明るく、右を向いた斜面が暗い（平らな面 1.0、急な斜面で左 0.94・右 0.67 ほど）
LIGHT = (-0.45, 0.25, 0.86)
VIEW = (0.612, 0.612, 0.5)  # 見る人のほうの向き（u, v, z）


def _world(u: float, v: float, z: float) -> tuple[float, float, float]:
    """マスの中の位置と高さ → 長さをそろえた3次元の位置（マスの一辺がおよそ 45 ピクセル）。"""
    return u * 45.25, v * 45.25, z / 0.866


def _normal(pts: list[tuple[float, float, float]]) -> tuple[float, float, float]:
    a, b, c = (_world(*p) for p in pts[:3])
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    n = (uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx)
    length = math.sqrt(sum(x * x for x in n)) or 1.0
    return n[0] / length, n[1] / length, n[2] / length


def massif(
    rings: list[list[tuple[float, float, float]]],
    apex: tuple[float, float, float],
    bands: list[str],
) -> Prim:
    """岩山: 足元から上へ重ねた輪と頂をつないだ多面体。

    rings は下から順の輪で、どれも同じ数の点 (u, v, z) を、上から見て同じ向きに並べる
    （ring で作る）。bands は輪と輪のあいだの帯の色で、最後は頂までの色（雪など）。
    面ごとに向きで明るさを変え、見えない向きの面は描かない。
    """
    tris = []
    n = len(rings[0])
    for k, color in enumerate(bands):
        lo = rings[k]
        for i in range(n):
            j = (i + 1) % n
            if k + 1 < len(rings):
                hi = rings[k + 1]
                tris += [((lo[i], lo[j], hi[j]), color), ((lo[i], hi[j], hi[i]), color)]
            else:
                tris.append(((lo[i], lo[j], apex), color))
    cu = sum(p[0] for p in rings[0]) / n
    cv = sum(p[1] for p in rings[0]) / n
    faces = []
    for pts, color in tris:
        nu, nv, nz = _normal(list(pts))
        mu = sum(p[0] for p in pts) / 3 - cu
        mv = sum(p[1] for p in pts) / 3 - cv
        if nu * mu + nv * mv + nz * 0.3 < 0:  # 外を向くようにそろえる
            nu, nv, nz = -nu, -nv, -nz
        if nu * VIEW[0] + nv * VIEW[1] + nz * VIEW[2] <= 0:
            continue
        k = min(1.0, 0.6 + 0.47 * max(0.0, nu * LIGHT[0] + nv * LIGHT[1] + nz * LIGHT[2]))
        depth = sum(sum(a * b for a, b in zip(_world(*p), VIEW, strict=True)) for p in pts)
        faces.append((depth, Face("top", color, [iso(*p) for p in pts], k=round(k, 3))))
    faces.sort(key=lambda f: f[0])
    us, vs = [p[0] for p in rings[0]], [p[1] for p in rings[0]]
    z0 = min(p[2] for p in rings[0])
    return Prim((min(us), min(vs), max(us), max(vs)), z0, [f for _, f in faces])


# 峰の輪の大きさと高さ（足元を 1 とした割合）。裾は広くゆるく、頂に近いほど急にする
PEAK_PROFILE = [(1.0, 0.0), (0.58, 0.4), (0.25, 0.76)]


def peak(
    uc: float,
    vc: float,
    h: float,
    r: float,
    shape: list[float],
    bands: list[str],
    lean: tuple[float, float] = (0.0, 0.0),
    turn: float = 0.0,
    z0: float = 0.0,
    profile: list[tuple[float, float]] | None = None,
) -> Prim:
    """山の峰 1 つ: 足元の半径 r（マスの一辺に対する割合）、高さ h ピクセル。

    shape は角ごとの出っ張り（1 がふつう）で、その数が輪の角の数になる。bands は下から順の帯の色で、
    数は PEAK_PROFILE の輪の数まで（少なければ下の輪だけ使う）。頂は lean の向きへ寄せる。
    z0 は足元の高さ（水面から出る岩など）。profile は輪の大きさと高さ（なければ PEAK_PROFILE）。
    """
    rings = []
    for k, (rk, zk) in enumerate((profile or PEAK_PROFILE)[: len(bands)]):
        t = 1 - rk
        rr = [r * rk * s for s in shape]
        zs = [z0 + h * zk * (0.85 + 0.15 * s) for s in shape]
        rings.append(ring(uc + lean[0] * t, vc + lean[1] * t, rr, zs, turn + 0.3 * k))
    return massif(rings, (uc + lean[0], vc + lean[1], z0 + h), bands)


def ring(
    uc: float, vc: float, radii: list[float], z: float | list[float] = 0.0, turn: float = 0.0
) -> list[tuple[float, float, float]]:
    """(uc, vc) のまわりに、上から見て時計回りに並べた点 (u, v, z)。radii の数だけ点を置く。"""
    n = len(radii)
    zs = z if isinstance(z, list) else [z] * n
    pts = []
    for i, r in enumerate(radii):
        a = turn + 2 * math.pi * i / n
        pts.append((uc + r * math.cos(a), vc + r * math.sin(a), zs[i]))
    return pts


def arch_dam(
    cu: float, cv: float, r: float, a0: float, a1: float, h: float, n: int = 10
) -> list[Prim]:
    """アーチダム: 手前（下流）の (cu, cv) を中心に半径 r の弧を、角度 a0〜a1（度。0 が u の向き、
    90 が v の向き）で描く高さ h の壁と、天端の道、真ん中の放流の白い筋。
    壁は奥の湖のほうへふくらむ。
    """

    def at(a: float, rr: float, z: float) -> tuple[float, float]:
        t = math.radians(a)
        return iso(cu + rr * math.cos(t), cv + rr * math.sin(t), z)

    step = (a1 - a0) / n
    faces = []
    for i in range(n):
        b0, b1 = a0 + i * step, a0 + (i + 1) * step
        # 壁は中心を向く。明るさは u の向き（右の側面）0.66 から v の向き（左の側面）0.82 へ
        k = 0.66 + 0.16 * min(1.0, max(0.0, ((b0 + b1) / 2 - 180) / 90))
        wall = [at(b0, r, 0), at(b1, r, 0), at(b1, r, h), at(b0, r, h)]
        faces.append(Face("top", CONCRETE, wall, k=round(k, 3)))
    crest = [at(a0 + i * step, r, h) for i in range(n + 1)]
    crest += [at(a1 - i * step, r + 0.04, h) for i in range(n + 1)]
    faces.append(Face("top", PAVEMENT, crest))
    mid = (a0 + a1) / 2
    spill = [at(mid - 3, r - 0.004, 0), at(mid + 3, r - 0.004, 0)]
    spill += [at(mid + 3, r - 0.004, h - 1), at(mid - 3, r - 0.004, h - 1)]
    faces.append(Face("top", "#dbe9f1", spill, k=0.95))
    us = [
        cu + rr * math.cos(math.radians(a0 + i * step))
        for i in range(n + 1)
        for rr in (r, r + 0.04)
    ]
    vs = [
        cv + rr * math.sin(math.radians(a0 + i * step))
        for i in range(n + 1)
        for rr in (r, r + 0.04)
    ]
    return [Prim((min(us), min(vs), max(us), max(vs)), 0, faces)]


def cliff(a: tuple[float, float], b: tuple[float, float], h: float, c: str = ROCK) -> Prim:
    """a から b への線に立つ高さ h の崖（手前を向く面）。左の側面ほど明るく、右の側面ほど暗い。"""
    du, dv = b[0] - a[0], b[1] - a[1]
    nu, nv = dv, -du  # 線の右手（手前）の向き
    length = math.hypot(nu, nv) or 1.0
    k = 0.66 + 0.16 * max(0.0, nv / length) if nu >= 0 else 0.82
    pts = [iso(*a, 0), iso(*b, 0), iso(*b, h), iso(*a, h)]
    us, vs = (a[0], b[0]), (a[1], b[1])
    return Prim((min(us), min(vs), max(us), max(vs)), 0, [Face("top", c, pts, k=round(k, 3))])


def crag(u0: float, v0: float, u1: float, v1: float, h: float, c: str = ROCK) -> Prim:
    """岩山の峰: 四角すい（手前の 2 面だけ見える）。"""
    return hip(u0, v0, u1, v1, 0, h, c)


# 水力発電所のアーチダム（手前の (0.96, 0.96) を中心に半径 0.58 の弧）と、その奥のダム湖
DAM = (0.96, 0.96, 0.58, 186, 264)
LAKE = [
    *[
        (0.96 + 0.6 * math.cos(math.radians(a)), 0.96 + 0.6 * math.sin(math.radians(a)))
        for a in range(186, 265, 13)
    ],
    (0.86, 0.0),
    (0.0, 0.0),
    (0.0, 0.86),
]

# --- 形の一覧 ----------------------------------------------------------------

SHAPES: dict[str, list[Prim]] = {
    # 地形。上面のひし形と、そこから上に出る木や岩だけを描く（側面の崖はコードが色で描く）。
    # 地の色は theme.yaml の terrain の top と同じにして、小さな地図や隣のマスとそろえる
    "terrain/sea": [
        ground("#2f6db5"),
        wave(0.18, 0.3, 0.2, "#4580c2"),
        wave(0.55, 0.22, 0.16, "#4580c2"),
        wave(0.42, 0.58, 0.22, "#4580c2"),
        wave(0.2, 0.78, 0.14, "#4580c2"),
        wave(0.7, 0.72, 0.16, "#4580c2"),
    ],
    "terrain/shallows": [
        # 透けて見える砂の底（明るい斑）と、さざ波
        ground("#5b9bd5"),
        patch([(0.24, 0.3), (0.42, 0.2), (0.58, 0.26), (0.5, 0.38), (0.3, 0.42)], "#63a2d9"),
        patch([(0.52, 0.66), (0.7, 0.58), (0.82, 0.66), (0.7, 0.78), (0.56, 0.76)], "#63a2d9"),
        wave(0.16, 0.62, 0.18, "#7ab3e1"),
        wave(0.62, 0.38, 0.16, "#7ab3e1"),
        wave(0.36, 0.84, 0.12, "#7ab3e1"),
    ],
    "terrain/plain": [
        ground("#8cc163"),
        patch([(0.2, 0.3), (0.34, 0.24), (0.44, 0.34), (0.32, 0.42)], "#93c76b"),
        patch([(0.58, 0.62), (0.72, 0.56), (0.8, 0.68), (0.66, 0.74)], "#93c76b"),
        patch([(0.62, 0.22), (0.7, 0.2), (0.74, 0.28), (0.66, 0.3)], "#84b85c"),
        patch([(0.24, 0.7), (0.32, 0.66), (0.36, 0.74), (0.28, 0.78)], "#84b85c"),
    ],
    "terrain/forest": [
        ground("#3f8a3a"),
        patch([(0.3, 0.4), (0.5, 0.32), (0.66, 0.5), (0.46, 0.62)], "#38803a"),
        *tree(0.22, 0.24, 0.09, 18),
        *broadleaf(0.55, 0.18, 4.5, 13),
        *tree(0.42, 0.42, 0.1, 20),
        *broadleaf(0.2, 0.56, 4.5, 13),
        *tree(0.75, 0.42, 0.09, 17),
        *broadleaf(0.58, 0.66, 5, 14),
        *tree(0.36, 0.76, 0.09, 17),
        *tree(0.8, 0.78, 0.08, 15),
    ],
    "terrain/mountain": [
        # 裾が森の緑の山。高い峰と、右手前へ下る低い峰。頂の近くだけ岩が出る。
        # 地面は平地と同じ高さで、隣の平地とつながる草の色
        ground("#8cc163"),
        patch([(0.62, 0.84), (0.8, 0.78), (0.9, 0.9), (0.74, 0.96)], "#84b85c"),
        peak(
            0.44,
            0.42,
            36,
            0.44,
            [1, 0.9, 1, 0.86, 1.04, 0.9, 1, 0.88, 1.02, 0.92],
            MOUNTAIN_GREEN,
            (-0.02, -0.03),
            0.2,
            profile=GREEN_PROFILE,
        ),
        peak(
            0.74,
            0.66,
            19,
            0.24,
            [1, 0.88, 1.04, 0.9, 1, 0.92, 0.96, 0.9],
            MOUNTAIN_GREEN[:3],
            (0.0, -0.01),
            0.5,
            profile=GREEN_PROFILE,
        ),
        peak(
            0.24,
            0.78,
            12,
            0.18,
            [1, 0.9, 1.04, 0.88, 1, 0.92, 0.96],
            MOUNTAIN_GREEN[:3],
            (0.0, 0.0),
            0.1,
            profile=GREEN_PROFILE,
        ),
        *tree(0.9, 0.42, 0.06, 11),
        *tree(0.56, 0.9, 0.06, 10),
    ],
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
        # 岩山のふもとの立て坑のやぐら、選鉱の建屋、ぼた山
        peak(
            0.74, 0.24, 15, 0.22, [1, 0.7, 1.05, 0.75, 1, 0.8, 0.95], MOUNTAIN_ROCK, (0.02, -0.02)
        ),
        *[box(u, v, u + 0.06, v + 0.06, 0, 34, IRON) for u in (0.18, 0.36) for v in (0.18, 0.36)],
        box(0.15, 0.15, 0.45, 0.45, 34, 4, RUST),
        *house(0.5, 0.45, 0.88, 0.85, 12, 7, "u", WALL, ROOF_SLATE),
        cone(0.25, 0.72, 0.13, 9, c=COAL),
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
        # 岩山の谷をせき止めたアーチダム。奥に崖に囲まれたダム湖、ダムの正面に放流の白い筋、
        # 足元に発電所と川
        patch(LAKE, WATER, 17),
        cliff((0.0, 0.86), LAKE[0], 17, "#8e7f6a"),
        cliff(LAKE[len(LAKE) - 4], (0.86, 0.0), 17, "#8e7f6a"),
        peak(
            0.2, 0.2, 9, 0.2, [1, 0.7, 1.05, 0.75, 1, 0.8, 0.9], MOUNTAIN_ROCK, (0.0, 0.0), 0.2, 17
        ),
        *arch_dam(*DAM, 20),
        peak(
            0.88,
            0.36,
            22,
            0.16,
            [1, 0.7, 1.05, 0.75, 1, 0.8, 0.95],
            MOUNTAIN_ROCK,
            (0.03, -0.04),
            0.5,
            0.5,
        ),
        peak(
            0.36,
            0.88,
            20,
            0.16,
            [1, 0.75, 1.05, 0.7, 1, 0.8],
            MOUNTAIN_ROCK,
            (-0.04, 0.03),
            0.1,
            0.5,
        ),
        patch([(0.58, 0.62), (0.62, 0.58), (1.0, 0.94), (0.94, 1.0)], WATER),
        box(0.8, 0.62, 0.94, 0.76, 0, 8, WALL, ROOF_SLATE),
        *[box(u, 0.86, u + 0.025, 0.885, 0, 8, METAL) for u in (0.62, 0.72)],
        box(0.61, 0.86, 0.755, 0.885, 8, 1, METAL),
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
        # 石の基壇と正面の石段、正面に同じ間隔で並ぶ 6 本の円柱と、それが支える梁
        # （エンタブラチュア）、正面に三角の破風を見せる切妻の屋根（ギリシャの神殿風）。
        # 柱のあいだに扉
        box(0.14, 0.12, 0.88, 0.8, 0, 3, STONE, "#ddd5c3"),
        box(0.18, 0.8, 0.84, 0.86, 0, 2, STONE, "#ddd5c3"),
        box(0.18, 0.86, 0.84, 0.9, 0, 1, STONE, "#ddd5c3"),
        box(0.18, 0.14, 0.84, 0.6, 3, 20, SAND, "#c9b680"),
        box(0.42, 0.6, 0.58, 0.605, 3, 10, "#5c4a36"),
        *[cylinder(0.22 + i * 0.116, 0.72, 0.022, 3, 16, c=WHITE) for i in range(6)],
        box(0.17, 0.6, 0.85, 0.765, 19, 4, "#e6d9ae"),
        box(0.15, 0.12, 0.87, 0.785, 23, 1.5, "#efe6c4"),
        gable(0.15, 0.12, 0.87, 0.785, 24.5, 9, "v", "#b9a676", "#efe6c4"),
    ],
    # フェーズ3-1 の商社（港と造船所は下の「海沿いの施設」）
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
    # フェーズ3-2 の油井・製油所・アルミ精錬所・繊維工場・牧場・自動車工場
    "facility/oil_well": [
        # 砂利の敷地、鉄骨のやぐら、首を振るポンプ、原油のタンク 2 基
        box(0.08, 0.08, 0.92, 0.92, 0, 1, "#a99d84"),
        *derrick(0.3, 0.3, 0.15, 46),
        box(0.18, 0.18, 0.42, 0.42, 1, 3, IRON, "#6b655e"),
        *pumpjack(0.5, 0.56),
        *[cylinder(u, v, 0.09, 1, 10, c="#d7d2c6") for u, v in ((0.2, 0.62), (0.22, 0.82))],
    ],
    "facility/refinery": [
        # 蒸留塔 3 本、加工の装置とそこへの配管、先に炎の出るフレアスタック、浮き屋根のタンク 3 基
        box(0.06, 0.06, 0.94, 0.94, 0, 1, CONCRETE, PAVEMENT),
        *column(0.2, 0.24, 0.05, 50),
        *column(0.36, 0.15, 0.04, 40),
        *column(0.15, 0.44, 0.035, 32),
        box(0.46, 0.12, 0.72, 0.4, 1, 16, "#8c8f6a", DARK),
        box(0.5, 0.16, 0.68, 0.36, 17, 6, "#8c8f6a", DARK),
        box(0.24, 0.42, 0.5, 0.46, 9, 2, "#8c8f6a"),
        cylinder(0.86, 0.12, 0.018, 1, 48, c=METAL),
        cylinder(0.86, 0.12, 0.022, 49, 4, c=RED),
        flame(0.86, 0.12, 53, 9),
        *[
            cylinder(u, v, 0.12, 1, 11, c=WHITE, cap_c="#cfcac0")
            for u, v in ((0.3, 0.76), (0.62, 0.78), (0.8, 0.52))
        ],
    ],
    "facility/aluminium_smelter": [
        # 電解炉の長い建屋 2 棟（屋根に換気の越屋根）、アルミナのサイロ 2 基、変電所、地金の山
        box(0.06, 0.06, 0.94, 0.94, 0, 1, CONCRETE, PAVEMENT),
        *[
            prim
            for v in (0.1, 0.34)
            for prim in (
                box(0.08, v, 0.92, v + 0.18, 1, 14, "#cfd5da", ROOF_STEEL),
                box(0.08, v + 0.06, 0.92, v + 0.12, 15, 4, "#e4e8eb", "#8f9aa4"),
            )
        ],
        *[cylinder(u, 0.74, 0.075, 1, 28, c="#e2e5e8") for u in (0.18, 0.36)],
        *[box(u, 0.64, u + 0.08, 0.72, 1, 6, DARK) for u in (0.58, 0.74)],
        *[box(u, 0.84, u + 0.02, 0.86, 1, 22, METAL) for u in (0.56, 0.86)],
        box(0.56, 0.84, 0.88, 0.86, 21, 2, METAL),
        *[
            box(u, 0.84, u + 0.1, 0.92, z, 2, "#e8ecef", WHITE)
            for u in (0.12, 0.26)
            for z in (1, 3)
        ],
    ],
    "facility/textile_mill": [
        # のこぎり屋根の織物の建屋（左の妻壁がぎざぎざで、棟の近くに明かり取り）、レンガの煙突と
        # ボイラー室、綿の俵
        cylinder(0.16, 0.16, 0.04, 0, 42, c=BRICK),
        box(0.3, 0.08, 0.62, 0.26, 0, 14, BRICK, ROOF_SLATE),
        *[box(u, 0.12, u + 0.08, 0.22, 0, 4, WHITE) for u in (0.7, 0.8)],
        box(0.75, 0.12, 0.83, 0.22, 4, 4, WHITE),
        box(0.1, 0.32, 0.9, 0.9, 0, 11, "#d8aab5"),
        *[
            prim
            for u in (0.1, 0.3, 0.5, 0.7)
            for prim in sawtooth(
                u, 0.32, u + 0.2, 0.9, 11, 8, "v", ROOF_SLATE, "#d4e4ee", "#d8aab5"
            )
        ],
    ],
    "facility/ranch": [
        # 柵で囲った牧草地、赤い牛舎、干し草のロール、牛
        box(0.06, 0.06, 0.94, 0.94, 0, 1, "#a8c46a"),
        *house(0.12, 0.12, 0.42, 0.4, 10, 7, "v", "#b4483a", ROOF_SLATE),
        *[
            cylinder(u, v, 0.035, 1, 3.5, c=CROP)
            for u, v in ((0.56, 0.16), (0.64, 0.16), (0.6, 0.24))
        ],
        *cow(0.5, 0.42, "#7a4a2b"),
        *cow(0.24, 0.58, WHITE, "#2d2b29"),
        *cow(0.6, 0.66, "#2d2b29"),
        *cow(0.36, 0.76, "#7a4a2b"),
        *fence(0.08, 0.08, 0.92, 0.92),
    ],
    "facility/automobile_plant": [
        # 組立の大きな建屋（屋根に明かり取りの帯）、塗装の建屋、事務所、出来上がった車の列
        box(0.06, 0.06, 0.94, 0.94, 0, 1, CONCRETE, PAVEMENT),
        box(0.08, 0.08, 0.92, 0.54, 1, 18, WALL, "#4a6fa5"),
        *[box(0.3, v, 0.9, v + 0.04, 19, 1.5, GLASS) for v in (0.14, 0.26, 0.38)],
        box(0.08, 0.08, 0.28, 0.3, 19, 9, WALL, "#4a6fa5"),
        box(0.1, 0.62, 0.3, 0.9, 1, 12, GLASS, WHITE),
        *[
            prim
            for i, u in enumerate((0.4, 0.52, 0.64, 0.76))
            for j, v in enumerate((0.62, 0.72, 0.82))
            for prim in car(u, v, CAR_COLORS[(i * 2 + j) % len(CAR_COLORS)], 1)
        ],
    ],
    # 回7 後半の自動車部品工場
    "facility/auto_parts_plant": [
        # のこぎり屋根の部品の建屋、荷積みの台と箱の荷台のトラック、積んだタイヤの山、部品の箱
        box(0.06, 0.06, 0.94, 0.94, 0, 1, CONCRETE, PAVEMENT),
        box(0.1, 0.1, 0.62, 0.9, 1, 12, WALL),
        *[
            prim
            for v in (0.1, 0.3, 0.5, 0.7)
            for prim in sawtooth(0.1, v, 0.62, v + 0.2, 13, 7, "u", PARTS_BLUE, GLASS, WALL)
        ],
        box(0.62, 0.14, 0.7, 0.5, 1, 3, CONCRETE, PAVEMENT),
        box(0.72, 0.16, 0.86, 0.4, 1, 9, WHITE, "#dfe3e8"),
        box(0.86, 0.2, 0.92, 0.36, 1, 6, PARTS_BLUE, GLASS2),
        *[
            cylinder(u, v, 0.04, 1 + 1.8 * k, 1.5, c=TIRE)
            for u, v, n in ((0.72, 0.58, 4), (0.82, 0.58, 3), (0.72, 0.7, 3), (0.82, 0.7, 2))
            for k in range(n)
        ],
        *[box(u, 0.8, u + 0.08, 0.88, 1, 4, WOOD) for u in (0.68, 0.78)],
        box(0.68, 0.8, 0.76, 0.88, 5, 4, WOOD),
    ],
    # フェーズ3-4 の警察署
    "facility/police": [
        # 3 階建ての白い庁舎に青い帯と金の記章、入口のひさしに赤い灯、屋上の無線のアンテナ、
        # 右の駐車場にパトロールカー 3 台、前の広場に木
        box(0.08, 0.08, 0.62, 0.92, 0, 0.6, CONCRETE, PAVEMENT),
        box(0.62, 0.08, 0.92, 0.92, 0, 0.6, "#6d7177"),
        box(0.14, 0.12, 0.58, 0.56, 0.6, 1.6, POLICE_BLUE),
        box(0.14, 0.12, 0.58, 0.56, 2.2, 19.8, "#eceae4", "#cfd3d8"),
        *windows(0.14, 0.12, 0.58, 0.56, [4, 10.5, 17], 3.2, (5, 5)),
        box(0.135, 0.115, 0.585, 0.565, 21.5, 3, POLICE_BLUE, "#cfd3d8"),
        box(0.35, 0.565, 0.38, 0.57, 22, 2, YELLOW),
        box(0.36, 0.16, 0.52, 0.3, 24.5, 4, "#eceae4", "#cfd3d8"),
        cylinder(0.22, 0.22, 0.01, 24.5, 12, c=METAL),
        box(0.18, 0.215, 0.26, 0.225, 32, 0.8, METAL),
        cylinder(0.22, 0.22, 0.013, 36.5, 1.4, c=RED),
        box(0.3, 0.56, 0.42, 0.565, 0.6, 5, "#4a5866"),
        *[cylinder(u, 0.66, 0.008, 0.6, 6, c=WHITE) for u in (0.28, 0.44)],
        box(0.26, 0.56, 0.46, 0.68, 6.6, 1.2, POLICE_BLUE, WHITE),
        cylinder(0.36, 0.675, 0.016, 7.8, 2, cap="dome", c=RED),
        *patrol_car(0.72, 0.34, 0.6),
        *patrol_car(0.72, 0.5, 0.6),
        *patrol_car(0.72, 0.66, 0.6),
        *broadleaf(0.16, 0.8, 3.5, 10),
        *broadleaf(0.52, 0.84, 3.5, 10),
    ],
    # 回7 の百貨店
    "facility/department_store": [
        # 石張りの 5 階建ての大きな店、1 階のショーウィンドウと縞の日よけ、上の階の窓の列、
        # 屋上の塔屋と、文字のない看板の枠、前の歩道の植え込み
        box(0.06, 0.06, 0.94, 0.94, 0, 0.6, CONCRETE, PAVEMENT),
        box(0.12, 0.12, 0.78, 0.78, 0.6, 30, STORE_STONE, "#cfc8b8"),
        box(0.12, 0.78, 0.78, 0.785, 1.2, 5.5, GLASS),
        box(0.78, 0.12, 0.785, 0.78, 1.2, 5.5, GLASS),
        *[
            box(0.12 + i * 0.066, 0.785, 0.186 + i * 0.066, 0.84, 7, 1.4, STRIPES[i % 2])
            for i in range(10)
        ],
        *[
            box(0.785, 0.12 + i * 0.066, 0.84, 0.186 + i * 0.066, 7, 1.4, STRIPES[i % 2])
            for i in range(10)
        ],
        *windows(0.12, 0.12, 0.78, 0.78, [11, 16, 21, 26], 2.6, (6, 6), c="#7f9bb3"),
        box(0.115, 0.115, 0.785, 0.785, 30.6, 1.6, "#f1e9d6", "#cfc8b8"),
        box(0.18, 0.18, 0.4, 0.4, 32.2, 5, STORE_STONE, "#cfc8b8"),
        *[box(u, 0.6, u + 0.014, 0.614, 32.2, 4, METAL) for u in (0.44, 0.6, 0.72)],
        box(0.42, 0.6, 0.74, 0.62, 36.2, 7, STORE_PINK),
        *broadleaf(0.88, 0.3, 2.8, 8),
        *broadleaf(0.3, 0.9, 2.8, 8),
    ],
    # 首都の建物。政治の仕組みの建物（議事堂・首相官邸・党本部）と元首の建物（王宮・大統領府）の
    # 組み合わせごとに 1 枚。実在の建物の雰囲気だけを借り、そっくりには描かない
    "capital/parliament-palace": [
        # 民主制 × 君主制: 縦の筋の多い石の議事堂、端に時計塔、反対の端に旗を立てた四角い塔
        box(0.06, 0.06, 0.94, 0.94, 0, 0.6, CONCRETE, PAVEMENT),
        box(0.1, 0.7, 0.62, 0.92, 0.6, 0.3, "#7fb35a"),
        box(0.14, 0.28, 0.84, 0.6, 0.6, 14, GOTHIC_STONE, GOTHIC_STONE),
        gable(0.14, 0.28, 0.84, 0.6, 14.6, 6, "u", GOTHIC_ROOF, GOTHIC_STONE),
        *[
            box(u, 0.6, u + 0.014, 0.606, 0.6, 15.5, "#c4a96c")
            for u in (0.18, 0.26, 0.34, 0.42, 0.5, 0.58, 0.66)
        ],
        box(0.46, 0.41, 0.52, 0.47, 14.6, 9, GOTHIC_STONE),
        spire(0.49, 0.44, 0.03, 23.6, 9, GOTHIC_ROOF),
        box(0.08, 0.22, 0.24, 0.38, 0.6, 36, GOTHIC_STONE, "#c4a96c"),
        *[spire(u, v, 0.014, 36.6, 6, GOTHIC_STONE) for u in (0.09, 0.23) for v in (0.23, 0.37)],
        *flag(0.16, 0.3, 36.6, 10, RED),
        box(0.72, 0.6, 0.84, 0.72, 0.6, 34, GOTHIC_STONE),
        box(0.75, 0.72, 0.81, 0.725, 27, 5, "#f3ecd4"),
        box(0.84, 0.63, 0.845, 0.69, 27, 5, "#f3ecd4"),
        box(0.73, 0.61, 0.83, 0.71, 34.6, 3, GOTHIC_STONE),
        hip(0.73, 0.61, 0.83, 0.71, 37.6, 12, GOTHIC_ROOF),
        spire(0.78, 0.66, 0.008, 49.6, 4, YELLOW),
    ],
    "capital/parliament-presidential_office": [
        # 民主制 × 共和制: 白い議事堂の真ん中に、列柱の胴の上の高い丸屋根。左右に翼、正面に芝生
        box(0.06, 0.06, 0.94, 0.94, 0, 0.6, CONCRETE, PAVEMENT),
        box(0.12, 0.72, 0.88, 0.94, 0.6, 0.3, "#7fb35a"),
        box(0.08, 0.3, 0.36, 0.56, 0.6, 12, WHITE, "#dcd8ce"),
        box(0.64, 0.3, 0.92, 0.56, 0.6, 12, WHITE, "#dcd8ce"),
        box(0.36, 0.24, 0.64, 0.62, 0.6, 15, WHITE, "#dcd8ce"),
        *[cylinder(u, 0.67, 0.013, 0.6, 12, c=WHITE) for u in (0.4, 0.455, 0.51, 0.565, 0.62)],
        box(0.37, 0.62, 0.63, 0.7, 12.6, 2.4, WHITE),
        gable(0.37, 0.62, 0.63, 0.7, 15, 3.5, "v", "#e3dfd6", WHITE),
        cylinder(0.5, 0.42, 0.12, 15.6, 7, c=WHITE),
        cylinder(0.5, 0.42, 0.124, 17.2, 3.6, c="#bcb9b1"),
        cylinder(0.5, 0.42, 0.1, 22.6, 4, c=WHITE),
        dome(0.5, 0.42, 0.1, 26.6, 10, WHITE),
        cylinder(0.5, 0.42, 0.022, 36, 3.5, cap="dome", c=WHITE),
        spire(0.5, 0.42, 0.008, 41, 3, "#7a6640"),
    ],
    "capital/official_residence-palace": [
        # 権威主義 × 君主制: 黄色い壁の長い王宮。真ん中の張り出しに白い付け柱と三角の破風、
        # 前に生け垣で区切った幾何学の庭と噴水
        box(0.04, 0.04, 0.96, 0.96, 0, 0.6, GRAVEL, GRAVEL),
        box(0.1, 0.52, 0.9, 0.94, 0.6, 0.3, "#7fb35a"),
        *[
            box(u0, v0, u0 + 0.32, v0 + 0.16, 0.9, 1.2, "#4f8a3c", "#5f9a48")
            for u0 in (0.14, 0.54)
            for v0 in (0.55, 0.75)
        ],
        box(0.47, 0.52, 0.53, 0.94, 0.9, 0.1, GRAVEL),
        box(0.1, 0.71, 0.9, 0.75, 0.9, 0.1, GRAVEL),
        cylinder(0.5, 0.73, 0.06, 1, 1.5, c=WHITE, cap_c=WATER),
        box(0.06, 0.1, 0.94, 0.44, 0.6, 13, BAROQUE_YELLOW),
        *windows(0.06, 0.1, 0.36, 0.44, [3, 8.5], 3.4, (5, 0), 0.024, "#5f7f73"),
        *windows(0.64, 0.1, 0.94, 0.44, [3, 8.5], 3.4, (5, 5), 0.024, "#5f7f73"),
        hipped(0.05, 0.09, 0.95, 0.45, 13.6, 5, PALACE_ROOF),
        box(0.36, 0.08, 0.64, 0.48, 0.6, 16, BAROQUE_YELLOW),
        *[box(u, 0.48, u + 0.012, 0.485, 0.6, 16, "#f6efd8") for u in (0.37, 0.43, 0.55, 0.62)],
        *windows(0.36, 0.08, 0.64, 0.48, [3, 9.5], 4.2, (3, 0), 0.03, "#5f7f73"),
        gable(0.36, 0.08, 0.64, 0.48, 16.6, 5, "v", PALACE_ROOF, "#f6efd8"),
        box(0.42, 0.48, 0.58, 0.53, 0.6, 1.6, "#d8d2c4"),
        *flag(0.5, 0.28, 21.6, 8, RED),
    ],
    "capital/official_residence-presidential_office": [
        # 権威主義 × 共和制: 狭間のある赤れんがの城壁と緑のとがり屋根の塔、
        # 正面の門の塔の先に赤い星、中に黄色い大統領府と、金の玉ねぎ形の丸屋根の聖堂
        box(0.04, 0.04, 0.96, 0.96, 0, 0.6, CONCRETE, PAVEMENT),
        box(0.08, 0.08, 0.92, 0.12, 0.6, 8, KREMLIN_RED),
        box(0.08, 0.12, 0.12, 0.88, 0.6, 8, KREMLIN_RED),
        box(0.2, 0.2, 0.58, 0.5, 0.6, 13, "#e3c76a", "#5f8f6a"),
        box(0.2, 0.5, 0.58, 0.505, 3, 6, WHITE),
        dome(0.39, 0.35, 0.07, 13.6, 6, "#5f8f6a"),
        box(0.62, 0.22, 0.8, 0.42, 0.6, 12, WHITE, "#d8d4ca"),
        cylinder(0.71, 0.32, 0.04, 12.6, 5, c=WHITE),
        dome(0.71, 0.32, 0.045, 17.6, 9, GOLD, onion=True),
        *[dome(u, v, 0.03, 12.6, 6, GOLD, onion=True) for u, v in ((0.65, 0.25), (0.77, 0.39))],
        box(0.12, 0.88, 0.92, 0.92, 0.6, 8, KREMLIN_RED),
        box(0.88, 0.12, 0.92, 0.88, 0.6, 8, KREMLIN_RED),
        *crenels(0.12, 0.88, 0.92, 0.92, 8.6, KREMLIN_RED),
        *crenels(0.88, 0.12, 0.92, 0.88, 8.6, KREMLIN_RED),
        *[
            prim
            for u0, v0, h in (
                (0.05, 0.05, 13),
                (0.05, 0.83, 13),
                (0.83, 0.05, 13),
                (0.83, 0.83, 15),
            )
            for prim in (
                box(u0, v0, u0 + 0.12, v0 + 0.12, 0.6, h, KREMLIN_RED),
                hip(u0, v0, u0 + 0.12, v0 + 0.12, h + 0.6, 9, TOWER_GREEN),
            )
        ],
        box(0.44, 0.85, 0.56, 0.95, 0.6, 20, KREMLIN_RED),
        box(0.47, 0.95, 0.53, 0.955, 15, 4, "#f3ecd4"),
        box(0.46, 0.865, 0.54, 0.935, 20.6, 6, KREMLIN_RED),
        hip(0.46, 0.865, 0.54, 0.935, 26.6, 11, TOWER_GREEN),
        box(0.493, 0.893, 0.507, 0.907, 37.4, 3, RED),
    ],
    "capital/party_headquarters-palace": [
        # 全体主義 × 君主制: 奥に鉛ぶきの低い丸屋根と赤い旗、両端に寄棟の棟を持つ王宮、
        # 正面に赤い垂れ幕、王宮へ続く赤い道と、広場の真ん中の大きな金の像、広場のまわりに赤い旗
        box(0.04, 0.04, 0.96, 0.96, 0, 0.6, PLAZA, PLAZA),
        box(0.46, 0.38, 0.56, 0.58, 0.6, 0.3, "#b03a2e"),
        box(0.22, 0.12, 0.78, 0.38, 0.6, 14, "#e8dcc0"),
        gable(0.22, 0.12, 0.78, 0.38, 14.6, 5, "u", ROOF_SLATE, "#e8dcc0"),
        *banners(0.24, 0.76, 0.38, 3, 10, 5),
        *[
            prim
            for u0 in (0.08, 0.78)
            for prim in (
                box(u0, 0.1, u0 + 0.14, 0.4, 0.6, 18, "#e8dcc0", ROOF_SLATE),
                hipped(u0 - 0.005, 0.095, u0 + 0.145, 0.405, 18.6, 6, LEAD),
            )
        ],
        box(0.4, 0.16, 0.6, 0.36, 14.6, 11, "#e8dcc0"),
        cylinder(0.5, 0.26, 0.085, 25.6, 3, c="#e8dcc0"),
        dome(0.5, 0.26, 0.085, 28.6, 7, LEAD),
        cylinder(0.5, 0.26, 0.018, 34.6, 3, cap="dome", c="#e8dcc0", cap_c=LEAD),
        *flag(0.5, 0.26, 38, 9, RED),
        box(0.4, 0.58, 0.62, 0.78, 0.6, 6, "#bdb5a5", "#cfc8b9"),
        *statue(0.51, 0.68, 6.6, 20, GOLD),
        *[
            prim
            for u, v in ((0.1, 0.6), (0.14, 0.8), (0.86, 0.56), (0.9, 0.76))
            for prim in flag(u, v, 0.6, 16)
        ],
    ],
    "capital/party_headquarters-presidential_office": [
        # 全体主義 × 共和制: 段々に細くなる高い党本部の塔の先に赤い星、正面に赤い垂れ幕、
        # 広場に大きな像と赤い旗の列
        box(0.04, 0.04, 0.96, 0.96, 0, 0.6, PLAZA, PLAZA),
        box(0.62, 0.14, 0.9, 0.4, 0.6, 10, PARTY_STONE, "#bdb5a5"),
        box(0.1, 0.1, 0.62, 0.44, 0.6, 14, PARTY_STONE, "#bdb5a5"),
        *banners(0.12, 0.6, 0.44, 3, 10, 4),
        box(0.2, 0.16, 0.52, 0.38, 14.6, 12, PARTY_STONE, "#bdb5a5"),
        box(0.28, 0.2, 0.44, 0.34, 26.6, 10, PARTY_STONE, "#bdb5a5"),
        box(0.32, 0.23, 0.4, 0.31, 36.6, 6, PARTY_STONE),
        spire(0.36, 0.27, 0.03, 42.6, 12, GOLD),
        box(0.35, 0.26, 0.37, 0.28, 54.4, 3, RED),
        box(0.6, 0.6, 0.76, 0.76, 0.6, 5, "#bdb5a5", "#cfc8b9"),
        *statue(0.68, 0.68, 5.6, 17, "#6f6a5e"),
        *[prim for u in (0.12, 0.24, 0.36) for prim in flag(u, 0.86, 0.6, 15)],
        *[prim for v in (0.5, 0.64) for prim in flag(0.9, v, 0.6, 15)],
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


# 海沿いの施設（漁港・港・造船所）。マスの縁の 1 つが海に面していて、そこを岸壁と水面にする。
# 水面は海と同じ高さ（平地の 1 段下）まで下げ、海のマスとつながって見えるようにする。
# 形は海が右下（u = 1 の縁、マスの x + 1）にある向きで書き、Side で 4 つの向きに回す
SEA_Z = -8  # 海の水面の高さ（平地の高さ 1 段 = theme.yaml の step_height 8 ピクセル下）
SEA_TOP = "#2f6db5"  # 海の上面（theme.yaml の terrain の sea）
SEA_WAVE = "#4580c2"
PLAIN_TOP = "#8cc163"  # 平地の上面と側面（theme.yaml の terrain の plain）
PLAIN_LEFT = "#6f9a4e"
PLAIN_RIGHT = "#5c8040"
QUAY = "#b3ada2"  # 岸壁の石
HULL_DARK = "#3a4654"

# 海のある向き（画面の上で）。se: 右下（x + 1）、sw: 左下（y + 1）、
# ne: 右上（y - 1）、nw: 左上（x - 1）。
# se の絵は名前に向きを付けず、ほかは「facility/port@sw」のように付ける（renderer.js が選ぶ）
SIDES = ("se", "sw", "ne", "nw")


class Side:
    """海のある向き。海が右下にある形の位置 (u, v) を、その向きの位置に移す。"""

    def __init__(self, name: str) -> None:
        self.name = name
        self.swap = name in ("sw", "ne")  # u と v が入れ替わる
        self.front = name in ("se", "sw")  # 海が手前（水面と岸壁が見える）
        # 水の上のもの（船・桟橋の脚）の下の端。奥の海は陸の縁に隠れて見えないので、
        # 水面を陸の高さに描く
        self.water_z = SEA_Z if self.front else 0.2

    def pt(self, u: float, v: float) -> tuple[float, float]:
        return {"se": (u, v), "sw": (v, u), "nw": (1 - u, v), "ne": (v, 1 - u)}[self.name]

    def rect(self, u0: float, v0: float, u1: float, v1: float) -> tuple[float, float, float, float]:
        a, b = self.pt(u0, v0), self.pt(u1, v1)
        return min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])

    def along(self, a: str) -> str:
        return {"u": "v", "v": "u"}[a] if self.swap else a

    def box(self, u0: float, v0: float, u1: float, v1: float, *a: object, **k: object) -> Prim:
        return box(*self.rect(u0, v0, u1, v1), *a, **k)

    def gable(
        self,
        u0: float,
        v0: float,
        u1: float,
        v1: float,
        z0: float,
        r: float,
        along: str,
        *a: object,
    ) -> Prim:
        return gable(*self.rect(u0, v0, u1, v1), z0, r, self.along(along), *a)

    def house(
        self,
        u0: float,
        v0: float,
        u1: float,
        v1: float,
        h: float,
        r: float,
        along: str,
        wall: str,
        roof: str,
    ) -> list[Prim]:
        return [
            self.box(u0, v0, u1, v1, 0, h, wall),
            self.gable(u0, v0, u1, v1, h, r, along, roof, wall),
        ]

    def cylinder(self, uc: float, vc: float, *a: object, **k: object) -> Prim:
        return cylinder(*self.pt(uc, vc), *a, **k)

    def strut(
        self, a: tuple[float, float, float], b: tuple[float, float, float], *x: object
    ) -> Prim:
        return strut((*self.pt(a[0], a[1]), a[2]), (*self.pt(b[0], b[1]), b[2]), *x)

    def prism(
        self, points: list[tuple[float, float]], z0: float, h: float, c: str, top: str | None = None
    ) -> Prim:
        return prism([self.pt(u, v) for u, v in points], z0, h, c, top)

    def ship(
        self,
        u0: float,
        v0: float,
        u1: float,
        v1: float,
        z0: float,
        h: float,
        bow: float,
        c: str,
        top: str | None = None,
        along: str = "u",
    ) -> Prim:
        """船体（の一段）。along の向きに長く、u1（v1）の側がとがった船首。"""
        if along == "u":
            vm = (v0 + v1) / 2
            pts = [(u0, v0), (u1 - bow, v0), (u1, vm), (u1 - bow, v1), (u0, v1)]
        else:
            um = (u0 + u1) / 2
            pts = [(u0, v0), (u1, v0), (u1, v1 - bow), (um, v1), (u0, v1 - bow)]
        return self.prism(pts, z0, h, c, top)


def prism(
    points: list[tuple[float, float]],
    z0: float,
    h: float,
    c: str,
    top: str | None = None,
    side_color: object = None,
) -> Prim:
    """足元が凸の多角形 points の柱。見えるのは上面と、手前（u か v の増える向き）を向いた側面。

    side_color は側面ごとの色を返す関数 (a, b, (nu, nv)) -> (色, 明るさ)。
    なければ c を面の向きで陰影する。
    """
    zt = z0 + h
    cu = sum(u for u, _ in points) / len(points)
    cv = sum(v for _, v in points) / len(points)
    faces = []
    for a, b in zip(points, points[1:] + points[:1], strict=True):
        nu, nv = b[1] - a[1], a[0] - b[0]
        if nu * ((a[0] + b[0]) / 2 - cu) + nv * ((a[1] + b[1]) / 2 - cv) < 0:
            nu, nv = -nu, -nv
        if nu + nv <= 1e-9:
            continue
        if abs(nu) < 1e-9:
            shade = "left"
        elif abs(nv) < 1e-9:
            shade = "right"
        else:
            shade = "slope_l" if nv > nu else "slope_r"
        pts = [iso(*a, z0), iso(*b, z0), iso(*b, zt), iso(*a, zt)]
        if side_color:
            color, k = side_color(a, b, (nu, nv))
            faces.append(Face(shade, color, pts, k=k))
        else:
            faces.append(Face(shade, c, pts))
    faces.append(Face("top", top or c, [iso(u, v, zt) for u, v in points]))
    us, vs = [u for u, _ in points], [v for _, v in points]
    return Prim((min(us), min(vs), max(us), max(vs)), z0, faces)


def basin(s: Side, w: float, a: float = 0.08, b: float = 0.92) -> list[Prim]:
    """海側の縁から幅 w の水面と岸壁。

    海が手前なら、水面を海の高さまで下げ、陸の側に岸壁の石の壁を立てる。縁の両端（v < a と v > b）は
    陸のまま残し、その切り口も描く（崖の色は theme.yaml の平地の側面）。海が奥なら、陸の縁に隠れて
    岸壁は見えないので、水面だけを陸の高さに描く。
    """
    u0 = 1 - w
    if not s.front:
        pts = [s.pt(u, v) for u, v in ((u0, 0), (1, 0), (1, 1), (u0, 1))]
        us, vs = [u for u, _ in pts], [v for _, v in pts]
        surface = [iso(u, v, 0.2) for u, v in pts]
        return tiered(
            0,
            Prim((min(us), min(vs), max(us), max(vs)), 0.1, [Face("top", SEA_TOP, surface, k=1.0)]),
        )

    def edge_color(
        p: tuple[float, float], q: tuple[float, float], n: tuple[float, float]
    ) -> tuple[str, float | None]:
        outer = (p[0] == q[0] == 1) or (p[1] == q[1] == 1)
        if outer:
            return (PLAIN_LEFT if n[1] > n[0] else PLAIN_RIGHT), 1.0
        return QUAY, None

    pts = [s.pt(u, v) for u, v in ((u0, a), (1, a), (1, b), (u0, b))]
    us, vs = [u for u, _ in pts], [v for _, v in pts]
    water = Prim(
        (min(us), min(vs), max(us), max(vs)),
        SEA_Z - 0.01,
        [Face("top", SEA_TOP, [iso(u, v, SEA_Z) for u, v in pts], k=1.0)],
    )
    waves = [
        Prim(
            water.bounds,
            SEA_Z - 0.005,
            [Face("top", SEA_WAVE, [iso(*s.pt(u, v), SEA_Z) for u, v in q], k=1.0)],
        )
        for q in (
            [
                (u0 + w * 0.55, a + 0.1),
                (u0 + w * 0.62, a + 0.14),
                (u0 + w * 0.55, a + 0.2),
                (u0 + w * 0.5, a + 0.14),
            ],
            [
                (u0 + w * 0.7, b - 0.22),
                (u0 + w * 0.77, b - 0.17),
                (u0 + w * 0.7, b - 0.12),
                (u0 + w * 0.65, b - 0.17),
            ],
        )
    ]
    wall_pts = [s.pt(u0, a), s.pt(u0, b)]
    (wu0, wv0), (wu1, wv1) = wall_pts
    wall_face = [iso(wu0, wv0, SEA_Z), iso(wu1, wv1, SEA_Z), iso(wu1, wv1, 0), iso(wu0, wv0, 0)]
    wall = Prim(
        (min(wu0, wu1), min(wv0, wv1), max(wu0, wu1), max(wv0, wv1)),
        SEA_Z,
        [Face("right" if not s.swap else "left", QUAY, wall_face)],
    )
    corners = [
        prism(
            [s.pt(u, v) for u, v in ((u0, v0), (1, v0), (1, v1), (u0, v1))],
            SEA_Z,
            -SEA_Z,
            PLAIN_TOP,
            PLAIN_TOP,
            edge_color,
        )
        for v0, v1 in ((0, a), (b, 1))
    ]
    for c in corners:
        c.faces[-1].k = 1.0
    return [*tiered(0, water, *waves, wall), *corners]


def fishing_port(s: Side) -> list[Prim]:
    # 青い屋根の漁協の建物と魚の市場、岸壁、水の上に突き出た木の桟橋 2 本と、つないだ漁船、
    # 荷揚げの小さなクレーン
    wz = s.water_z
    return [
        *basin(s, 0.36),
        *tiered(0, s.box(0.06, 0.06, 0.64, 0.94, 0, 0.6, CONCRETE, PAVEMENT)),
        *s.house(0.1, 0.12, 0.4, 0.5, 10, 7, "v", WALL, "#4f8fb8"),
        s.box(0.12, 0.58, 0.4, 0.86, 0.6, 7, WALL, "#4fa3c7"),
        *[
            prim
            for v in (0.26, 0.66)
            for prim in (
                *[s.cylinder(u, v + 0.04, 0.012, wz, -wz + 0.2, c=TRUNK) for u in (0.78, 0.92)],
                s.box(0.64, v, 0.96, v + 0.08, 0.2, 0.8, WOOD),
            )
        ],
        s.ship(0.7, 0.4, 0.94, 0.52, wz, 2.5, 0.07, WHITE, "#d8dde0"),
        s.box(0.74, 0.42, 0.8, 0.5, wz + 2.5, 3, "#4f8fb8", WHITE),
        s.ship(0.68, 0.78, 0.9, 0.88, wz, 2.2, 0.06, "#c8473a", "#e8e2d6"),
        s.box(0.72, 0.8, 0.77, 0.86, wz + 2.2, 2.6, WHITE),
        s.box(0.58, 0.16, 0.62, 0.2, 0.6, 24, YELLOW),
        *tiered(2, s.box(0.58, 0.16, 0.82, 0.2, 24.6, 2, YELLOW)),
    ]


def port(s: Side) -> list[Prim]:
    # 岸壁（コンクリートの床）、横付けしたコンテナ船、赤いコンテナクレーン、積んだコンテナ、
    # 倉庫、係船柱
    wz = s.water_z
    return [
        *basin(s, 0.26),
        *tiered(0, s.box(0.06, 0.06, 0.74, 0.94, 0, 4, CONCRETE, PAVEMENT)),
        s.box(0.1, 0.1, 0.36, 0.38, 4, 9, WALL),
        s.gable(0.1, 0.1, 0.36, 0.38, 13, 5, "u", ROOF_STEEL, WALL),
        *[
            s.box(u, v, u + 0.22, v + 0.08, 4 + 4 * k, 4, col)
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
        # 船: 黒い船体に赤い船底の線、甲板にコンテナ、後ろに白い船橋
        s.ship(0.78, 0.14, 0.96, 0.9, wz, 2, 0.14, RED, along="v"),
        s.ship(0.78, 0.14, 0.96, 0.9, wz + 2, 5, 0.14, HULL_DARK, "#7c8794", along="v"),
        # 船尾の船橋: 白い居住区の上に窓の帯と張り出した操舵室、後ろに煙突
        s.box(0.8, 0.15, 0.94, 0.25, wz + 7, 6, WHITE),
        s.box(0.795, 0.145, 0.945, 0.255, wz + 13, 2, "#3e5a73", WHITE),
        s.box(0.79, 0.17, 0.95, 0.23, wz + 15, 1, WHITE),
        s.box(0.84, 0.15, 0.9, 0.19, wz + 13, 5, RED, COAL),
        *[
            s.box(0.81, v, 0.93, v + 0.1, wz + 7 + 3.5 * k, 3.5, col)
            for v, k, col in (
                (0.3, 0, CONTAINER_BLUE),
                (0.42, 0, RED),
                (0.42, 1, YELLOW),
                (0.54, 0, COPPER),
                (0.66, 0, CONTAINER_BLUE),
                (0.66, 1, RED),
            )
        ],
        # クレーン: 4 本の脚の上に、船の上まで張り出す腕。腕の根元に機械室、柱から腕の両端へ控え
        *[s.box(u, v, u + 0.04, v + 0.04, 4, 24, RED) for u in (0.48, 0.66) for v in (0.34, 0.5)],
        *tiered(
            2,
            s.box(0.28, 0.36, 1.0, 0.52, 28, 3, RED),
            s.box(0.3, 0.38, 0.44, 0.5, 31, 6, WHITE),
            s.box(0.57, 0.42, 0.61, 0.46, 31, 11, RED),
            s.strut((0.59, 0.44, 42), (0.98, 0.44, 31)),
            s.strut((0.59, 0.44, 42), (0.44, 0.44, 37)),
        ),
        *[s.cylinder(0.71, v, 0.02, 4, 2.5, c=IRON) for v in (0.14, 0.42, 0.66, 0.88)],
    ]


def shipyard(s: Side) -> list[Prim]:
    # 地面を掘り下げた乾ドックで組み立て中の船体（赤い船底・とがった船首）、海との間の扉船、
    # それをまたぐ黄色い門形クレーンが吊った船体の部品、奥に加工の建屋、手前に鋼材
    return [
        *basin(s, 0.18),
        s.box(0.06, 0.06, 0.82, 0.36, -6, 9, CONCRETE, PAVEMENT),
        s.box(0.06, 0.64, 0.82, 0.94, 0, 3, CONCRETE, PAVEMENT),
        s.box(0.06, 0.36, 0.14, 0.64, -6, 9, CONCRETE, PAVEMENT),
        s.box(0.14, 0.36, 0.79, 0.64, -6, 0.3, "#8d8a84"),
        s.box(
            0.79,
            0.36,
            0.82,
            0.64,
            SEA_Z if s.front else -6,
            (-SEA_Z if s.front else 6) + 2,
            "#6d7177",
        ),
        s.box(0.08, 0.08, 0.38, 0.32, 3, 10, WALL),
        s.gable(0.08, 0.08, 0.38, 0.32, 13, 5, "u", ROOF_STEEL, WALL),
        s.ship(0.2, 0.4, 0.78, 0.6, -5.7, 8, 0.16, RED),
        s.ship(0.2, 0.4, 0.78, 0.6, 2.3, 5.5, 0.16, METAL, "#c3c8cc"),
        s.box(0.22, 0.43, 0.34, 0.57, 7.8, 6, WHITE),
        *[s.box(u, 0.12, u + 0.1, 0.3, 3, 2 + k, ROOF_STEEL) for k, u in enumerate((0.6, 0.7))],
        *[s.box(0.5, v, 0.56, v + 0.06, 3, 31, YELLOW) for v in (0.12, 0.82)],
        s.strut((0.53, 0.5, 34), (0.53, 0.5, 25), 0.6, IRON),
        s.box(0.47, 0.44, 0.59, 0.56, 19, 6, METAL, "#c3c8cc"),
        *tiered(
            2,
            s.box(0.48, 0.08, 0.58, 0.92, 34, 6, YELLOW),
            s.box(0.47, 0.44, 0.59, 0.56, 40, 4, WHITE),
        ),
    ]


for _name, _shape in (("fishing_port", fishing_port), ("port", port), ("shipyard", shipyard)):
    for _side in SIDES:
        _suffix = "" if _side == "se" else f"@{_side}"
        SHAPES[f"facility/{_name}{_suffix}"] = _shape(Side(_side))


# --- 記念碑（フェーズ4-1。設計書 タブ 2 の PO-47）--------------------------------------------

BLACK_STONE = "#2f3236"
GRANITE = "#8a8d91"
BRONZE = "#7a6640"
OBELISK = "#efeae0"
STEEL_SILVER = "#b8bec4"
MONUMENT_PLAZA = "#d9d4c8"


def _plaza(r: float = 0.86) -> Prim:
    """記念碑の足元の石畳（どの碑も共通）。"""
    return box(1 - r, 1 - r, r, r, 0, 0.6, CONCRETE, MONUMENT_PLAZA)


def _tower_legs(base: float, top: float, z_top: float, c: str, w: float = 1.4) -> list[Prim]:
    """細くすぼまる 4 本の脚（展望塔）。足元の角 (base, 1 - base) から頂点 (0.5, 0.5, z_top) へ。"""
    corners = [(base, base), (base, 1 - base), (1 - base, base), (1 - base, 1 - base)]
    return [strut((u, v, 0.6), (0.5, 0.5, z_top), w, c) for u, v in corners]


def _figure(u: float, v: float, z0: float, h: float, c: str, arm: bool = True) -> list[Prim]:
    """記念碑の人の像: 太めの胴と丸い頭、上げた右腕。statue() より大きく、遠くからも人に見える。"""
    prims = [
        cylinder(u, v, 0.06, z0, h * 0.62, r_top=0.04, c=c),
        crown(u, v, z0 + h * 0.6, h * 0.19, c),
    ]
    if arm:
        prims.append(box(u + 0.03, v - 0.012, u + 0.055, v + 0.012, z0 + h * 0.4, h * 0.6, c))
    return prims


SHAPES.update(
    {
        # モノリス: 何も刻まれていない黒い石板（箱庭の原点）。低い台の上に立つ
        "facility/monument_monolith": [
            _plaza(),
            box(0.3, 0.3, 0.7, 0.7, 0.6, 1.6, GRANITE, "#a4a7ab"),
            box(0.43, 0.36, 0.55, 0.64, 2.2, 28, BLACK_STONE, "#4a4e53"),
        ],
        # 自作の記念碑: 低い台に、絵を入れる額（白い板に木の枠）が立つ
        "facility/monument_custom": [
            _plaza(),
            box(0.28, 0.28, 0.72, 0.72, 0.6, 3, GRANITE, "#a4a7ab"),
            box(0.3, 0.46, 0.7, 0.54, 3.6, 22, WOOD),
            box(0.33, 0.54, 0.67, 0.545, 5.6, 18, WHITE),
            *flag(0.7, 0.62, 3.6, 8, YELLOW, 0.06),
        ],
        # 建国記念碑: 白いオベリスク（四角い柱が細くなり、先がとがる）
        "facility/monument_founding": [
            _plaza(),
            box(0.3, 0.3, 0.7, 0.7, 0.6, 3.4, GRANITE, "#a4a7ab"),
            box(0.38, 0.38, 0.62, 0.62, 4, 4, OBELISK),
            box(0.41, 0.41, 0.59, 0.59, 8, 20, OBELISK),
            hip(0.41, 0.41, 0.59, 0.59, 28, 7, "#d9d2c0"),
            *flag(0.64, 0.66, 4, 8, RED, 0.06),
        ],
        # 平和記念碑: 白い円柱の塔と、先の緑の丸屋根、足元の木
        "facility/monument_peace": [
            _plaza(),
            cylinder(0.5, 0.5, 0.26, 0.6, 3.2, c=GRANITE),
            cylinder(0.5, 0.5, 0.12, 3.8, 24, r_top=0.085, c=WHITE),
            cylinder(0.5, 0.5, 0.1, 27.8, 2, c=STONE),
            dome(0.5, 0.5, 0.1, 29.8, 8, TOWER_GREEN),
            *broadleaf(0.2, 0.78, 3.4, 9),
            *broadleaf(0.8, 0.8, 3.4, 9),
        ],
        # 戦いの碑: 砂岩の門（二本の脚と、それをまたぐ梁。凱旋門の雰囲気だけ）
        "facility/monument_battle": [
            _plaza(),
            box(0.2, 0.34, 0.4, 0.66, 0.6, 22, SAND),
            box(0.6, 0.34, 0.8, 0.66, 0.6, 22, SAND),
            box(0.2, 0.34, 0.8, 0.66, 22.6, 6, SAND),
            box(0.18, 0.32, 0.82, 0.68, 28.6, 1.6, CREAM),
            box(0.4, 0.5, 0.6, 0.66, 0.6, 1, COAL),
            flame(0.5, 0.58, 1.6, 4),
        ],
        # 慰霊碑: 低い黒御影の台と、白い花、小さな灯
        "facility/monument_cenotaph": [
            _plaza(),
            box(0.22, 0.32, 0.78, 0.68, 0.6, 5, BLACK_STONE, "#4a4e53"),
            box(0.3, 0.38, 0.7, 0.62, 5.6, 2.4, GRANITE, "#a4a7ab"),
            *[
                box(u, 0.24, u + 0.05, 0.29, 0.6, 2.2, WHITE, "#f4f1ea")
                for u in (0.28, 0.4, 0.52, 0.64)
            ],
            *[box(u, 0.71, u + 0.05, 0.76, 0.6, 2.2, YELLOW) for u in (0.34, 0.5, 0.66)],
            cylinder(0.5, 0.5, 0.05, 8, 3, c=IRON),
            flame(0.5, 0.5, 11, 5),
        ],
        # 元首の像: 高い台座の上の青銅の像
        "facility/monument_leader": [
            _plaza(),
            box(0.3, 0.3, 0.7, 0.7, 0.6, 3, GRANITE, "#a4a7ab"),
            box(0.38, 0.38, 0.62, 0.62, 3.6, 13, CONCRETE, "#d7d2c6"),
            box(0.36, 0.36, 0.64, 0.64, 16.6, 1.6, GRANITE, "#a4a7ab"),
            *_figure(0.5, 0.5, 18.2, 20, BRONZE),
        ],
        # 労働者の像: 台座の上に、金づちを掲げる像と、前の赤い旗
        "facility/monument_worker": [
            _plaza(),
            box(0.28, 0.32, 0.72, 0.68, 0.6, 4, "#8c4a3c", "#a8604a"),
            box(0.36, 0.38, 0.64, 0.62, 4.6, 9, CONCRETE, "#d7d2c6"),
            *_figure(0.4, 0.42, 13.6, 18, STEEL_SILVER),
            *_figure(0.6, 0.6, 13.6, 15, STEEL_SILVER, arm=False),
            box(0.3, 0.6, 0.7, 0.64, 4.6, 1.4, RED),
            *flag(0.7, 0.3, 4.6, 12, RED, 0.08),
        ],
        # 展望塔: 赤と白の鉄骨の塔。二つの展望台と、先のアンテナ
        "facility/monument_tower": [
            _plaza(),
            *_tower_legs(0.28, 0.5, 44, RED, 2.2),
            *tiered(
                1,
                box(0.34, 0.34, 0.66, 0.66, 12, 3.4, WHITE, "#cfd3d8"),
                box(0.4, 0.4, 0.6, 0.6, 24, 3, WHITE, "#cfd3d8"),
            ),
            cylinder(0.5, 0.5, 0.012, 38, 12, c=RED),
            cylinder(0.5, 0.5, 0.005, 50, 6, c=METAL),
        ],
        # 帳簿の碑: 開いた帳簿のように罫線の入った低い石板（真ん中の赤い線が借方と貸方を分ける）
        "facility/monument_ledger": [
            _plaza(),
            box(0.18, 0.3, 0.82, 0.7, 0.6, 2.4, GRANITE, "#a4a7ab"),
            box(0.22, 0.38, 0.78, 0.62, 3, 13, CREAM, "#d9cfb4"),
            *[box(0.26, 0.62, 0.74, 0.625, 5.5 + 2.6 * k, 0.6, IRON) for k in range(4)],
            box(0.495, 0.62, 0.505, 0.625, 4, 11, RED),
            box(0.22, 0.38, 0.78, 0.62, 16, 1, GRANITE, "#a4a7ab"),
        ],
        # 検証の碑: 灰色の敷石の台座に濃灰色の角柱、それを囲む銀灰色のコの字の測定枠
        "facility/monument_verification": [
            box(0.12, 0.12, 0.88, 0.88, 0, 1.4, "#9a9da1", "#b4b7bb"),
            box(0.4, 0.4, 0.6, 0.6, 1.4, 24, "#3d4044", "#55595e"),
            box(0.24, 0.24, 0.29, 0.29, 1.4, 30, STEEL_SILVER),
            box(0.24, 0.71, 0.29, 0.76, 1.4, 30, STEEL_SILVER),
            box(0.24, 0.24, 0.29, 0.76, 31.4, 2.4, STEEL_SILVER),
            *[
                box(0.29, 0.24 + 0.1 * k, 0.31, 0.25 + 0.1 * k, 31.4, 1.2, IRON)
                for k in range(1, 5)
            ],
        ],
    }
)


def render(prims: list[Prim], style: dict) -> str:
    prims = order(prims)
    faces = [f for p in prims for f in p.faces]
    # 縦の大きさ: いちばん上の点が絵の上の端に来るよう、全体を下へずらす
    ys = [y for f in faces for _, y in f.points]
    ys += [f.ymin for f in faces if f.path]
    top = min(min(ys), 0.0)
    off = math.ceil(-top) + 1
    # 下の頂点より下に出るもの（海の高さまで下げた水面）があれば、そのぶん下へ伸ばす。
    # そのときは theme.yaml の anchor を [32, TILE_H + off] にする（main が書き出す）
    below = max(ys) - TILE_H
    height = TILE_H + off + (math.ceil(below) if below > 0.01 else 0)
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{TILE_W}" height="{height}" '
        f'viewBox="0 0 {TILE_W} {height}">',
        f'<g transform="translate(0 {off})" stroke-linejoin="round" stroke-width="0.4">',
    ]
    for f in faces:
        color = _color(style, f)
        width = f' stroke-width="{2 * f.bleed:g}"' if f.bleed else ""
        if f.path:
            out.append(f'<path d="{f.path}" fill="{color}" stroke="{color}"{width}/>')
        else:
            pts = " ".join(f"{x:.2f},{y:.2f}" for x, y in f.points)
            out.append(f'<polygon points="{pts}" fill="{color}" stroke="{color}"{width}/>')
    out += ["</g>", "</svg>"]
    return "\n".join(out) + "\n"


def _color(style: dict, face: Face) -> str:
    k = style[face.shade] if face.k is None or style is STYLES["flat"] else face.k
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
        svg = render(prims, STYLES[args.style])
        path.write_text(svg, encoding="utf-8")
        height = int(svg.split('height="', 1)[1].split('"', 1)[0])
        off = int(svg.split("translate(0 ", 1)[1].split(")", 1)[0])
        if height > TILE_H + off:
            print(f"  {name}: theme.yaml に anchor: [{TILE_W // 2}, {TILE_H + off}]")
    print(f"{len(SHAPES)} 枚を {args.out} に書きました")


if __name__ == "__main__":
    main()
