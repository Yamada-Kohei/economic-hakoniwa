"""軍事サンプル第1部の船団の印を、直線の多角形で SVG にする。

使い方: python tools/sprites/military_icons.py server/static/themes/default/sprites
標準ライブラリだけで動く。施設・歩兵・塹壕は silhouette.py で生成する。
"""

from __future__ import annotations

import argparse
from pathlib import Path

from silhouette import DARK, GLASS, METAL, NAVY_GRAY, STYLES, Face, _color


def polygon(points: list[tuple[float, float]], color: str) -> str:
    """船体・艦橋・装備を直線の輪郭で描く。"""
    coords = " ".join(f"{x:g},{y:g}" for x, y in points)
    return f'<polygon points="{coords}" fill="{color}"/>'


def svg(width: int, height: int, parts: list[str]) -> str:
    """基準点は下の真ん中。国の色の輪や水面は入れない。"""
    return "\n".join(
        [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}">',
            *parts,
            "</svg>",
            "",
        ]
    )


def icons() -> dict[str, str]:
    """現代の護衛艦に共通する低い船体と装備の配置。特定の艦は写さない。"""
    lower_hull = _color(STYLES["color"], Face("left", NAVY_GRAY))
    return {
        "fleet/frigate": svg(
            24,
            10,
            [
                # 船首は右。反り上がる鋭い艦首から、低い艦尾甲板までを一本で結ぶ。
                polygon(
                    [
                        (0.5, 7.1),
                        (15.8, 7.1),
                        (20.3, 6.6),
                        (23.6, 6),
                        (21.5, 9),
                        (2.1, 9),
                        (0.5, 8.1),
                    ],
                    NAVY_GRAY,
                ),
                polygon([(0.8, 8.1), (22.1, 8.1), (21.5, 9), (2.1, 9)], lower_hull),
                # 艦尾のヘリ甲板を広く空け、角ばった煙突を艦橋の後ろに一本。
                polygon([(6.7, 4.4), (8.1, 4.4), (8.7, 7.1), (6.7, 7.1)], NAVY_GRAY),
                polygon([(6.7, 4.2), (8.1, 4.2), (8.2, 4.7), (6.7, 4.7)], DARK),
                # 二段の上部構造。艦橋の上段は前を傾け、窓の帯は細くする。
                polygon([(8.8, 5.1), (14.1, 5.1), (15.6, 7.1), (8.3, 7.1)], METAL),
                polygon([(10, 3.6), (13.3, 3.6), (14.2, 5.3), (9.7, 5.3)], METAL),
                polygon([(9.95, 4), (13.5, 4), (13.78, 4.55), (9.85, 4.55)], GLASS),
                # マストは幅0.4px。小さなレーダー板を載せ、艦橋の幅を超えない。
                polygon([(11.1, 0.65), (11.5, 0.65), (11.5, 3.6), (11.1, 3.6)], DARK),
                polygon([(10.4, 0.8), (12.2, 0.8), (12.2, 1.25), (10.4, 1.25)], NAVY_GRAY),
                # 艦首寄りの低い砲塔と、細い砲身。
                polygon([(17, 6.6), (17.3, 5.8), (18.4, 5.8), (18.8, 6.7)], METAL),
                polygon([(18.1, 5.95), (21, 5.7), (21, 6.05), (18.1, 6.3)], DARK),
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
    print("フリゲート 24×10（anchor [12, 10]）を書きました")


if __name__ == "__main__":
    main()
