"""島の俯瞰図に出す船の絵（ship/）を作る。標準ライブラリだけで動く。

案6（港の商船）と W-100（母港の艦隊）の絵で、斜め上から見た形にする。世界マップの
横から見た絵（fleet/）とは別。船体は等角の斜め（右下へ 2:1）に置き、長さは
商船 44px・輸送船とフリゲート 40px・潜水艦 36px・小さな軍艦 34px（マスの幅 64px に対して
0.53〜0.69 倍。掲示板 #6133 で置いた大きさ）。

使い方: python tools/sprites/ship_sprites.py
server/static/themes/default/sprites/ship/ の下に SVG を書き、theme.yaml に書く
基準点（canvas の真ん中）を標準出力に出す。
"""

from __future__ import annotations

import math
from pathlib import Path

OUT = Path(__file__).resolve().parents[2] / "server/static/themes/default/sprites/ship"

# 船の軸（船尾→船首）の向きと、それに直交する向き（等角の斜め 2:1）
U = (2 / math.sqrt(5), 1 / math.sqrt(5))
V = (-1 / math.sqrt(5), 2 / math.sqrt(5))


class Hull:
    """船尾の真ん中を原点に、軸方向 t・幅方向 s で形を置く。"""

    def __init__(self, length: float, width: float) -> None:
        self.length = length
        self.width = width
        self.points: list[tuple[float, float]] = []

    def p(self, t: float, s: float) -> tuple[float, float]:
        x = t * U[0] + s * V[0]
        y = t * U[1] + s * V[1]
        self.points.append((x, y))
        return (x, y)

    def rect(self, t0: float, t1: float, s0: float, s1: float) -> list[tuple[float, float]]:
        return [self.p(t0, s0), self.p(t1, s0), self.p(t1, s1), self.p(t0, s1)]

    def outline(self, scale: float = 1.0, taper_stern: bool = False) -> list[tuple[float, float]]:
        """船体の輪郭。scale は水面の縁用に少し広げる。taper_stern は潜水艦用（船尾も尖らせる）。"""
        L, W = self.length, self.width * scale
        cx, cy = self.length / 2, 0.0

        def q(t: float, s: float) -> tuple[float, float]:
            return self.p(cx + (t - cx) * scale, cy + s * scale)

        if taper_stern:
            return [
                q(0, 0),
                q(0.10 * L, -0.5 * W),
                q(0.85 * L, -0.5 * W),
                q(L, 0),
                q(0.85 * L, 0.5 * W),
                q(0.10 * L, 0.5 * W),
            ]
        return [
            q(0, -0.30 * W),
            q(0.12 * L, -0.5 * W),
            q(0.62 * L, -0.5 * W),
            q(0.88 * L, -0.27 * W),
            q(L, 0),
            q(0.88 * L, 0.27 * W),
            q(0.62 * L, 0.5 * W),
            q(0.12 * L, 0.5 * W),
            q(0, 0.30 * W),
        ]


def fmt(points: list[tuple[float, float]]) -> str:
    return " ".join(f"{x:.1f},{y:.1f}" for x, y in points)


def build(
    name: str, parts: list[tuple[list[tuple[float, float]], str]], hull: Hull
) -> tuple[int, int]:
    """parts（多角形と色の組）を、全体が収まる canvas の SVG にして書き出す。"""
    xs = [x for x, _ in hull.points]
    ys = [y for _, y in hull.points]
    x0, y0 = math.floor(min(xs)) - 2, math.floor(min(ys)) - 2
    w = math.ceil(max(xs)) + 2 - x0
    h = math.ceil(max(ys)) + 2 - y0
    moved = [([(x - x0, y - y0) for x, y in points], color) for points, color in parts]
    body = "\n".join(f'<polygon points="{fmt(points)}" fill="{color}"/>' for points, color in moved)
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}">\n{body}\n</svg>\n'
    )
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{name}.svg").write_text(svg, encoding="utf-8")
    return w, h


def merchant() -> tuple[int, int]:
    hull = Hull(44, 13)
    L, W = hull.length, hull.width
    parts = [
        (hull.outline(1.14), "#dcebf0"),  # 水面の縁
        (hull.outline(), "#8f4630"),  # 船体（錆び色）
        (hull.outline(0.86), "#c2a878"),  # 甲板（輪郭を縮めた形。矩形だと角が船体の外へ出る）
        (hull.rect(0.30 * L, 0.45 * L, -0.30 * W, 0.30 * W), "#3e7d8a"),  # コンテナ
        (hull.rect(0.48 * L, 0.63 * L, -0.30 * W, 0.30 * W), "#c2692c"),
        (hull.rect(0.66 * L, 0.81 * L, -0.30 * W, 0.30 * W), "#77828c"),
        (hull.rect(0.08 * L, 0.20 * L, -0.26 * W, 0.26 * W), "#e9e6de"),  # 船橋
        (hull.rect(0.10 * L, 0.16 * L, -0.26 * W, 0.26 * W), "#5b7d94"),  # 窓
    ]
    return build("merchant", parts, hull)


def transport() -> tuple[int, int]:
    hull = Hull(40, 13)
    L, W = hull.length, hull.width
    parts = [
        (hull.outline(1.14), "#dcebf0"),
        (hull.outline(), "#66755f"),  # 船体（灰緑）
        (hull.outline(0.86), "#75846e"),  # 甲板（輪郭を縮めた形）
        (hull.rect(0.30 * L, 0.52 * L, -0.30 * W, 0.30 * W), "#57634f"),  # 荷のハッチ
        (hull.rect(0.56 * L, 0.78 * L, -0.30 * W, 0.30 * W), "#57634f"),
        (hull.rect(0.08 * L, 0.20 * L, -0.26 * W, 0.26 * W), "#ddd9cf"),  # 船橋
        (hull.rect(0.10 * L, 0.16 * L, -0.26 * W, 0.26 * W), "#54626e"),
    ]
    return build("transport", parts, hull)


def frigate() -> tuple[int, int]:
    hull = Hull(40, 12)
    L, W = hull.length, hull.width
    parts = [
        (hull.outline(1.14), "#dcebf0"),
        (hull.outline(), "#7e8a94"),  # 船体（灰色）
        (hull.outline(0.86), "#95a3ad"),  # 甲板（輪郭を縮めた形）
        (hull.rect(0.04 * L, 0.17 * L, -0.30 * W, 0.30 * W), "#8b98a1"),  # 船尾の甲板
        (hull.rect(0.42 * L, 0.57 * L, -0.30 * W, 0.30 * W), "#aab4bc"),  # 艦橋
        (hull.rect(0.45 * L, 0.54 * L, -0.30 * W, 0.30 * W), "#9db6c7"),  # 窓
        (hull.rect(0.475 * L, 0.505 * L, -0.07 * W, 0.07 * W), "#555b63"),  # マスト
        (hull.rect(0.78 * L, 0.87 * L, -0.16 * W, 0.16 * W), "#5d666e"),  # 船首の砲
    ]
    return build("frigate", parts, hull)


def corvette() -> tuple[int, int]:
    hull = Hull(34, 11)
    L, W = hull.length, hull.width
    parts = [
        (hull.outline(1.14), "#dcebf0"),
        (hull.outline(), "#7e8a94"),
        (hull.outline(0.86), "#95a3ad"),
        (hull.rect(0.40 * L, 0.56 * L, -0.30 * W, 0.30 * W), "#aab4bc"),  # 艦橋
        (hull.rect(0.43 * L, 0.53 * L, -0.30 * W, 0.30 * W), "#9db6c7"),
        (hull.rect(0.465 * L, 0.50 * L, -0.08 * W, 0.08 * W), "#555b63"),
        (hull.rect(0.76 * L, 0.86 * L, -0.17 * W, 0.17 * W), "#5d666e"),
    ]
    return build("corvette", parts, hull)


def submarine() -> tuple[int, int]:
    hull = Hull(36, 10)
    L, W = hull.length, hull.width
    parts = [
        (hull.outline(1.16), "#dcebf0"),  # 水面の縁（浮上中）
        (hull.outline(taper_stern=True), "#414e59"),  # 船体（濃い灰）
        (hull.rect(0.08 * L, 0.92 * L, -0.18 * W, 0.18 * W), "#4f5d68"),  # 背の帯
        (hull.rect(0.52 * L, 0.65 * L, -0.24 * W, 0.24 * W), "#39434d"),  # 司令塔
    ]
    return build("submarine", parts, hull)


def main() -> None:
    for name, make in [
        ("merchant", merchant),
        ("transport", transport),
        ("frigate", frigate),
        ("corvette", corvette),
        ("submarine", submarine),
    ]:
        w, h = make()
        print(f"ship/{name}: {w}x{h} anchor [{w // 2}, {h // 2}]")


if __name__ == "__main__":
    main()
