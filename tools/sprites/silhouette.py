"""施設と住宅のシルエットの絵（SVG）を、箱・円柱・屋根などの形の組み合わせから作る。

使い方: python tools/sprites/silhouette.py <出力フォルダ> [--style clay|flat]
  例: python tools/sprites/silhouette.py server/static/themes/default/sprites
  clay: 1色の濃淡で面を分ける（上面が明るく、左の側面・右の側面の順に暗い）
  flat: 1色で塗りつぶす（輪郭だけ）

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

# 基の色と、面ごとの明るさ（上面・屋根の左向き・左の側面・屋根の右向き・右の側面）
STYLES = {
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
    """a を b より先に（奥に）描くか。足元が重ならなければ奥のもの、重なれば低いもの。"""
    au0, av0, au1, av1 = a.bounds
    bu0, bv0, bu1, bv1 = b.bounds
    if au1 <= bu0 or av1 <= bv0:
        return True
    if bu1 <= au0 or bv1 <= av0:
        return False
    return a.z0 < b.z0


def order(prims: list[Prim]) -> list[Prim]:
    """奥から手前・下から上の順に並べる（重なりの関係で決まる順。決まらないものは元の順）。"""
    rest = list(prims)
    out: list[Prim] = []
    while rest:
        for p in rest:
            if not any(_before(q, p) for q in rest if q is not p):
                break
        else:
            p = rest[0]  # 循環したら書いた順
        rest.remove(p)
        out.append(p)
    return out


def box(u0: float, v0: float, u1: float, v1: float, z0: float, h: float) -> Prim:
    zt = z0 + h
    return Prim(
        (u0, v0, u1, v1),
        z0,
        [
            Face("left", [iso(u0, v1, z0), iso(u1, v1, z0), iso(u1, v1, zt), iso(u0, v1, zt)]),
            Face("right", [iso(u1, v0, z0), iso(u1, v1, z0), iso(u1, v1, zt), iso(u1, v0, zt)]),
            Face("top", [iso(u0, v0, zt), iso(u1, v0, zt), iso(u1, v1, zt), iso(u0, v1, zt)]),
        ],
    )


def gable(
    u0: float, v0: float, u1: float, v1: float, z0: float, r: float, along: str = "u"
) -> Prim:
    """切妻屋根。along の向きに棟が通る。"""
    if along == "u":
        vm = (v0 + v1) / 2
        a, b = iso(u0, vm, z0 + r), iso(u1, vm, z0 + r)
        faces = [
            Face("slope_r", [iso(u0, v0, z0), iso(u1, v0, z0), b, a]),
            Face("slope_l", [iso(u0, v1, z0), iso(u1, v1, z0), b, a]),
            Face("right", [iso(u1, v0, z0), iso(u1, v1, z0), b]),
        ]
    else:
        um = (u0 + u1) / 2
        a, b = iso(um, v0, z0 + r), iso(um, v1, z0 + r)
        faces = [
            Face("slope_l", [iso(u0, v0, z0), iso(u0, v1, z0), b, a]),
            Face("slope_r", [iso(u1, v0, z0), iso(u1, v1, z0), b, a]),
            Face("left", [iso(u0, v1, z0), iso(u1, v1, z0), b]),
        ]
    return Prim((u0, v0, u1, v1), z0 + 0.1, faces)


def hip(u0: float, v0: float, u1: float, v1: float, z0: float, r: float) -> Prim:
    """四角すいの屋根。"""
    apex = iso((u0 + u1) / 2, (v0 + v1) / 2, z0 + r)
    return Prim(
        (u0, v0, u1, v1),
        z0 + 0.1,
        [
            Face("slope_l", [iso(u0, v1, z0), iso(u1, v1, z0), apex]),
            Face("slope_r", [iso(u1, v0, z0), iso(u1, v1, z0), apex]),
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
    waist: float | None = None,
    cap: str = "flat",
) -> Prim:
    """円柱。r_top で上を細く、waist（0〜1）で胴をくびれさせる（冷却塔）。

    cap は上の端の形: flat（平ら）・dome（丸屋根）・cone（とがり屋根）。
    """
    r_top = r if r_top is None else r_top
    cx, yb, rxb, ryb = _ellipse(uc, vc, r, z0)
    _, yt, rxt, ryt = _ellipse(uc, vc, r_top, z0 + h)

    def half(sign: int) -> str:
        xb, xt = cx + sign * rxb, cx + sign * rxt
        sweep = 1 if sign < 0 else 0
        side = (
            f"Q {cx + sign * rxt * waist:.2f} {(yb + yt) / 2:.2f} {xt:.2f} {yt:.2f}"
            if waist
            else f"L {xt:.2f} {yt:.2f}"
        )
        return (
            f"M {cx:.2f} {yb + ryb:.2f} A {rxb:.2f} {ryb:.2f} 0 0 {sweep} {xb:.2f} {yb:.2f} "
            f"{side} L {cx:.2f} {yt:.2f} Z"
        )

    faces = [Face("left", path=half(-1), ymin=yt), Face("right", path=half(1), ymin=yt)]
    if cap == "dome":
        top = yt - rxt * 0.75
        for sign, shade, sweep in ((-1, "slope_l", 1), (1, "slope_r", 0)):
            x = cx + sign * rxt
            d = _d("M", x, yt, "Q", x, top, cx, top, "L", cx, yt + ryt)
            d += " " + _d("A", rxt, ryt, 0, 0, sweep, x, yt, "Z")
            faces.append(Face(shade, path=d, ymin=top))
    elif cap == "cone":
        top = yt - rxt * 1.2
        faces += [
            Face("slope_l", [(cx - rxt, yt), (cx, top), (cx, yt + ryt)]),
            Face("slope_r", [(cx + rxt, yt), (cx, top), (cx, yt + ryt)]),
        ]
    else:
        arc = ("A", rxt, ryt, 0, 1, 0)
        d = _d("M", cx - rxt, yt, *arc, cx + rxt, yt, *arc, cx - rxt, yt, "Z")
        faces.append(Face("top", path=d, ymin=yt - ryt))
    return Prim((uc - r, vc - r, uc + r, vc + r), z0, faces)


def tree(uc: float, vc: float, r: float, h: float, z0: float = 0) -> list[Prim]:
    """針葉樹: 細い幹と円すい。"""
    cx, yb, rx, ry = _ellipse(uc, vc, r, z0 + h * 0.2)
    _, ya, _, _ = _ellipse(uc, vc, r, z0 + h)
    faces = []
    for sign, shade, sweep in ((-1, "left", 1), (1, "right", 0)):
        x = cx + sign * rx
        d = _d("M", x, yb, "L", cx, ya, "L", cx, yb + ry, "A", rx, ry, 0, 0, sweep, x, yb, "Z")
        faces.append(Face(shade, path=d, ymin=ya))
    crown = Prim((uc - r, vc - r, uc + r, vc + r), z0 + 0.2, faces)
    return [cylinder(uc, vc, r * 0.18, z0, h * 0.25), crown]


def house(
    u0: float, v0: float, u1: float, v1: float, h: float, r: float, along: str = "u"
) -> list[Prim]:
    return [box(u0, v0, u1, v1, 0, h), gable(u0, v0, u1, v1, h, r, along)]


# --- 形の一覧 ----------------------------------------------------------------

SHAPES: dict[str, list[Prim]] = {
    "facility/farm": [
        *[box(0.1, v, 0.9, v + 0.06, 0, 2) for v in (0.1, 0.22, 0.34, 0.46)],
        *house(0.55, 0.58, 0.85, 0.88, 8, 6, "v"),
        cylinder(0.25, 0.75, 0.08, 0, 18, cap="dome"),
    ],
    "facility/lumber_camp": [
        *tree(0.25, 0.22, 0.1, 30),
        *tree(0.5, 0.15, 0.09, 26),
        *tree(0.18, 0.5, 0.09, 24),
        *house(0.5, 0.45, 0.85, 0.8, 9, 6, "u"),
        *[box(0.15, 0.68 + i * 0.07, 0.42, 0.74 + i * 0.07, 0, 4) for i in range(3)],
    ],
    "facility/quarry": [
        box(0.1, 0.1, 0.9, 0.9, 0, 4),
        box(0.18, 0.18, 0.78, 0.78, 4, 5),
        box(0.26, 0.26, 0.62, 0.62, 9, 5),
        box(0.72, 0.18, 0.78, 0.24, 9, 22),
        box(0.4, 0.18, 0.78, 0.23, 29, 2),
    ],
    "facility/mine": [
        box(0.18, 0.18, 0.24, 0.24, 0, 34),
        box(0.36, 0.18, 0.42, 0.24, 0, 34),
        box(0.18, 0.36, 0.24, 0.42, 0, 34),
        box(0.36, 0.36, 0.42, 0.42, 0, 34),
        box(0.15, 0.15, 0.45, 0.45, 34, 4),
        *house(0.5, 0.45, 0.88, 0.85, 12, 7, "u"),
    ],
    "facility/fishing_port": [
        box(0.45, 0.4, 0.98, 0.55, 0, 3),
        box(0.45, 0.62, 0.98, 0.77, 0, 3),
        *house(0.1, 0.15, 0.4, 0.55, 10, 7, "v"),
        box(0.12, 0.6, 0.38, 0.85, 0, 7),
        box(0.85, 0.25, 0.9, 0.3, 3, 26),
        box(0.62, 0.25, 0.9, 0.29, 29, 2),
    ],
    "facility/steel_mill": [
        cylinder(0.25, 0.22, 0.04, 0, 56),
        cylinder(0.42, 0.18, 0.04, 0, 50),
        cylinder(0.28, 0.42, 0.12, 0, 30, r_top=0.08, cap="cone"),
        box(0.45, 0.35, 0.88, 0.85, 0, 20),
        gable(0.45, 0.35, 0.88, 0.85, 20, 7, "u"),
        box(0.12, 0.6, 0.42, 0.88, 0, 12),
    ],
    "facility/building_materials_plant": [
        cylinder(0.22, 0.2, 0.07, 0, 30, cap="cone"),
        cylinder(0.36, 0.2, 0.07, 0, 30, cap="cone"),
        cylinder(0.5, 0.2, 0.07, 0, 30, cap="cone"),
        box(0.15, 0.4, 0.88, 0.85, 0, 14),
        box(0.6, 0.45, 0.85, 0.7, 14, 8),
    ],
    "facility/machinery_plant": [
        cylinder(0.2, 0.2, 0.04, 0, 44),
        box(0.12, 0.3, 0.88, 0.88, 0, 16),
        *[gable(0.12, v, 0.88, v + 0.14, 16, 7, "u") for v in (0.3, 0.44, 0.58, 0.72)],
    ],
    "facility/food_factory": [
        cylinder(0.25, 0.25, 0.1, 0, 22, cap="dome"),
        cylinder(0.48, 0.18, 0.1, 0, 22, cap="dome"),
        box(0.15, 0.45, 0.88, 0.88, 0, 15),
        box(0.62, 0.55, 0.85, 0.8, 15, 5),
    ],
    "facility/consumer_goods_factory": [
        box(0.15, 0.15, 0.55, 0.45, 0, 26),
        box(0.15, 0.45, 0.88, 0.88, 0, 14),
        *[box(u, 0.6, u + 0.1, 0.7, 14, 4) for u in (0.3, 0.5, 0.7)],
    ],
    "facility/thermal_power_plant": [
        cylinder(0.28, 0.25, 0.17, 0, 42, r_top=0.12, waist=0.75),
        cylinder(0.62, 0.15, 0.04, 0, 64),
        box(0.4, 0.4, 0.88, 0.85, 0, 20),
        box(0.12, 0.6, 0.4, 0.88, 0, 12),
    ],
    "facility/hydro_power_plant": [
        box(0.1, 0.2, 0.9, 0.42, 0, 28),
        box(0.1, 0.42, 0.9, 0.48, 0, 24),
        *[box(u, 0.48, u + 0.08, 0.68, 0, 10) for u in (0.25, 0.45, 0.65)],
        box(0.2, 0.68, 0.8, 0.9, 0, 9),
    ],
    "facility/government_office": [
        box(0.1, 0.25, 0.9, 0.75, 0, 18),
        box(0.35, 0.3, 0.65, 0.7, 18, 6),
        cylinder(0.5, 0.5, 0.13, 24, 6, cap="dome"),
        box(0.1, 0.75, 0.9, 0.86, 0, 3),
    ],
    "facility/school": [
        box(0.12, 0.15, 0.45, 0.85, 0, 14),
        box(0.45, 0.55, 0.88, 0.85, 0, 14),
        box(0.25, 0.4, 0.35, 0.5, 14, 12),
        hip(0.25, 0.4, 0.35, 0.5, 26, 7),
    ],
    "facility/university": [
        box(0.1, 0.12, 0.9, 0.42, 0, 18),
        gable(0.1, 0.12, 0.9, 0.42, 18, 7, "u"),
        box(0.42, 0.5, 0.58, 0.66, 0, 40),
        hip(0.42, 0.5, 0.58, 0.66, 40, 12),
        box(0.1, 0.55, 0.35, 0.88, 0, 12),
        box(0.65, 0.55, 0.9, 0.88, 0, 12),
    ],
    "facility/bank": [
        box(0.2, 0.12, 0.85, 0.62, 0, 22),
        *[box(u, 0.7, u + 0.06, 0.76, 0, 16) for u in (0.2, 0.36, 0.52, 0.68)],
        box(0.15, 0.62, 0.9, 0.8, 16, 4),
        gable(0.15, 0.62, 0.9, 0.8, 20, 7, "v"),
        box(0.12, 0.62, 0.92, 0.86, 0, 2),
    ],
    "housing/village": [
        *house(0.15, 0.15, 0.42, 0.4, 7, 6, "u"),
        *house(0.55, 0.2, 0.8, 0.45, 7, 6, "v"),
        *house(0.25, 0.55, 0.5, 0.8, 7, 6, "v"),
    ],
    "housing/town": [
        *house(0.12, 0.12, 0.42, 0.4, 14, 6, "u"),
        *house(0.55, 0.15, 0.85, 0.42, 10, 6, "v"),
        *house(0.12, 0.55, 0.4, 0.85, 10, 6, "v"),
        *house(0.55, 0.55, 0.85, 0.85, 16, 6, "u"),
    ],
    "housing/city": [
        box(0.12, 0.12, 0.45, 0.45, 0, 36),
        box(0.55, 0.12, 0.88, 0.42, 0, 26),
        box(0.12, 0.55, 0.42, 0.88, 0, 22),
        box(0.52, 0.52, 0.88, 0.88, 0, 42),
    ],
    "housing/metropolis": [
        box(0.1, 0.1, 0.9, 0.9, 0, 6),
        box(0.15, 0.15, 0.42, 0.42, 6, 58),
        box(0.58, 0.15, 0.85, 0.4, 6, 40),
        box(0.15, 0.58, 0.4, 0.85, 6, 34),
        box(0.52, 0.52, 0.85, 0.85, 6, 66),
        box(0.62, 0.62, 0.75, 0.75, 72, 8),
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
        color = _color(style, f.shade)
        if f.path:
            out.append(f'<path d="{f.path}" fill="{color}" stroke="{color}"/>')
        else:
            pts = " ".join(f"{x:.2f},{y:.2f}" for x, y in f.points)
            out.append(f'<polygon points="{pts}" fill="{color}" stroke="{color}"/>')
    out += ["</g>", "</svg>"]
    return "\n".join(out) + "\n"


def _color(style: dict, shade: str) -> str:
    k = style[shade]
    r, g, b = (round(c * k) for c in style["base"])
    return f"#{r:02x}{g:02x}{b:02x}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out", type=Path)
    parser.add_argument("--style", choices=sorted(STYLES), default="clay")
    args = parser.parse_args()
    for name, prims in SHAPES.items():
        path = args.out / f"{name}.svg"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render(prims, STYLES[args.style]), encoding="utf-8")
    print(f"{len(SHAPES)} 枚を {args.out} に書きました")


if __name__ == "__main__":
    main()
