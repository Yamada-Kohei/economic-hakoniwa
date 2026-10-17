"""試し遊び（Codex の世界、ターン250〜300）で見つかった表示と道具の点と、AI 用の入り口で読めなかった
情報（飢えている層・サービスの店・人口の増減のわけ・町ごとの働く人・作物替えの畑）。"""

from __future__ import annotations

import json
from types import SimpleNamespace

from hakoniwa_core import facilities as fac
from hakoniwa_core.economy import GENERAL
from hakoniwa_core.state import Facility
from server.accounts import keys
from server.web import display, reports
from server.world.models import Island
from tests.server.test_ai_players import _hk, _keyed, _tool
from tests.server.test_ai_players import played as played  # noqa: F401  （試験の部品を使い回す）


def _starving(island: Island) -> None:
    """一般の層が、お金がなくて食べ物を買えず飢えている形にする（人口の段が残すものと同じ形）。"""
    economy = island.economy
    general = economy["households"][GENERAL]
    general.update(
        {"fed": 600, "hunger": 1.5, "food_short": "money", "food_buyers": 1234, "hunger_risk": 0.05}
    )
    island.economy = economy
    island.save(update_fields=["economy"])


def test_summary_and_island_screen_tell_who_is_hungry_and_why(client, played):
    """国の要約（JSON・MCP）と島の画面の「注意」に、飢えている層・人数・食べられた割合・買えなかった
    わけ・亡くなるまでの危うさを同じ文で出す。サービスの店と人口の増減のわけも要約に入れる。"""
    world, (alice, bob, _), (a, _, _) = played
    _starving(a)
    _, token = keys.issue(alice)
    data = _keyed(token).get(f"/api/islands/{a.id}/summary").json()
    row = next(r for r in data["hunger"] if r["layer"] == GENERAL)
    assert row["persons"] == 1234 and row["fed"] == 0.6 and row["reason"] == "money"
    assert row["at_risk"] == 0.05 and row["months"] == 1.5
    alert = next(x for x in data["alerts"] if "飢えています" in x)
    assert "1,234 人" in alert and "60%" in alert and "お金が足りず買えません" in alert
    assert "5.0% が持ちこたえられません" in alert
    assert {"births", "deaths", "starved", "arrived", "left", "labels"} <= set(
        data["population_changes"]
    )
    assert "services" in data
    if data["services"]:
        assert {"workers", "workers_text"} <= set(data["services"])
    # ほかの国の人にも同じ注意を出す（画面の値を言葉にしただけ）。MCP の要約も同じ
    _, other = keys.issue(bob)
    seen = _keyed(other).get(f"/api/islands/{a.id}/summary").json()
    assert alert in seen["alerts"]
    text, error = _tool(_keyed(token), "summary")
    assert not error and json.loads(text)["hunger"] == data["hunger"]
    page = client.get(f"/islands/{a.id}/").content.decode()
    assert "注意" in page and "お金が足りず買えません" in page


def test_food_ratio_and_jobless_layer_names():
    """99.9% を 100% と出さない（足りていないのに満ちて見えた）。失業している家計の層にも名前が
    ある。"""
    assert display.stat_text("food.ratio", 0.9994, "%") == "99.9%"
    assert display.stat_text("food.ratio", 1.0, "%") == "100%"
    assert display.stat_text("unemployment", 0.2504, "%") == "25%"
    label, _ = reports.stat_label("layer.jobless.persons")
    assert label.startswith("失業している労働者") and "jobless" not in label


def test_series_tells_what_it_could_not_return(played, monkeypatch):
    """統計の系列の上限を超えた分は黙って切らず、名前を dropped に返す。選べる名前の数を超えた分も
    not_read に返す。"""
    world, _, (a, _, _) = played
    monkeypatch.setattr(reports, "MAX_SERIES", 2)
    data = reports.series(a, ["gdp", "cpi", "population"], 0, world.turn, True)
    assert list(data["series"]) == ["gdp", "cpi"] and data["dropped"] == ["population"]
    assert data["asked"] == 3 and data["found"] == 3
    assert data["limits"] == {"names": reports.MAX_NAMES, "series": 2}
    monkeypatch.setattr(reports, "MAX_NAMES", 1)
    data = reports.series(a, ["gdp", "cpi"], 0, world.turn, True)
    assert data["not_read"] == ["cpi"] and list(data["series"]) == ["gdp"]


def test_facilities_show_workers_by_town_to_everyone(client, played):
    """施設の画面と API に、町ごとの働ける人・自給の農業・失業と、買いに行く店の場所を出す
    （誰にでも。地図と同じ中身）。"""
    _, (_, bob, _), (a, _, _) = played
    _, other = keys.issue(bob)
    data = _keyed(other).get(f"/api/islands/{a.id}/facilities").json()
    towns = data["towns"]
    assert towns and towns == sorted(towns, key=lambda t: -t["population"])
    for t in towns:
        assert t["labor"] >= t["employed"] >= 0 and t["jobless"] >= 0
        assert 0 <= t["unemployment"] <= 1 and {"x", "y"} == set(t["center"])
    assert sum(t["population"] for t in towns) == sum(
        tile.population for tile in a.tiles.all() if tile.population > 0
    )
    page = client.get(f"/islands/{a.id}/facilities/").content.decode()
    assert "町ごとの働く人" in page and towns[0]["place"] in page


def test_farm_changing_crops_shows_its_fields():
    """作物を替えている途中の農場は、前の作物の畑（割合・要る原料）と蒔いた畑（収穫の始まる月）を出す。"""
    farm = Facility(id=1, kind="farm", x=3, y=3, recipe="default", built=True)
    farm.economy[fac.FIELDS] = {"cotton_fertilized": 500, "default": 200}
    farm.economy[fac.SOWN] = [["default", 300, 12]]
    shown = display.crop_transition(farm)
    assert [x["recipe"] for x in shown["fields"]] == ["cotton_fertilized"]
    assert shown["fields"][0]["share"] == 0.5
    assert [g["good"] for g in shown["fields"][0]["inputs"]] == ["chemicals"]
    assert shown["sown"][0]["ready_turn"] == 12 and shown["ready_turn"] == 12
    assert "50%" in shown["text"] and "30%" in shown["text"]


def test_hk_json_remembers_the_turn(tmp_path, monkeypatch):
    """hk.py の summary --json と changes --json も、読んだターンを覚える（次の changes の
    既定）。"""
    hk = _hk()
    monkeypatch.setattr(hk, "STATE", tmp_path / "state.json")
    asked = []

    def get(path):
        asked.append(path)
        return 200, json.dumps({"turn": 7}), {}

    c = SimpleNamespace(base="http://hakoniwa.test", get=get)
    assert hk.cmd_summary(c, SimpleNamespace(island=4, json=True)) == 0
    assert hk.load_state() == {"http://hakoniwa.test|4": 7}
    assert hk.cmd_changes(c, SimpleNamespace(island=4, json=True, since=None)) == 0
    assert asked[-1] == "/api/islands/4/changes?since=7"
