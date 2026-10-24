"""マップの絵（テーマの sprites）の検査。決まりは docs/map-art.md にあります。

絵を足す PR（Codex などに任せたものを含む）で、ファイルの置き忘れ・名前の誤り・危ない SVG・
出典の書き忘れを CI で見つけるためのものです。絵の出来の良し悪しはここでは見ません。
"""

from __future__ import annotations

import itertools
import re
import struct
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import yaml
from django.conf import settings

from server.game import get_game

THEMES = settings.BASE_DIR / "server" / "static" / "themes"
THIRD_PARTY = settings.BASE_DIR / "THIRD_PARTY.md"
MAX_BYTES = 100_000
FORMATS = {".svg", ".png", ".webp"}
SVG_FORBIDDEN = {"script", "foreignObject", "iframe", "audio", "video"}


def _themes() -> list[Path]:
    return sorted(p for p in THEMES.iterdir() if (p / "theme.yaml").exists())


def _load(theme_dir: Path) -> dict:
    with (theme_dir / "theme.yaml").open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _entries(theme: dict, field: str = "sprites") -> dict[str, dict]:
    return {
        key: spec if isinstance(spec, dict) else {"file": spec}
        for key, spec in (theme.get(field) or {}).items()
    }


def _capital_combos() -> set[str]:
    """首都の建物の組み合わせ（軸ごとに体制の建物を 1 つずつ、軸の順に "-" でつなぐ）。"""
    d = get_game().defs
    per_axis = [
        sorted(
            {
                r["capital_building"]
                for r in d.regimes.values()
                if r["axis"] == axis and r.get("capital_building")
            }
        )
        for axis in d.regime_axes()
    ]
    return {"-".join(combo) for combo in itertools.product(*[b for b in per_axis if b])}


def _allowed_keys() -> set[str]:
    d = get_game().defs
    sprites = {f.get("sprite", fid) for fid, f in d.facilities.items() if not f.get("tileless")}
    coastal = {f.get("sprite", fid) for fid, f in d.facilities.items() if f.get("coastal")}
    return (
        {f"terrain/{t}" for t in d.terrain}
        | {f"facility/{s}" for s in sprites}
        | {f"facility/{s}@{side}" for s in coastal for side in ("sw", "ne", "nw")}
        | {f"capital/{s}" for s in sprites}
        | {f"capital/{c}" for c in _capital_combos()}
        | {f"housing/{s}" for s in d.settlements}
        | {"construction"}
        | {
            "overlay/trench_back",
            "overlay/trench_front",
            "overlay/occupied",
            "overlay/contaminated",
            "unit/rebels",  # 反乱軍（部隊の一覧に入らない。4-4）
        }
        | {f"unit/{a}" for a, spec in d.army.items() if spec["arm"] == "land"}
        | {f"fleet/{a}" for a, spec in d.army.items() if spec["arm"] == "sea"}
    )


def _length(value: str | None) -> float | None:
    """SVG の width / height。単位なしか px だけを認める。"""
    if value is None:
        return None
    m = re.fullmatch(r"\s*([0-9]+(?:\.[0-9]+)?)\s*(px)?\s*", value)
    return float(m.group(1)) if m else None


def _image_size(path: Path) -> tuple[float, float]:
    if path.suffix == ".svg":
        root = ET.parse(path).getroot()
        w, h = _length(root.get("width")), _length(root.get("height"))
        assert w and h, f"{path.name}: SVG に数値の width と height を書いてください"
        return w, h
    data = path.read_bytes()
    if path.suffix == ".png":
        assert data[:8] == b"\x89PNG\r\n\x1a\n", f"{path.name}: PNG ではありません"
        return struct.unpack(">II", data[16:24])
    assert data[:4] == b"RIFF" and data[8:12] == b"WEBP", f"{path.name}: WebP ではありません"
    pytest.skip("WebP の大きさは調べていません")


def _check_svg(path: Path) -> None:
    root = ET.parse(path).getroot()
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        assert tag not in SVG_FORBIDDEN, f"{path.name}: <{tag}> は使えません"
        for name, value in el.attrib.items():
            attr = name.rsplit("}", 1)[-1]
            assert not attr.lower().startswith("on"), f"{path.name}: {attr} 属性は使えません"
            if attr == "href":
                assert value.startswith(("#", "data:image/")), (
                    f"{path.name}: 外のファイルを参照しないでください（{value[:40]}）"
                )
    text = path.read_text(encoding="utf-8")
    assert "@import" not in text and "url(http" not in text, f"{path.name}: 外の参照があります"


@pytest.mark.parametrize("theme_dir", _themes(), ids=lambda p: p.name)
def test_sprite_entries(theme_dir: Path) -> None:
    theme = _load(theme_dir)
    entries = _entries(theme)
    # 使う施設や描く仕組みがまだ入っていない絵（先に置いたもの）。名前の一覧との照合だけ省く
    pending = _entries(theme, "pending_sprites")
    assert not entries.keys() & pending.keys(), "sprites と pending_sprites に同じ名前があります"
    allowed = _allowed_keys()
    third_party = THIRD_PARTY.read_text(encoding="utf-8")
    for key, spec in {**entries, **pending}.items():
        scale = spec.get("scale", theme.get("sprite_scale", 1))
        if key not in pending:
            assert key in allowed, f"{key}: 使える名前ではありません（docs/map-art.md の一覧）"
        path = (theme_dir / spec["file"]).resolve()
        assert path.is_relative_to(theme_dir.resolve()), f"{key}: テーマのフォルダの外です"
        assert path.exists(), f"{key}: {spec['file']} がありません"
        assert path.suffix in FORMATS, f"{key}: 形式は {sorted(FORMATS)} のどれかにしてください"
        assert path.stat().st_size <= MAX_BYTES, f"{key}: {MAX_BYTES} バイト以下にしてください"
        if path.suffix == ".svg":
            _check_svg(path)
        w, h = (v * scale for v in _image_size(path))
        if key.startswith("terrain/") and "anchor" not in spec:
            assert w == theme["tile_width"], f"{key}: 地形の絵の幅はマスの幅にしてください"
        assert w <= theme["tile_width"] * 2, f"{key}: 幅が広すぎます"
        if "anchor" in spec:
            ax, ay = (v * scale for v in spec["anchor"])
            assert 0 <= ax <= w and 0 <= ay <= h, f"{key}: 基準点が絵の外にあります"
        # ファイルそのものか、それを含むテーマの中のフォルダが THIRD_PARTY.md に載っていること
        names = [path, *(p for p in path.parents if p.is_relative_to(theme_dir.resolve()))]
        rels = [p.relative_to(settings.BASE_DIR.resolve()).as_posix() for p in names]
        assert any(r in third_party for r in rels), (
            f"{key}: THIRD_PARTY.md に {rels[0]}（かそのフォルダ）の出典とライセンスがありません"
        )


@pytest.mark.parametrize("theme_dir", _themes(), ids=lambda p: p.name)
def test_no_unlisted_sprite_files(theme_dir: Path) -> None:
    """テーマのフォルダに置いた絵は、どれも theme.yaml の sprites から使われていること。"""
    theme = _load(theme_dir)
    specs = [*_entries(theme).values(), *_entries(theme, "pending_sprites").values()]
    listed = {(theme_dir / s["file"]).resolve() for s in specs}
    for path in theme_dir.rglob("*"):
        if path.suffix in FORMATS:
            assert path.resolve() in listed, f"{path.name}: theme.yaml の sprites に載っていません"
