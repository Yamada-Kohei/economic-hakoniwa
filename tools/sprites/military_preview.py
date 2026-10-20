"""軍事第1部の比較ページを作る。theme.yaml やゲーム本体を使わない確認用。

使い方: python tools/sprites/military_preview.py
出力: tools/sprites/military-preview.html（画像を埋め込んだ単独の HTML）
標準ライブラリだけで動く。
"""

from __future__ import annotations

import base64
import html
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPRITES = ROOT / "server/static/themes/default/sprites"
SAMPLES = [
    ("facility/barracks", "兵舎"),
    ("facility/naval_base", "軍港・海が右下"),
    ("facility/naval_base@sw", "軍港・海が左下"),
    ("facility/naval_base@ne", "軍港・海が右上"),
    ("facility/naval_base@nw", "軍港・海が左上"),
    ("facility/radar", "レーダー"),
    ("unit/infantry", "歩兵の印"),
    ("fleet/frigate", "フリゲート"),
    ("overlay/trench", "陣地・平地"),
    ("overlay/trench", "陣地・町", "housing/town"),
    ("overlay/trench", "陣地・農場", "facility/farm"),
    ("overlay/trench", "陣地・兵舎", "facility/barracks"),
    ("facility/police", "既存・警察署"),
    ("facility/port", "既存・港"),
    ("facility/shipyard", "既存・造船所"),
    ("facility/machinery_plant", "既存・機械工場"),
    ("housing/town", "既存・町"),
]


def image(name: str, x: float, y: float) -> str:
    """基準点が (x, y) になるように画像を置く。"""
    content = (SPRITES / f"{name}.svg").read_bytes()
    root = ET.fromstring(content)
    w, h = int(root.attrib["width"]), int(root.attrib["height"])
    anchors = {
        "facility/naval_base": 49,
        "facility/naval_base@sw": 49,
        "facility/port": 60,
        "facility/shipyard": 65,
    }
    ay = anchors.get(name, h)
    data = base64.b64encode(content).decode("ascii")
    return (
        f'<image x="{x - w / 2:g}" y="{y - ay:g}" width="{w}" height="{h}" '
        f'href="data:image/svg+xml;base64,{data}"/>'
    )


def scene(name: str, under: str | None = None) -> str:
    """地面と重ねの簡易比較。実ゲームでの表示確認とは分ける。"""
    content = '<polygon points="32,72 64,88 64,96 32,80" fill="#6f9a4e"/>'
    content += '<polygon points="96,72 64,88 64,96 96,80" fill="#5c8040"/>'
    content += '<polygon points="64,56 96,72 64,88 32,72" fill="#8cc163"/>'
    # 4 方向とも、海の隣マスとの水面の高さを比べる。
    if name.startswith("facility/naval_base"):
        sea = {
            "facility/naval_base": "96,80 128,96 96,112 64,96",
            "facility/naval_base@sw": "32,80 64,96 32,112 0,96",
            "facility/naval_base@ne": "96,48 128,64 96,80 64,64",
            "facility/naval_base@nw": "32,48 64,64 32,80 0,64",
        }[name]
        water = f'<polygon points="{sea}" fill="#2f6db5"/>'
        content = content + water if "@ne" not in name and "@nw" not in name else water + content
    if name.startswith("fleet/"):
        content = '<polygon points="64,56 96,72 64,88 32,72" fill="#2f6db5"/>'
    if under:
        content += image(under, 64, 88)
    if name.startswith("unit/"):
        content += image("housing/town", 64, 88)
        content += image(name, 64, 76)
    elif name.startswith("fleet/"):
        content += image(name, 64, 78)
    else:
        content += image(name, 64, 88)
    return f'<svg width="128" height="120" viewBox="0 0 128 120">{content}</svg>'


def main() -> None:
    parts = [
        '<!doctype html><html lang="ja"><meta charset="utf-8">',
        "<title>軍事の絵 第1部・比較</title>",
        "<style>body{font:14px system-ui;background:#eeeae2;color:#34363a;margin:24px}",
        "nav a{margin-right:24px}section{display:flex;flex-wrap:wrap;gap:12px}",
        "figure{margin:0;padding:12px;background:#faf8f2;border:1px solid #ccc6ba}",
        "figcaption{max-width:240px}small{display:block;color:#666}",
        ".s025 svg{width:32px;height:30px}.s1 svg{width:128px;height:120px}",
        ".s4 svg{width:512px;height:480px}</style>",
        "<h1>軍事の絵 第1部・雰囲気確認</h1>",
        "<p>幾何の形だけで制作。6種類・9 SVG。ゲームへの登録前の独立した比較ページです。</p>",
        '<nav><a href="#s1">等倍</a><a href="#s025">0.25倍</a><a href="#s4">4倍</a></nav>',
    ]
    for key, label in (("s1", "等倍"), ("s025", "0.25倍"), ("s4", "4倍")):
        parts.append(f'<h2 id="{key}">{label}</h2><section class="{key}">')
        for name, title, *under in SAMPLES:
            parts.append(
                "<figure>"
                + scene(name, under[0] if under else None)
                + f"<figcaption>{html.escape(title)}<small>{html.escape(name)}</small>"
                + "</figcaption></figure>"
            )
        parts.append("</section>")
    parts.append("</html>")
    destination = Path(__file__).with_name("military-preview.html")
    destination.write_text("\n".join(parts), encoding="utf-8")
    print(destination)


if __name__ == "__main__":
    main()
