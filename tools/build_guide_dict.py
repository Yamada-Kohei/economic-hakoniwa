"""案内係フェーズ1の「仕様の辞書」と「相談メモ」を生成する道具（掲示板 #5248・#5259）。

packs の定義データ（goods.yaml・facilities.yaml）と locale から辞書を作り、
手書きの注記（tools/guide_notes.yaml）と相談メモの原稿（tools/guide_memos.yaml）を
合成して docs/guide/ に出力します。docs/guide/ は生成物なので手で直さず、
定義や注記を直してから作り直してください。

    python3 tools/build_guide_dict.py           # docs/guide/ を作り直す
    python3 tools/build_guide_dict.py --check   # 古くなっていたら終わりの値 1

決まり:
- 注記は財・施設の id をキーにし、未知 id・重複 id があると生成は失敗します。
- 施設の範囲は guide_notes.yaml の facility_scope（よく聞く施設の固定の一覧）だけ。
  施設の全部は次の枝で広げます。
- 相談メモは1枚ごとに「質問・答え・根拠（ファイルと項目）」を必須とし、
  根拠のファイルが実在しないメモがあると生成は失敗します。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
OUT_DIR = Path("docs/guide")

GOODS_DEF = "packs/base/data/goods.yaml"
FACILITIES_DEF = "packs/base/data/facilities.yaml"
LOCALE_DIR = "packs/base/locale/ja"

TIER_NAMES = {1: "原料", 2: "中間財", 3: "最終財"}
NEED_NAMES = {
    "necessity": "必需品",
    "luxury": "ぜいたく品",
    "durable": "耐久消費財（ぜいたく品の種類にも数える）",
    "service": "サービス（ぜいたく品の種類にも数える）",
    "retail": "小売り（店の売り場の働き）",
}
# hakoniwa_core/facilities.py の DEFAULT_MAX_SIZE（max_size を書かない施設の上限）
DEFAULT_MAX_SIZE = 10


class GuideError(Exception):
    """生成を止める入力の誤り。"""


class UniqueKeyLoader(yaml.SafeLoader):
    """同じキーが2回出たら失敗する YAML の読み込み（注記の重複 id 検出用）。"""


def _unique_mapping(loader: UniqueKeyLoader, node: yaml.MappingNode) -> dict:
    mapping: dict = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if key in mapping:
            raise GuideError(f"キーが重複しています: {key!r}（{node.start_mark}）")
        mapping[key] = loader.construct_object(value_node, deep=True)
    return mapping


UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def load_yaml(path: Path, *, unique: bool = False) -> dict:
    loader = UniqueKeyLoader if unique else yaml.SafeLoader
    with path.open(encoding="utf-8") as f:
        data = yaml.load(f, Loader=loader)
    if not isinstance(data, dict):
        raise GuideError(f"{path} の中身が辞書ではありません")
    return data


def tidy(text: str | None) -> str | None:
    """YAML の折り畳みで入る和文中の空白を落とす。"""
    if text is None:
        return None
    return re.sub(r"(?<=[^\x00-\x7f]) (?=[^\x00-\x7f])", "", text)


def fmt_num(value: object) -> str:
    """数値の表示（整数は整数らしく、小数はそのまま）。"""
    if isinstance(value, bool):
        return "はい" if value else "いいえ"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def goods_points(gdef: dict) -> list[str]:
    """定義の項目から「誤解しやすい点」を機械で組み立てる。"""
    points: list[str] = []
    scale = gdef.get("display_scale", 1)
    if scale != 1:
        points.append(
            f"画面の量は内部の量の{fmt_num(scale)}倍で表示される。"
            f"1単位あたりの値段は{fmt_num(scale)}分の1になり、お金の額は変わらない"
        )
    else:
        points.append("画面の量は内部の量そのまま（倍率の表示は付かない）")
    if "shelf_life" not in gdef:
        points.append("持ち越しの期限は無い（shelf_life を書かない＝無期限）")
    elif gdef["shelf_life"] == 0:
        points.append("そのターンに使わなかった分は消える。貯めておけない（shelf_life 0）")
    else:
        points.append(f"持ち越せるのは{fmt_num(gdef['shelf_life'])}ターンまで（shelf_life）")
    if "freight_weight" not in gdef:
        points.append("船で運べない。島の外へ送れない（freight_weight が無い）")
    elif gdef["freight_weight"] == 0:
        points.append("自分で航海して届く物。運ぶ重さとしては数えない（freight_weight 0）")
    else:
        points.append(
            f"船で運ぶとき、1単位を{fmt_num(gdef['freight_weight'])}トンとして積載量を数える"
        )
    food = gdef.get("food")
    if food:
        points.append(
            f"住民が食べられる。1単位で{fmt_num(food.get('nutrition'))}人前、"
            f"食事の質は{fmt_num(food.get('quality'))}（0〜1）"
        )
    need = gdef.get("need")
    if need:
        points.append(f"家計にとっての種類は{NEED_NAMES.get(need, need)}（need: {need}）")
    if gdef.get("consumer"):
        points.append("家計が買う財で、物価指数に入る（consumer）")
    if gdef.get("mining"):
        points.append("建国時に選べる地下資源（mining）")
    if gdef.get("arms"):
        points.append(
            "軍事品。貿易圏の市場には出さず、国どうしの契約と物の援助でだけ動く。"
            "買うのは国だけ（arms）"
        )
    if gdef.get("vein_only"):
        points.append("限られた島の鉱脈でだけ採れる（vein_only）")
    if "fx_priority" in gdef:
        points.append(
            f"計画経済の国営商社が少ない外貨を使う順は{fmt_num(gdef['fx_priority'])}（1が先）"
        )
    return points


def build_goods(repo: Path, notes: dict) -> list[dict]:
    goods = load_yaml(repo / GOODS_DEF)
    names = load_yaml(repo / LOCALE_DIR / "goods.yaml")
    units = load_yaml(repo / LOCALE_DIR / "units.yaml")
    entries = []
    for gid, gdef in goods.items():
        entry = {
            "id": gid,
            "name": names.get(gid, gid),
            "tier": gdef.get("tier"),
            "tier_name": TIER_NAMES.get(gdef.get("tier"), ""),
            "unit": units.get(gdef.get("unit"), gdef.get("unit")),
            "unit_key": gdef.get("unit"),
            "definition": gdef,
            "points": goods_points(gdef),
            "note": tidy(notes.get(gid, {}).get("note")),
            "source": f"{GOODS_DEF} の {gid}",
        }
        entries.append(entry)
    return entries


def recipe_lines(fdef: dict, names_goods: dict, recipe_names: dict, fid: str) -> list[dict]:
    recipes = []
    for rname, rdef in (fdef.get("recipes") or {}).items():
        recipe = {
            "id": rname,
            "name": recipe_names.get(f"{fid}_{rname}", rname),
            "inputs": {
                names_goods.get(g, g): fmt_num(v) for g, v in (rdef.get("inputs") or {}).items()
            },
            "outputs": {
                names_goods.get(g, g): fmt_num(v) for g, v in (rdef.get("outputs") or {}).items()
            },
        }
        if "grow_turns" in rdef:
            recipe["grow_turns"] = rdef["grow_turns"]
        recipes.append(recipe)
    return recipes


def build_facilities(repo: Path, notes: dict, scope: list[str]) -> list[dict]:
    facilities = load_yaml(repo / FACILITIES_DEF)
    names = load_yaml(repo / LOCALE_DIR / "facilities.yaml")
    names_goods = load_yaml(repo / LOCALE_DIR / "goods.yaml")
    industries = load_yaml(repo / LOCALE_DIR / "industries.yaml")
    recipe_names = load_yaml(repo / LOCALE_DIR / "recipes.yaml")
    terrains = load_yaml(repo / LOCALE_DIR / "terrain.yaml")
    worker_names = load_yaml(repo / LOCALE_DIR / "workers.yaml")
    entries = []
    for fid in scope:
        fdef = facilities[fid]
        construction = fdef.get("construction") or {}
        workers = fdef.get("workers")
        if isinstance(workers, dict):
            workers_text = "・".join(
                f"{worker_names.get(k, k)} {fmt_num(v)}人" for k, v in workers.items()
            )
        elif workers is None:
            workers_text = None
        else:
            workers_text = f"{fmt_num(workers)}人（一般の労働者）"
        entry = {
            "id": fid,
            "name": names.get(fid, fid),
            "category": fdef.get("category"),
            "industry": industries.get(fdef.get("industry"), fdef.get("industry")),
            "terrain": [terrains.get(t, t) for t in fdef.get("terrain") or []],
            "construction": {
                "turns": construction.get("turns"),
                "labor": construction.get("labor"),
                "materials": {
                    names_goods.get(g, g): fmt_num(v)
                    for g, v in (construction.get("materials") or {}).items()
                },
            },
            "workers": workers_text,
            "max_size": fdef.get("max_size", DEFAULT_MAX_SIZE),
            "max_size_from_code": "max_size" not in fdef,
            "recipes": recipe_lines(fdef, names_goods, recipe_names, fid),
            "grades": [g.get("name") for g in fdef.get("grades") or []],
            "pollution": fdef.get("pollution"),
            "water": fdef.get("water"),
            "public_service": bool(fdef.get("public_service")),
            "note": tidy(notes.get(fid, {}).get("note")),
            "source": f"{FACILITIES_DEF} の {fid}",
        }
        entries.append(entry)
    return entries


def validate(repo: Path, notes_doc: dict, memos_doc: dict) -> tuple[list[str], dict, dict]:
    goods = load_yaml(repo / GOODS_DEF)
    facilities = load_yaml(repo / FACILITIES_DEF)
    scope = notes_doc.get("facility_scope")
    if not isinstance(scope, list) or not scope:
        raise GuideError("guide_notes.yaml に facility_scope（施設の id の一覧）がありません")
    for fid in scope:
        if fid not in facilities:
            raise GuideError(f"facility_scope に定義の無い施設があります: {fid}")
    if len(set(scope)) != len(scope):
        raise GuideError("facility_scope に重複があります")
    notes_goods = notes_doc.get("goods") or {}
    for gid in notes_goods:
        if gid not in goods:
            raise GuideError(f"注記に定義の無い財があります: {gid}")
    notes_facilities = notes_doc.get("facilities") or {}
    for fid in notes_facilities:
        if fid not in facilities:
            raise GuideError(f"注記に定義の無い施設があります: {fid}")
        if fid not in scope:
            raise GuideError(f"注記が facility_scope の外の施設です: {fid}")
    memos = memos_doc.get("memos")
    if not isinstance(memos, list) or not memos:
        raise GuideError("guide_memos.yaml に memos がありません")
    seen: set[str] = set()
    for memo in memos:
        for key in ("id", "question", "answer", "basis"):
            if not memo.get(key):
                raise GuideError(f"相談メモ {memo.get('id')!r} に {key} がありません")
        if memo["id"] in seen:
            raise GuideError(f"相談メモの id が重複しています: {memo['id']}")
        seen.add(memo["id"])
        for basis in memo["basis"]:
            file_part = basis.split()[0]
            if not (repo / file_part).exists():
                raise GuideError(f"相談メモ {memo['id']} の根拠のファイルがありません: {file_part}")
    return scope, notes_goods, notes_facilities


def render_goods_md(entries: list[dict]) -> list[str]:
    lines = ["## 財の辞書", ""]
    for e in entries:
        lines.append(f"### {e['name']}（{e['id']}）")
        lines.append("")
        lines.append(f"- 段: {fmt_num(e['tier'])}（{e['tier_name']}）")
        lines.append(f"- 単位: {e['unit']}")
        lines.append("- 誤解しやすい点:")
        for point in e["points"]:
            lines.append(f"  - {point}")
        if e["note"]:
            lines.append(f"- 注記: {e['note']}")
        lines.append(f"- 出典: {e['source']}／表示名は {LOCALE_DIR}/goods.yaml")
        lines.append("")
    return lines


def render_facilities_md(entries: list[dict]) -> list[str]:
    lines = ["## 施設の辞書（よく聞く施設）", ""]
    for e in entries:
        lines.append(f"### {e['name']}（{e['id']}）")
        lines.append("")
        lines.append(f"- 分類: {e['category']}／産業: {e['industry']}")
        if e["terrain"]:
            lines.append(f"- 建てられる地形: {'・'.join(e['terrain'])}")
        c = e["construction"]
        materials = "・".join(f"{g} {v}" for g, v in c["materials"].items()) or "なし"
        lines.append(
            f"- 建設: {fmt_num(c['turns'])}ターン、労働 {fmt_num(c['labor'])}人、資材 {materials}"
        )
        if e["workers"]:
            lines.append(f"- 働き手（全力のとき）: {e['workers']}")
        max_size_note = (
            "（定義に書かれていないので hakoniwa_core/facilities.py の DEFAULT_MAX_SIZE）"
            if e["max_size_from_code"]
            else ""
        )
        lines.append(f"- 1マスに建て増せる規模の上限: {fmt_num(e['max_size'])}{max_size_note}")
        for r in e["recipes"]:
            inputs = "・".join(f"{g} {v}" for g, v in r["inputs"].items()) or "なし"
            outputs = "・".join(f"{g} {v}" for g, v in r["outputs"].items())
            grow = (
                f"、蒔いてから収穫まで{fmt_num(r['grow_turns'])}ターン" if "grow_turns" in r else ""
            )
            lines.append(f"- 生産方式「{r['name']}」: 入り {inputs} → 出 {outputs}{grow}")
        if e["grades"]:
            lines.append(f"- 段階: {'・'.join(e['grades'])}（読み込み時に作り方が足される）")
        if e["pollution"]:
            lines.append(
                f"- 汚染: 周り{fmt_num(e['pollution'].get('radius'))}マスに"
                f"{fmt_num(e['pollution'].get('amount'))}"
            )
        if e["water"]:
            w = e["water"]
            lines.append(
                f"- 水: 周り{fmt_num(w.get('radius'))}マスの地形から集める"
                f"（満水 {fmt_num(w.get('full'))}、産出の下限の割合 {fmt_num(w.get('min'))}）"
            )
        if e["public_service"]:
            lines.append("- 公務員の施設。賃金は国庫が払う")
        if e["note"]:
            lines.append(f"- 注記: {e['note']}")
        lines.append(f"- 出典: {e['source']}／表示名は {LOCALE_DIR}/facilities.yaml")
        lines.append("")
    return lines


README_TEXT = """# 案内係の辞書と相談メモ（生成物）

このフォルダの中身は**生成物**です。手で直さないでください。
直すときは定義（packs/）・注記（tools/guide_notes.yaml）・メモの原稿
（tools/guide_memos.yaml）を直してから、次のコマンドで作り直します。

    python3 tools/build_guide_dict.py

- dictionary.md / dictionary.json: 仕様の辞書（財の全部と、よく聞く施設）。
- memos.md: 相談メモ（1枚ごとに質問・答え・根拠）。
- 注記は財・施設の id をキーにし、未知 id・重複 id があると生成は失敗します。
- 施設の範囲は最初の枝では「よく聞く施設」の id の一覧で固定しています
  （tools/guide_notes.yaml の facility_scope）。施設の全部は次の枝で広げます。
- 数値は定義データから引いた「定義上の値」です。根拠がコード側にあるものは、
  項目の中に出典（ファイルと関数名）を書きます。
"""


def render_dictionary_md(goods: list[dict], facilities: list[dict]) -> str:
    lines = [
        "# 仕様の辞書（案内係フェーズ1）",
        "",
        "生成物です。手で直さないでください（作り直しは tools/build_guide_dict.py）。",
        "数値は定義データから引いた「定義上の値」です。",
        "",
    ]
    lines += render_goods_md(goods)
    lines += render_facilities_md(facilities)
    return "\n".join(lines)


def render_memos_md(memos: list[dict]) -> str:
    lines = [
        "# 相談メモ（案内係フェーズ1）",
        "",
        "生成物です。手で直さないでください（原稿は tools/guide_memos.yaml）。",
        "答えは、コードと定義で確かめた事だけを書きます。",
        "",
    ]
    for memo in memos:
        lines.append(f"## {memo['question']}")
        lines.append("")
        lines.append(tidy(memo["answer"]) or "")
        lines.append("")
        lines.append("根拠:")
        for basis in memo["basis"]:
            lines.append(f"- {basis}")
        lines.append("")
    return "\n".join(lines)


def build_documents(repo: Path) -> dict[str, str]:
    """生成物のファイル名→中身を作る（検証で失敗したら GuideError）。"""
    notes_doc = load_yaml(repo / "tools/guide_notes.yaml", unique=True)
    memos_doc = load_yaml(repo / "tools/guide_memos.yaml", unique=True)
    scope, notes_goods, notes_facilities = validate(repo, notes_doc, memos_doc)
    goods = build_goods(repo, notes_goods)
    facilities = build_facilities(repo, notes_facilities, scope)
    memos = [{**m, "answer": tidy(m["answer"])} for m in memos_doc["memos"]]
    payload = {"goods": goods, "facilities": facilities, "memos": memos}
    return {
        "README.md": README_TEXT,
        "dictionary.md": render_dictionary_md(goods, facilities),
        "dictionary.json": json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        "memos.md": render_memos_md(memos),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="案内係の辞書と相談メモを生成する")
    parser.add_argument("--check", action="store_true", help="作り直さず、古くなっていないか調べる")
    args = parser.parse_args(argv)
    try:
        documents = build_documents(REPO)
    except GuideError as exc:
        print(f"生成できません: {exc}", file=sys.stderr)
        return 1
    out_dir = REPO / OUT_DIR
    if args.check:
        stale = [
            name
            for name, text in documents.items()
            if not (out_dir / name).exists() or (out_dir / name).read_text(encoding="utf-8") != text
        ]
        if stale:
            print(f"古くなっています: {', '.join(stale)}（作り直してください）", file=sys.stderr)
            return 1
        print("docs/guide/ は最新です")
        return 0
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, text in documents.items():
        (out_dir / name).write_text(text, encoding="utf-8")
    print(f"docs/guide/ に {len(documents)} ファイルを生成しました")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
