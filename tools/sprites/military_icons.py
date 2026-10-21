"""軍事第1部・第2部の船団の印を、直線の多角形で SVG にする。

使い方: python tools/sprites/military_icons.py server/static/themes/default/sprites
標準ライブラリだけで動く。施設・部隊・重ねる印は silhouette.py で生成する。
"""

from __future__ import annotations

import argparse
from pathlib import Path

from silhouette import (
    COAL,
    CONTAINER_BLUE,
    COPPER,
    DARK,
    GLASS,
    HULL_DARK,
    METAL,
    NAVY_GRAY,
    RED,
    STYLES,
    WHITE,
    Face,
    _color,
)


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
                # 船首は右。コルベットより大きく、中央に背の高い一つながりの塔を立てる。
                polygon(
                    [
                        (0.5, 6.6),
                        (17.5, 6.6),
                        (22.6, 5.9),
                        (23.7, 5.4),
                        (21.6, 9),
                        (2.1, 9),
                        (0.5, 8),
                    ],
                    NAVY_GRAY,
                ),
                polygon([(0.8, 8.1), (22.4, 8.1), (21.6, 9), (2.1, 9)], lower_hull),
                # 艦尾の格納庫（箱）と、その上のヘリ甲板の印
                polygon([(1.4, 3.9), (7.4, 3.9), (7.4, 6.6), (1.4, 6.6)], METAL),
                polygon([(1.4, 3.6), (7.4, 3.6), (7.4, 4.1), (1.4, 4.1)], NAVY_GRAY),
                polygon([(3.1, 4.7), (5.2, 4.7), (5.2, 6.6), (3.1, 6.6)], DARK),
                # 中央の塔: 台形の下段、四角い上段、その上に一体の細いマスト
                polygon([(8.2, 4.4), (15.6, 4.4), (16.4, 6.6), (7.6, 6.6)], METAL),
                polygon([(9.4, 2.4), (13.6, 2.4), (14.4, 4.4), (8.9, 4.4)], METAL),
                polygon([(9.7, 2.9), (13.4, 2.9), (13.6, 3.4), (9.6, 3.4)], GLASS),
                polygon([(10.9, 0.5), (12.1, 0.5), (12.4, 2.4), (10.6, 2.4)], NAVY_GRAY),
                polygon([(10.2, 0.5), (12.8, 0.5), (12.8, 0.95), (10.2, 0.95)], DARK),
                # 艦首側に垂直発射の四角い蓋の列と、前の主砲
                polygon([(16.9, 6.2), (20.2, 6.2), (20.2, 6.6), (16.9, 6.6)], DARK),
                polygon([(21, 6.5), (21.7, 5.7), (22.4, 5.7), (22.7, 6.4)], METAL),
            ],
        ),
        "fleet/corvette": svg(
            20,
            9,
            [
                # 艦首は右。フリゲートより短く低い一段の艦橋。
                polygon([(0.5, 6.6), (12.5, 6.6), (19.5, 5.6), (17.6, 8), (1.6, 8)], NAVY_GRAY),
                polygon([(0.8, 7.3), (18.2, 7.3), (17.6, 8), (1.6, 8)], lower_hull),
                polygon([(4.7, 4.7), (6, 4.7), (6.5, 6.6), (4.7, 6.6)], NAVY_GRAY),
                polygon([(4.7, 4.5), (6, 4.5), (6.1, 4.9), (4.7, 4.9)], DARK),
                polygon([(7, 4.5), (11, 4.5), (12.3, 6.6), (6.7, 6.6)], METAL),
                polygon([(7.15, 4.9), (11.2, 4.9), (11.5, 5.4), (7, 5.4)], GLASS),
                polygon([(8.5, 1.2), (8.85, 1.2), (8.85, 4.5), (8.5, 4.5)], DARK),
                polygon([(8, 1.5), (9.5, 1.5), (9.5, 1.9), (8, 1.9)], NAVY_GRAY),
                polygon([(14, 6.3), (14.3, 5.7), (15.4, 5.7), (15.8, 6.4)], METAL),
                polygon([(15, 5.8), (17.8, 5.6), (17.8, 5.9), (15, 6.1)], DARK),
            ],
        ),
        "fleet/submarine": svg(
            22,
            7,
            [
                # 折れ線の鋭い船首。水上部分だけを低く描く。
                polygon(
                    [(0.5, 4.7), (3, 3.6), (17.9, 3.6), (21.5, 4.5), (19.7, 5.8), (2.6, 5.8)], DARK
                ),
                polygon([(0.5, 4.7), (21.5, 4.5), (19.7, 5.8), (2.6, 5.8)], COAL),
                polygon([(8, 3.6), (8.3, 1.5), (10.7, 1.5), (11.5, 3.6)], DARK),
                polygon([(9.2, 0.6), (9.5, 0.6), (9.5, 1.5), (9.2, 1.5)], COAL),
            ],
        ),
        "fleet/transport": svg(
            24,
            10,
            [
                # 港の貨物船と同じ暗い船体・白い船橋・青と緑青のコンテナ。
                # 赤は船底の細い帯一か所だけ。煙突には使わない。
                polygon(
                    [(0.5, 6.3), (18.3, 6.3), (23.5, 5.5), (21.8, 8.8), (2, 8.8), (0.5, 7.4)],
                    HULL_DARK,
                ),
                polygon([(1.4, 8.1), (22.2, 8.1), (21.8, 8.8), (2, 8.8)], RED),
                polygon([(2.6, 3.3), (6.5, 3.3), (6.5, 6.3), (2.6, 6.3)], WHITE),
                polygon([(2.4, 2.7), (6.8, 2.7), (6.8, 3.9), (2.4, 3.9)], WHITE),
                polygon([(2.6, 3), (6.6, 3), (6.6, 3.5), (2.6, 3.5)], GLASS),
                polygon([(2.9, 1.2), (4, 1.2), (4, 2.7), (2.9, 2.7)], DARK),
                polygon([(7.5, 4.4), (11.3, 4.4), (11.3, 6.3), (7.5, 6.3)], COPPER),
                polygon([(11.6, 4.4), (15.4, 4.4), (15.4, 6.3), (11.6, 6.3)], CONTAINER_BLUE),
                polygon([(15.7, 4.4), (19.5, 4.4), (19.5, 6.3), (15.7, 6.3)], COPPER),
                polygon([(11.6, 2.5), (15.4, 2.5), (15.4, 4.3), (11.6, 4.3)], COPPER),
                polygon([(15.7, 2.5), (19.5, 2.5), (19.5, 4.3), (15.7, 4.3)], CONTAINER_BLUE),
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
    print("船団4枚を書きました（基準点は各画像の下の真ん中）")


if __name__ == "__main__":
    main()
