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


@pytest.mark.django_db
def test_food_table_comes_from_the_goods_data(client, world):
    """食べ物の表は品目の定義（何人前・質・日持ち）から出す。質の低い順に並ぶ。"""
    page = client.get("/help/").content.decode()
    section = page[page.index('id="food"') : page.index("自動車（耐久消費財）")]
    cell = r"</t[hd]><td>([\d.]+)"
    rows = re.findall(rf"<tr><th>([^<]+){cell}{cell}{cell}</td></tr>", section)
    assert rows == [
        ("穀物", "1", "0.2", "24"),
        ("魚", "1", "0.4", "2"),
        ("肉", "1.2", "0.8", "3"),
        ("食品", "1.5", "1", "6"),
    ]


@pytest.mark.django_db
def test_diet_shares_come_from_the_food_target(client, world):
    """家計の献立の構成比（LB-20）は household.food_target から出す。標準の所得は base、
    food_target_rich_income 倍は rich、その平方根の倍は両者のちょうど中間（倍率の log の割合）。"""
    from server.game import get_game

    config = get_game().config
    target = config.get("household.food_target")
    rich = config.get("household.food_target_rich_income")
    page = client.get("/help/").content.decode()
    section = page[page.index('id="diet"') : page.index('id="clothing"')]
    table = section[section.index("diet-table") :]
    rows = re.findall(r"<tr><th>([^<]+)</th>((?:<td>[\d.]+</td>)+)</tr>", table)
    shares = [[float(v) for v in re.findall(r"<td>([\d.]+)</td>", cells)] for _, cells in rows]
    order = ["grain", "fish", "meat", "food"]  # 質の低い順（食べ物の表と同じ）
    labels = ["標準の所得", f"標準の {rich**0.5:g} 倍", f"標準の {rich:g} 倍"]
    assert [label for label, _ in rows] == labels
    assert shares[0] == pytest.approx([target["base"][g] * 100 for g in order], abs=0.05)
    assert shares[2] == pytest.approx([target["rich"][g] * 100 for g in order], abs=0.05)
    middle = [(target["base"][g] + target["rich"][g]) / 2 * 100 for g in order]
    assert shares[1] == pytest.approx(middle, abs=0.05)
    assert "img/help/diet-steps.svg" in section and "img/help/diet-shortage.svg" in section
