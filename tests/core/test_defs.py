from __future__ import annotations

import pytest

from hakoniwa_core.config import default_config_path, load_config
from hakoniwa_core.defs import DefsError, load_defs
from tests.conftest import write


def load(path):
    return load_defs(load_config(path / "game.yaml"))


def test_base_pack_loads():
    defs = load_defs(load_config(default_config_path()))
    assert "steel_mill" in defs.facilities
    assert defs.name("facilities", "steel_mill") == "製鉄所"
    # 量は 1000 分の 1 単位の整数に直る
    assert defs.facilities["steel_mill"]["recipes"]["default"]["inputs"]["iron_ore"] == 10_000
    assert defs.pack_versions() == {"base": "0.0.1"}


def test_defs_are_read_only():
    defs = load_defs(load_config(default_config_path()))
    with pytest.raises(TypeError):
        defs.goods["steel"]["tier"] = 9  # type: ignore[index]


def test_schema_error_shows_file_and_line(game_dir):
    path = game_dir()
    write(
        path / "packs/base/data/goods.yaml",
        """\
        grain: {tier: 1}
        steel:
          tier: 7
        """,
    )
    with pytest.raises(DefsError) as e:
        load(path)
    messages = [str(i) for i in e.value.issues]
    assert any("goods.yaml:3:" in m and "steel/tier" in m for m in messages), messages


def test_cross_reference_error(game_dir):
    path = game_dir()
    facilities = path / "packs/base/data/facilities.yaml"
    facilities.write_text(
        facilities.read_text(encoding="utf-8").replace("outputs: {steel: 5}", "outputs: {stel: 5}"),
        encoding="utf-8",
    )
    with pytest.raises(DefsError) as e:
        load(path)
    assert any("財 stel は定義されていません" in str(i) for i in e.value.issues)
    assert all(i.line is not None for i in e.value.issues)


def test_missing_locale_name(game_dir):
    path = game_dir()
    write(path / "packs/base/data/terrain.yaml", "sea: {land: false}\nlava: {land: true}\n")
    with pytest.raises(DefsError) as e:
        load(path)
    assert any("terrain.lava の表示名" in str(i) for i in e.value.issues)


def test_unknown_data_file(game_dir):
    path = game_dir()
    write(path / "packs/base/data/spaceships.yaml", "x: {}\n")
    with pytest.raises(DefsError, match="知らない種類"):
        load(path)


def test_addon_pack_adds_definitions(game_dir):
    path = game_dir(packs=["sugar"])
    write(path / "packs/sugar/pack.yaml", "name: sugar\nversion: 1.0\n")
    write(path / "packs/sugar/data/goods.yaml", "sugar: {tier: 2}\n")
    write(path / "packs/sugar/locale/ja/goods.yaml", "sugar: 砂糖\n")
    defs = load(path)
    assert defs.name("goods", "sugar") == "砂糖"
    assert [p.name for p in defs.packs] == ["base", "sugar"]


def test_name_collision_is_error(game_dir):
    path = game_dir(packs=["dup"])
    write(path / "packs/dup/pack.yaml", "name: dup\nversion: 1\n")
    write(path / "packs/dup/data/goods.yaml", "steel: {tier: 2}\n")
    with pytest.raises(DefsError, match="パック base でも定義されています"):
        load(path)


def test_override_changes_numbers_only(game_dir):
    path = game_dir(packs=["tweak"])
    write(path / "packs/tweak/pack.yaml", "name: tweak\nversion: 1\n")
    write(
        path / "packs/tweak/data/facilities.yaml",
        """\
        steel_mill:
          override: true
          workers: {general: 899}
          recipes: {default: {outputs: {steel: 7.5}}}
        """,
    )
    defs = load(path)
    mill = defs.facilities["steel_mill"]
    assert mill["workers"] == 999  # 一般 899 と、書かなかった技術者 100
    assert mill["staff"] == {"general": 899, "engineer": 100}
    assert mill["recipes"]["default"]["outputs"]["steel"] == 7500
    assert mill["recipes"]["default"]["inputs"]["iron_ore"] == 10_000  # 書かなかった所は残る

    write(path / "packs/tweak/data/facilities.yaml", "steel_mill: {override: true, category: x}\n")
    with pytest.raises(DefsError, match="数値だけ"):
        load(path)


def test_pack_order_follows_requirements(game_dir):
    path = game_dir(packs=["a_user", "z_lib"])
    write(path / "packs/a_user/pack.yaml", "name: a_user\nversion: 1\nrequires: [z_lib]\n")
    write(path / "packs/z_lib/pack.yaml", "name: z_lib\nversion: 1\n")
    write(path / "packs/z_lib/data/goods.yaml", "sugar: {tier: 2}\n")
    write(path / "packs/z_lib/locale/ja/goods.yaml", "sugar: 砂糖\n")
    write(path / "packs/a_user/data/goods.yaml", "sugar: {override: true, tier: 3}\n")
    defs = load(path)
    assert [p.name for p in defs.packs] == ["base", "z_lib", "a_user"]
    assert defs.goods["sugar"]["tier"] == 3


def test_missing_required_pack(game_dir):
    path = game_dir(packs=["needy"])
    write(path / "packs/needy/pack.yaml", "name: needy\nversion: 1\nrequires: [ghost]\n")
    with pytest.raises(DefsError, match="needy が必要とするパック ghost"):
        load(path)


def test_pack_cycle(game_dir):
    path = game_dir(packs=["p1"])
    write(path / "packs/p1/pack.yaml", "name: p1\nversion: 1\nrequires: [p2]\n")
    write(path / "packs/p2/pack.yaml", "name: p2\nversion: 1\nrequires: [p1]\n")
    with pytest.raises(DefsError, match="循環"):
        load(path)


@pytest.mark.parametrize(
    ("secret", "message"),
    [
        ("{sid: buy}", "payload に sid がありません"),
        ("{side: hold}", "'hold' は side の値になりません"),
    ],
)
def test_order_secret_condition_must_match_the_payload(game_dir, secret, message):
    """中身で伏せる命令の条件（secret: {項目: 値}）は、payload にある項目と、その形に合う値。"""
    path = game_dir()
    orders = path / "packs/base/data/orders.yaml"
    text = orders.read_text(encoding="utf-8")
    assert "  secret: {side: buy}\n" in text
    orders.write_text(text.replace("  secret: {side: buy}\n", f"  secret: {secret}\n"), "utf-8")
    with pytest.raises(DefsError) as e:
        load(path)
    assert any(message in str(i) for i in e.value.issues), e.value.issues
