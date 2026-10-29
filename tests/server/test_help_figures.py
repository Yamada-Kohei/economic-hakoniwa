"""手引きの図（SVG）の確かめ。図は tools/help_diagrams が作り、手引きが <img> で読む。"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FIGURES = ROOT / "server" / "static" / "img" / "help"


def _generator():
    spec = importlib.util.spec_from_file_location(
        "help_diagrams", ROOT / "tools" / "help_diagrams" / "diagrams.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_committed_figures_match_the_generator():
    """図の文字を直したら、作り直した SVG も一緒に入れる。"""
    for name, build in _generator().DIAGRAMS.items():
        assert (FIGURES / f"{name}.svg").read_text(encoding="utf-8") == build().render(), name


@pytest.mark.django_db
def test_both_guides_show_their_figures_with_a_text_version(client, world):
    """図は文字でも読める（スクリーンリーダーと、HTML を文字にして読む AI の入り口のため）。"""
    general = client.get("/help/").content.decode()
    hakoniwa = client.get("/help/hakoniwa/").content.decode()
    used = set(re.findall(r"/static/(?:v-[\w.-]+/)?img/help/([a-z-]+)\.svg", general))
    assert used == set(_generator().DIAGRAMS)
    assert {"overview", "town-stages"} <= set(re.findall(r"img/help/([a-z-]+)\.svg", hakoniwa))
    for name in used:
        assert (FIGURES / f"{name}.svg").is_file()
    assert general.count("図を文字で読む") == len(used)
    assert "賃金が先なので、同じ月に食べ物が買えます" in general  # 1か月の流れの文字の版
    assert 'href="#overview"' in general


@pytest.mark.django_db
def test_figure_sizes_in_the_guides_match_the_svg(client, world):
    """<img> の幅と高さが SVG の viewBox と同じなら、場所が先に決まり、縦横比が崩れない。"""
    for url in ("/help/", "/help/hakoniwa/"):
        page = client.get(url).content.decode()
        for src, width, height in re.findall(
            r'src="/static/(?:v-[\w.-]+/)?img/help/([a-z-]+)\.svg" width="(\d+)" height="(\d+)"',
            page,
        ):
            svg = (FIGURES / f"{src}.svg").read_text(encoding="utf-8")
            assert f'viewBox="0 0 {width} {height}"' in svg, (url, src)
