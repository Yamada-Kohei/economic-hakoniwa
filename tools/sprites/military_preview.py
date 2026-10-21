"""軍事第1部・第2部の比較ページを作る。theme.yaml やゲーム本体を使わない確認用。

使い方: python tools/sprites/military_preview.py
出力: tools/sprites/military-preview.html（画像を埋め込んだ単独の HTML）
      tools/sprites/military-part2-4x.html（第2部17枚の4倍一覧）
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
    ("unit/infantry", "歩兵の印・単体"),
    ("unit/infantry", "歩兵・町", "housing/town"),
    ("unit/infantry", "歩兵・農場", "facility/farm"),
    ("unit/infantry", "歩兵・兵舎", "facility/barracks"),
    ("fleet/frigate", "フリゲート"),
    ("overlay/trench_back", "陣地・奥側のみ"),
    ("overlay/trench_front", "陣地・手前側のみ"),
    ("trench", "陣地・平地（2枚を合成）"),
    ("trench", "陣地・町", "housing/town"),
    ("trench", "陣地・農場", "facility/farm"),
    ("trench", "陣地・兵舎", "facility/barracks"),
    ("facility/police", "既存・警察署"),
    ("facility/port", "既存・港"),
    ("facility/shipyard", "既存・造船所"),
    ("facility/machinery_plant", "既存・機械工場"),
    ("housing/town", "既存・町"),
]

PART2_FACILITIES = [
    ("arms_plant", "兵器工場"),
    ("copper_smelter", "銅の精錬所"),
    ("electronics_plant", "電子機器工場"),
    ("fortress", "要塞"),
    ("missile_plant", "ミサイル工場"),
    ("missile_base", "ミサイル基地"),
    ("air_defense", "防空陣地"),
    ("nuclear_facility", "核施設"),
]
PART2 = [(f"facility/{name}", title) for name, title in PART2_FACILITIES]
PART2 += [
    (f"facility/{name}", title + "・山", None, "mountain")
    for name, title in PART2_FACILITIES
    if name in ("missile_base", "nuclear_facility")
]
for name, title in (("armor", "機甲"), ("rebels", "反乱軍")):
    PART2.append((f"unit/{name}", title + "・単体"))
    PART2 += [
        (f"unit/{name}", title + "・" + label, under)
        for under, label in (
            ("housing/town", "町"),
            ("facility/farm", "農場"),
            ("facility/barracks", "兵舎"),
        )
    ]
PART2 += [
    (f"fleet/{name}", title)
    for name, title in (
        ("corvette", "小さな軍艦"),
        ("submarine", "潜水艦"),
        ("transport", "輸送船"),
    )
]
LAYERS = {
    "battlefield": "地形→焦げ跡→建物",
    "contaminated": "地形→灰色の斑点→建物",
    "occupied": "地形→建物→柵と見張り台",
    "ruined": "地形→瓦礫（建物の代わり）",
}
for name, title in (
    ("battlefield", "戦場"),
    ("contaminated", "汚染"),
    ("occupied", "占領"),
    ("ruined", "壊れた施設"),
):
    for under, label, terrain in (
        (None, "平地", "plain"),
        ("terrain/mountain", "山", "mountain"),
        ("housing/town", "町", "plain"),
        ("facility/farm", "農場", "plain"),
        ("facility/barracks", "兵舎", "plain"),
    ):
        PART2.append((f"overlay/{name}", title + "・" + label, under, terrain))


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


def scene(name: str, under: str | None = None, terrain: str = "plain") -> str:
    """地面と重ねの簡易比較。実ゲームでの表示確認とは分ける。"""
    content = '<polygon points="32,72 64,88 64,96 32,80" fill="#6f9a4e"/>'
    content += '<polygon points="96,72 64,88 64,96 96,80" fill="#5c8040"/>'
    content += '<polygon points="64,56 96,72 64,88 32,72" fill="#8cc163"/>'
    if terrain == "mountain":
        content = content.replace("#6f9a4e", "#7b6f5f").replace("#5c8040", "#665c4f")
        content = content.replace("#8cc163", "#9a8b77")
        # 施設では山の絵を消して色面に置く（map-art.md）。隣の山で地形も比較する。
        content = image("terrain/mountain", 96, 72) + content
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
    if name == "trench":
        content += image("overlay/trench_back", 64, 88)
    if name in ("overlay/battlefield", "overlay/contaminated"):
        if under and under.startswith("terrain/"):
            content += image(under, 64, 88)
        content += image(name, 64, 88)
    if (
        under
        and name != "overlay/ruined"
        and not (
            name in ("overlay/battlefield", "overlay/contaminated") and under.startswith("terrain/")
        )
    ):
        content += image(under, 64, 88)
    if name == "trench":
        content += image("overlay/trench_front", 64, 88)
    elif name.startswith("unit/"):
        content += image(name, 64, 76)
    elif name.startswith("fleet/"):
        content += image(name, 64, 78)
    elif name in ("overlay/battlefield", "overlay/contaminated"):
        pass
    else:
        content += image(name, 64, 88)
    return f'<svg width="128" height="120" viewBox="0 0 128 120">{content}</svg>'


def main() -> None:
    parts = [
        '<!doctype html><html lang="ja"><meta charset="utf-8">',
        "<title>軍事の絵 第1部・第2部 比較</title>",
        "<style>body{font:14px system-ui;background:#eeeae2;color:#34363a;margin:24px}",
        "nav a{margin-right:24px}section{display:flex;flex-wrap:wrap;gap:12px}",
        "figure{margin:0;padding:12px;background:#faf8f2;border:1px solid #ccc6ba}",
        "figcaption{max-width:240px}small{display:block;color:#666}",
        ".s025 svg{width:32px;height:30px}.s1 svg{width:128px;height:120px}",
        ".s4 svg{width:512px;height:480px}</style>",
        "<h1>軍事の絵 第2部・承認済み第1部との比較</h1>",
        "<p>第2部17枚。ゲームへの登録前の独立した比較ページです。"
        "焦げ跡と汚染→建物→占領の順。瓦礫は建物を置き換えます。"
        "山に建つ施設は地形の色面に置き、隣に山を配置。第1部は末尾に比較用として並べています。</p>",
        '<nav><a href="#s1">等倍</a><a href="#s025">0.25倍</a><a href="#s4">4倍</a></nav>',
    ]
    for key, label in (("s1", "等倍"), ("s025", "0.25倍"), ("s4", "4倍")):
        parts.append(f'<h2 id="{key}">{label}</h2><section class="{key}">')
        for sample in PART2 + SAMPLES:
            name, title, *context = sample
            under = context[0] if context else None
            terrain = context[1] if len(context) > 1 else "plain"
            layer = LAYERS.get(name.removeprefix("overlay/"), "")
            parts.append(
                "<figure>"
                + scene(name, under, terrain)
                + f"<figcaption>{html.escape(title)}<small>{html.escape(name)}</small>"
                + f"<small>{html.escape(layer)}</small>"
                + "</figcaption></figure>"
            )
        parts.append("</section>")
    parts.append("</html>")
    destination = Path(__file__).with_name("military-preview.html")
    destination.write_text("\n".join(parts), encoding="utf-8")
    # 4倍の全17枚を一画面で見直すための一覧。比較ページと同じ scene() で描く。
    unique = [(f"facility/{name}", title) for name, title in PART2_FACILITIES]
    unique += [(f"unit/{name}", title) for name, title in (("armor", "機甲"), ("rebels", "反乱軍"))]
    unique += [
        (f"fleet/{name}", title)
        for name, title in (
            ("corvette", "小さな軍艦"),
            ("submarine", "潜水艦"),
            ("transport", "輸送船"),
        )
    ]
    unique += [
        (f"overlay/{name}", title)
        for name, title in (
            ("battlefield", "戦場"),
            ("occupied", "占領"),
            ("ruined", "瓦礫"),
            ("contaminated", "汚染"),
        )
    ]
    sheet = [
        '<!doctype html><html lang="ja"><meta charset="utf-8"><title>第2部 4倍一覧</title>',
        "<style>body{margin:0;background:#eeeae2;font:18px system-ui;display:grid;"
        "grid-template-columns:repeat(4,384px)}figure{margin:0;height:410px;overflow:hidden}"
        "svg{width:512px;height:480px;margin:-80px -64px -30px}"
        "figcaption{text-align:center}</style>",
    ]
    sheet += [
        "<figure>" + scene(name) + f"<figcaption>{title} / {name}</figcaption></figure>"
        for name, title in unique
    ]
    Path(__file__).with_name("military-part2-4x.html").write_text(
        "\n".join(sheet), encoding="utf-8"
    )
    print(destination)


if __name__ == "__main__":
    main()
