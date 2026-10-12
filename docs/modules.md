# モジュールの作り方

ルールの計算は「モジュール」に分かれています。モジュールは次のものを登録します。

- **段**: ターン処理の中で実行されるもの。並びは `game.yaml` の `stages:` で決めます。
- **命令**: 命令の種類ごとの処理関数。命令の中身の形は `orders.yaml` に書きます。
- **式**: 「行政力の計算」のように名前が付いた差し替え口。`game.yaml` の `formulas:` で別の関数に替えられます。

標準のモジュールは `hakoniwa_core/modules/` に、パックのモジュールは `packs/<パック>/modules/` にあります。
どちらも `register(registry)` 関数を持つ Python のパッケージです。

## 標準のモジュール（フェーズ1）

1ターンは `game.yaml` の `stages:` の順に、次の段を実行します。

| 段 | モジュール | すること |
|---|---|---|
| `execute_orders` | `planning` | 行政力の範囲で、並んだ命令を上から実行する（足りない分は次のターンへ） |
| `employment` | `employment` | 住宅地から通勤圏の施設へ、優先度の高い施設から働き手を割り当てる |
| `production` | `production` | 送電網ごとに発電と配電をし、働き手・原料・電力の足りた割合で生産する |
| `construction` | `construction` | 資材と人手のそろった工事を進め、終わった施設を動かし始める |
| `distribution` | `distribution` | 共同倉庫の物を「住民の食料 → 工事の資材 → 工場の原料（次のターンに使う）」の順に、優先度の高い所から配る。傷んだ物を捨てる |
| `population` | `population` | 食べる、増える・減る、幸福度、島を出て行く人 |
| `housing` | `population` | 働き口の近くに住宅地を広げ、人が増えた住宅地を育てる |
| `events` | `events` | まれな災害で施設をしばらく止める |
| `records` | `records` | 人口・雇用・在庫などをこのターンの統計に残す |

命令は `basics`（名前の変更）、`construction`（建設・取り壊し）、`land`（送電線・開発禁止）、
`distribution`（優先度の変更）が登録します。差し替えられる式は `admin_power`（行政力）、
`output_rate`（稼働率）、`food_need`（食料の必要量）、`happiness`（幸福度）です。

モジュールが共通で使う道具はコアの直下にあります。

| 場所 | 中身 |
|---|---|
| `allocation.py` | 限りのある物を優先順位の順に配る `by_priority` |
| `stock.py` | 共同倉庫（作られたターンごとの束で持ち、古い物から使う。傷む財の計算） |
| `facilities.py` | 施設の定義の読み出し、優先度、住宅地の施設 |
| `geo.py` | 距離、隣のマス、つながった塊、最短の道 |
| `naming.py` | 地名と、施設に自動で付く名前 |
| `founding.py` | 建国（形と地形の検査、自動の地形、最初の中身の配置） |
| `autoplan.py` | 試験用の「標準の計画」（ゲームのルールではない） |

## 例: 毎ターン少し人が増える段

```python
# packs/example/modules/growth/__init__.py
from hakoniwa_core.registry import IslandStage, Registry


class Growth(IslandStage):
    name = "growth"

    def run_island(self, island, ctx):
        rng = ctx.rng(island.id)  # この島・この段・このターンの乱数
        rate = ctx.config.get("growth.rate")  # 数値は game.yaml に置く
        for tile in island.tiles:
            if tile.population and rng.random() < rate:
                tile.population += 1


def register(registry: Registry) -> None:
    registry.add_stage(Growth())
```

```yaml
# game.yaml
stages:
  - execute_orders
  - growth
growth:
  rate: 0.1
```

## 島の順番に結果が左右されないための決まり

- **島ごとの段**（`IslandStage`）は `run_island(island, ctx)` の中で、その島の状態だけを書き換えます。
  他の島を読むときは `reads_other_islands = True` にして `ctx.other_island(id)` を使います。
  返ってくるのは「この段が始まる前の写し」なので、どの島から処理しても結果は同じです。
- **全島の段**（`WorldStage`）は「`collect` で各島の注文を集める → `resolve` で一度に決める →
  `apply` で各島に結果を書く」の3つに分けて書きます。
- **乱数**は必ず `ctx.rng(island.id)`（全島の段では `ctx.rng()`）から取ります。
  世界の種・ターン・段の名前・島の番号から作るので、同じ入力から必ず同じ結果が出ます。
  `random.random()` のようなグローバルな乱数は使いません。
- 同じものを取り合うときは、定義データの優先順位と番号で順を決め、それでも並ぶときだけ乱数を使います。

## 命令

```python
from hakoniwa_core.registry import OrderFailed, OrderHandler


def check(island, payload, defs):
    # 受付時と実行時に呼ばれる。できないときは OrderFailed を投げる
    if ...:
        raise OrderFailed("reason_something")  # 文面は locale/ja/messages.yaml に書く


def execute(island, payload, ctx): ...


def register(registry):
    registry.add_order(OrderHandler("my_order", execute=execute, check=check))
```

命令の中身の形（JSON Schema）と要る行政力は `orders.yaml` に書きます。形の検査はコアが済ませてから
`check` と `execute` を呼ぶので、関数の中で形を確かめ直す必要はありません。

## 式の差し替え

```python
# 標準の式（hakoniwa_core/modules/planning/__init__.py）
def admin_power(island, ctx) -> int: ...


registry.add_formula("admin_power", admin_power)
```

```yaml
# game.yaml で別の関数に替える
formulas:
  admin_power: "my_package.formulas:admin_power"
```

## 状態の持ち方

モジュール固有の状態は、島やマスの `ext`（拡張欄、JSON にできる値）に入れます。
表が必要な大きいモジュールだけ、Django のアプリを対にして持ちます。

## 試す

```sh
python -m hakoniwa_core.simulate --islands 50 --turns 300             # 命令なし
python -m hakoniwa_core.simulate --islands 50 --turns 300 --autoplan  # 標準の計画で命令を並べる
```

最後に出る「状態の指紋」は、同じ種なら何度回しても同じになります。
`tests/core/test_gate.py` は標準の計画で 300 ターン回し、鉄鉱石 → 鋼材 → 機械 → 新しい工場の連鎖が
回ること、在庫が尽きないこと、人口が急に増えたり消えたりしないことを確かめます。
