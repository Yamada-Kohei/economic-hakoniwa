"""軍事サンプル第1部の小さな印を、平らな幾何の形だけで SVG にする。

使い方: python tools/sprites/military_icons.py server/static/themes/default/sprites
標準ライブラリだけで動く。施設と塹壕は silhouette.py で生成する。
"""

from __future__ import annotations

import argparse
from pathlib import Path

from silhouette import (
    DARK,
    GLASS,
    IRON,
    METAL,
    MILITARY,
    MILITARY_DARK,
    NAVY_GRAY,
    ROOF_STEEL,
    SAND,
    STYLES,
    Face,
    _color,
)


def polygon(points: list[tuple[float, float]], color: str) -> str:
    """同色の細い縁と丸い角を持つ多角形。"""
    coords = " ".join(f"{x:g},{y:g}" for x, y in points)
    return f'<polygon points="{coords}" fill="{color}" stroke="{color}"/>'


def path(d: str, color: str) -> str:
    """ヘルメットなどの単純な曲面。"""
    return f'<path d="{d}" fill="{color}" stroke="{color}"/>'


def soldier(x: float, y: float) -> list[str]:
    """頭と肩が残る兵の胸像。外側の暗い形を内側の形より一回り大きくする。"""
    return [
        polygon(
            [
                (x - 5, y + 8),
                (x - 4, y + 3),
                (x, y + 1),
                (x + 4, y + 3),
                (x + 5, y + 8),
                (x + 3.8, y + 14),
                (x - 3.8, y + 14),
            ],
            IRON,
        ),
        polygon(
            [
                (x - 3.8, y + 7),
                (x - 3, y + 3.8),
                (x, y + 2.2),
                (x + 3, y + 3.8),
                (x + 3.8, y + 7),
                (x + 2.8, y + 13),
                (x - 2.8, y + 13),
            ],
            MILITARY,
        ),
        polygon(
            [
                (x + 1, y + 3),
                (x + 3, y + 4),
                (x + 3.8, y + 7),
                (x + 2.8, y + 13),
                (x + 1, y + 13),
            ],
            MILITARY_DARK,
        ),
        polygon(
            [
                (x - 2.5, y - 2),
                (x + 2.5, y - 2),
                (x + 2, y + 3),
                (x - 1, y + 3),
                (x - 2.5, y + 1),
            ],
            IRON,
        ),
        polygon([(x - 1.8, y - 1), (x + 1.6, y - 1), (x + 1.2, y + 2), (x - 1, y + 2)], SAND),
        path(
            f"M {x - 4.4:g} {y:g} Q {x - 4.4:g} {y - 6:g} {x:g} {y - 6:g} "
            f"Q {x + 4.4:g} {y - 6:g} {x + 4.4:g} {y:g} Z",
            IRON,
        ),
        path(
            f"M {x - 3.4:g} {y - 1:g} Q {x - 3.4:g} {y - 5:g} {x:g} {y - 5:g} "
            f"Q {x + 3.4:g} {y - 5:g} {x + 3.4:g} {y - 1:g} Z",
            MILITARY,
        ),
        polygon(
            [
                (x - 4.5, y - 0.8),
                (x + 4.5, y - 0.8),
                (x + 4.5, y + 0.3),
                (x - 4.5, y + 0.3),
            ],
            MILITARY_DARK,
        ),
    ]


def svg(width: int, height: int, parts: list[str]) -> str:
    """基準点は下の真ん中。国の色の輪や水面は入れない。"""
    return "\n".join(
        [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}">',
            '<g stroke-width="0.4" stroke-linejoin="round">',
            *parts,
            "</g>",
            "</svg>",
            "",
        ]
    )


def icons() -> dict[str, str]:
    """今回の歩兵とフリゲートだけを作る。"""
    hull_side = _color(STYLES["color"], Face("left", NAVY_GRAY))
    return {
        "unit/infantry": svg(28, 28, [*soldier(6.5, 7.5), *soldier(21.5, 7.5), *soldier(14, 12)]),
        "fleet/frigate": svg(
            16,
            12,
            [
                polygon([(0.6, 8.2), (15.3, 8.2), (12.8, 10.9), (2.2, 10.9)], hull_side),
                polygon([(0.6, 8.2), (14.3, 7.4), (15.3, 8.2), (2, 9)], NAVY_GRAY),
                polygon([(5, 5.6), (9.6, 5.6), (10.3, 8.1), (4.5, 8.1)], NAVY_GRAY),
                polygon([(6, 4.5), (8.6, 4.5), (9, 6.4), (5.6, 6.4)], METAL),
                polygon([(6, 5), (8.5, 5), (8.7, 5.6), (5.9, 5.6)], GLASS),
                polygon([(3.5, 5.2), (4.6, 5.2), (4.6, 7.9), (3.5, 7.9)], ROOF_STEEL),
                polygon([(7.8, 1), (8.5, 1), (8.5, 4.5), (7.8, 4.5)], IRON),
                polygon([(6.5, 1.5), (10, 1.5), (10, 2.2), (6.5, 2.2)], METAL),
                polygon([(11.3, 7), (12.3, 7), (12.3, 8), (11.3, 8)], NAVY_GRAY),
                polygon([(12, 7), (14.2, 6.7), (14.2, 7.2), (12, 7.6)], DARK),
            ],
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    for name, content in icons().items():
        destination = args.out / f"{name}.svg"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
    print("歩兵 28×28（anchor [14, 28]）、フリゲート 16×12（anchor [8, 12]）を書きました")


if __name__ == "__main__":
    main()
