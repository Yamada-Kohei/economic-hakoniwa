# モジュールの作り方

ルールの計算は「モジュール」に分かれています。モジュールは次のものを登録します。

- **段**: ターン処理の中で実行されるもの。並びは `game.yaml` の `stages:` で決めます。
- **命令**: 命令の種類ごとの処理関数。命令の中身の形は `orders.yaml` に書きます。
- **式**: 「行政力の計算」のように名前が付いた差し替え口。`game.yaml` の `formulas:` で別の関数に替えられます。

標準のモジュールは `hakoniwa_core/modules/` に、パックのモジュールは `packs/<パック>/modules/` にあります。
どちらも `register(registry)` 関数を持つ Python のパッケージです。

## 標準のモジュール（フェーズ3-4）

1ターンは `game.yaml` の `stages:` の順に、次の段を実行します。フェーズ2から、物はお金で売り買いし、
お金はすべて帳簿（`ledger.py`）の仕訳で動きます。フェーズ3-1 から、島と島をまたぐ貿易と為替を全島の段
`trade` が受け持ち、島の通貨は島ごとに1つ（通貨の番号は島の番号）です。フェーズ3-3 から、国債の段
`bonds` も全島の段になり（外国の政府・中央銀行・銀行が、出した国の入札と売買の市場にその国の通貨で
加わるため）、記録の段の後の全島の段 `rankings` が産業ごとの称号を決めます。フェーズ3-4 で、島ごとの段
`politics` が政情・選挙・デモと暴動を受け持ち、計画経済の島の市場は公定価格で動きます。

| 段 | モジュール | すること |
|---|---|---|
| `upgrade` | `upgrade` | 前のフェーズから続く島に、フェーズ2-3 の基盤（インフラの整備度・学校・技術者・教育水準）と、フェーズ3-1 の貿易の仕組み（港・海運業と輸送船・商社。海外の商人の来る期間の始まり）を1度だけ置く（`economy/groundwork.py`・`trade/setup.py`） |
| `execute_orders` | `planning` | 行政力の範囲で、並んだ命令を上から実行する（足りない分は次のターンへ）。税率・予算などの政策もここで変わる |
| `budget` | `fiscal` | 国庫のお金を、支出の項目ごとに支払いの順番で取り置く |
| `employment` | `employment` | 技術者を席に割り当て、町から通勤圏の施設へ、賃金の高い施設（並べば優先度の高い施設）から労働者を割り当てる。余った技術者は島のどこの一般の席にも就く。公共事業の作業員を雇う |
| `production` | `production` | 送電網ごとに電力を板寄せして電気代を払い、労働者・投入財・電力の足りた割合で生産する（生産性に、産業の経験と同じ産業の工場の集積の係数が掛かる。フェーズ3-3）。産出は施設の在庫に入り、産業の経験に足す（年の終わりに、動かさなかった産業の経験を減らす。`economy/experience.py`） |
| `construction` | `construction` | 資材と人手のそろった工事を進め、終わった施設を動かし始める。インフラの維持と工事を進める |
| `payroll` | `labor` | 賃金・所得税・失業手当を払い、施設ごとの賃金を見直す |
| `market` | `market` | 財ごとの板寄せ（施設・家計・工事・国の倉庫・商社・海運業・海外の商人。契約で売る国の政府が足りない分を買う。船で国の倉庫に届いた腐る物は売り切れるまで指値なしで売る）、代金・運賃・消費税の支払い、傷んだ物を捨てる、希望価格と操業目標の見直し。計画経済の財は公定価格（`economy/plan.py`）で値幅 0 にし、足りなければ重点産業 → ほかの国営施設と国の工事 → 家計（1人あたり同じ量の配給）→ 輸出の順に配る。混合経済は値段の上限と下限で値幅を止める。体制を変えた後の首都の建物の建て替えの資材を、公共事業費でほかの工事より先に買う |
| `trade` | `trade`（全島の段） | 届いた荷を下ろす（関税を払う）、船の解体と就航、貿易圏と契約と援助の命令を決める、為替の板寄せ（島の中央銀行と、相手の通貨を発行した島の中央銀行の受け身の注文つき。介入と固定相場の注文、外国の国債を買う政府と銀行の外貨の買いも）、二国間契約の履行、貿易圏×財の板寄せ（禁輸の組を外し、輸入の割り当てまで。圏の固定価格）と船出、輸出補助、圏から抜ける島と終わった契約の始末、運賃と商社の狙う量の見直し（`hakoniwa_core/trade/`） |
| `banking` | `banking` | 銀行間と中央銀行への返済、融資の利払いと返済、預金の利子、運転資金の融資（審査・大口融資の規制）、預金の乗り換えと取り付け |
| `bonds` | `bonds`（全島の段。フェーズ3-3） | 国債の利払いと償還（不履行と、その後しばらく入札で売れない・損をした銀行が融資を止める罰）。国庫が足りない分を入札で発行し、売れ残りは政策に従って中央銀行が引き受けるか支出を減らす。国債の売買の市場。外国の持ち手（政府・中央銀行の命令と、資本取引が自由な国の銀行）は、段の初めに全部の島の分の注文を決めてから、島ごとに入札と売買の市場に加わる。利子と償還はその国の通貨で外国の持ち高に入る（`trade/abroad.py`） |
| `accounts` | `accounts` | 決算: 損益（国債と外貨は売った・償還された分の損益。まだ売っていない評価損益は入れない）、減価償却、補助金、法人税、配当と国庫への納付、中央銀行の納付 |
| `central_bank` | `central_bank` | 政策金利（独立のとき）、銀行間の貸し借り、中央銀行の貸し出し |
| `failures` | `failures` | 返済の遅れが続いた施設の倒産と差し押さえ、銀行の破綻と破綻処理 |
| `entry_exit` | `firms` | 民営工場と民間銀行と商社の参入（商社がなくなった島には開き直す）、民営工場の撤退、差し押さえた施設の売却 |
| `population` | `population` | 食べる、増える・減る、幸福度、島を出て行く人 |
| `housing` | `population` | 働き口の近くに町を作り、人が増えた町を育てる（資材は市場で買う。都市と大都市は上下水道が要る） |
| `politics` | `politics`（フェーズ3-4） | 政情を目標（式 `stability`）に近づけ、段階（落ち着き・デモ・暴動・反乱の前触れ）を決める。暴動で都市の施設を止め・まれに壊す（警察で減る）。支持率と選挙、報道の統制でたまった不満、体制を変えた後の混乱期間と首都の建物の建て替え（`hakoniwa_core/politics.py`） |
| `infrastructure` | `infrastructure` | インフラの整備度を傷みの分だけ下げ、統計を残す |
| `education` | `education` | 教育水準を学校の定員に向けて動かし、大学で技術者を育て、技術者の引退を進める |
| `events` | `events` | まれな災害で施設をしばらく止める |
| `records` | `records` | 物価指数・GDP・貿易収支・国際収支などの統計を残し、帳簿の確かめ（合わなければその場で止める） |
| `rankings` | `experience`（全島の段。フェーズ3-3） | 各産業の経験の累積が世界で1位の国に称号を付け（得た国と失った国のログに知らせる）、島ごとに称号の数と産業ごとの世界の順位を統計に残す |
| `trade_check` | `trade`（全島の段） | 世界の確かめ: 外貨の持ち高と発行した島の非居住者の預金、外国の国債の持ち高と発行した島の帳簿、運ぶ途中の荷（積んだ量 − 下ろした量）が合うか（合わなければその場で止める） |

命令は次のモジュールが登録します。

| モジュール | 命令 |
|---|---|
| `basics` | `rename_island`・`rename_place`・`rename_facility`（名前の変更） |
| `construction` | `build`・`demolish`（建設・取り壊し） |
| `land` | `build_power_line`・`set_no_development`（送電網の工事・開発禁止） |
| `infrastructure` | `build_infrastructure`（インフラの工事を列に並べる） |
| `fiscal` | `set_taxes`（税率）・`set_budget`（年の予算） |
| `labor` | `set_labor_policy`（失業手当・最低賃金） |
| `accounts` | `set_accounting`（国営施設の会計の形） |
| `market` | `set_priority`（優先度）・`warehouse_trade`（国の倉庫の売り買い）・`set_price_cap`（値段の上限。市場経済は電気料金だけ、混合経済はどの財も） |
| `politics` | `set_press`（報道と集会の統制）・`dissolve_parliament`（議会の解散）・`rename_leader`（元首の名前。共和制）・`mobilize_labor`（労働の動員。全体主義）・`amend_constitution`（憲法改正）・`change_economy`（経済体制の変更）。議会の要る命令（`orders.yaml` の `parliament`）は、実行の前に命令の関門（`add_order_gate`）で採決する |
| `plan` | `set_official_price`（公定価格。計画経済）・`set_priority_industries`（重点産業。計画経済）・`set_price_floor`（値段の下限。混合経済） |
| `banking` | `set_bank_policy`（民間銀行の参入・預金保護）・`grant_bank_license`（銀行の免許） |
| `bonds` | `set_bond_policy`（期間の割合・売れ残りの扱い・追加の発行・直接の引き受け）・`set_bond_payment`（国債の利払いを止める） |
| `central_bank` | `set_monetary_policy`（政策金利・国債の売買）・`set_central_bank_independence`（中央銀行の独立。独立すると介入・固定相場・中央銀行の外国の国債の命令をやめる）・`emergency_loan`（銀行への緊急の貸し出し） |
| `failures` | `resolve_bank`（破綻した銀行の処理） |
| `firms` | `set_zone`（工業団地・オフィス街）・`nationalize`・`privatize`（国有化・民営化）・`set_subsidy`（補助金） |
| `trade` | `create_bloc`・`join_bloc`・`leave_bloc`・`bloc_settings`・`transfer_bloc`・`take_over_bloc`（貿易圏）・`propose_contract`・`accept_contract`・`cancel_contract`（二国間契約）・`send_aid`（お金と物の援助）。命令の段では申し送りに積むだけで、貿易の段で島の番号の順に決める。フェーズ3-3 の政策（`trade/policy.py`）: `set_tariff`（関税）・`set_export_subsidy`（輸出補助）・`set_import_quota`（輸入の割り当て）・`set_embargo`（禁輸）・`set_capital_controls`（資本取引の制限）・`set_fx_intervention`（為替の介入）・`set_fx_peg`（固定相場）・`set_foreign_bonds`（外国の国債の売り買い）。命令の段で政策を変え、同じターンの貿易の段と国債の段から効く |

差し替えられる式は次のとおりです。

| 式 | モジュール | 中身 |
|---|---|---|
| `admin_power` | `planning` | 行政力 |
| `output_rate` | `production` | 稼働率 |
| `power_target` | `production` | 発電所の操業目標（送電網の買いたい量に合わせる） |
| `food_need` | `population` | 食料の必要量 |
| `happiness` | `population` | 幸福度 |
| `wage_update` | `labor` | 賃金の見直し（人手の足りなさと物価上昇率で動く） |
| `price_update` | `market` | 売り手の希望価格の見直し（企画書の式） |
| `operating_target` | `market` | 施設の操業目標（売れ行きの見込みと在庫の目安から） |
| `consumption` | `market` | 家計の層の使う額（所得と預金から） |
| `bond_yield` | `bonds` | 国債の利回り |
| `loan_rate` | `banking` | 融資の金利 |
| `policy_rate` | `central_bank` | 独立した中央銀行の政策金利 |
| `entry_return` | `firms` | 民営工場の見込みの年の利益率 |
| `engineer_wage_update` | `labor` | 技術者の賃金の見直し（技術者の席の埋まり方と技術者の失業率で動く） |
| `education_level` | `education` | 次のターンの教育水準 |
| `stability` | `politics` | 政情の目標とその内訳（フェーズ3-4） |

モジュールが共通で使う道具はコアの直下にあります。

| 場所 | 中身 |
|---|---|
| `ledger.py` | お金の帳簿。お金はここの `post` と `pay` を通してだけ動く。毎ターンの4つの確かめ |
| `market/auction.py` | 板寄せ（値幅の中で約定する量が最も多い値段を決め、有利な注文から配る） |
| `trade/` | 貿易と為替の部品（フェーズ3-1）: 貿易圏（`blocs.py`）、二国間契約（`contracts.py`）、為替（`fx.py`）、島をまたぐお金（`money.py`。外貨の持ち高と非居住者の預金）、海外の商人（`merchant.py`）、港・船・運賃（`ships.py`）、商社（`traders.py`）、移し替えと商社の開業（`setup.py`） |
| `economy/` | お金の経済の部品: 初めの値段と変動費（`prices.py`）、払えるお金の取り置き（`funds.py`）、国債（`debt.py`）、銀行と融資（`banks.py`）、運賃の率（`freight.py`）、家計の層（`households.py`）、収支の記録（`books.py`）、物価指数（`stats.py`）、経済の始まりと移し替え（`start.py`）、生産の基盤の初めの値（`groundwork.py`）、施設の生産性（`productivity.py`）、計画経済の公定価格・生産目標・割当と配給（`plan.py`。フェーズ3-4） |
| `politics.py` | 政治の状態の読み書き（フェーズ3-4）: 政情の段階、体制の特色、報道と集会の扱い、首都の建物、建て替えの資材 |
| `infra.py` | マスのインフラの整備度と工事の列の読み書き |
| `units.py` | 量・お金・率の単位と丸め（丸めはここの関数だけで行う） |
| `allocation.py` | 限りのある物を優先順位の順に配る `by_priority` |
| `stock.py` | 在庫（作られたターンごとの束で持ち、古い物から使う。傷む財の計算） |
| `facilities.py` | 施設の定義の読み出し、優先度、置ける場所と数の上限、町の施設 |
| `geo.py` | 距離、隣のマス、つながった塊、最短の道 |
| `naming.py` | 地名と、施設に自動で付く名前 |
| `founding.py` | 建国（形と地形の検査、自動の地形、最初の中身の配置） |
| `autoplan.py` | 試験用の「標準の計画」（ゲームのルールではない） |
| `gate.py` | フェーズ2・3の関門の目安（ゲームのルールではない） |

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
- **お金**は `ledger.py` の `post`・`pay`（とそれを包む `economy/funds.py`）を通してだけ動かし、
  `IslandState.accounts` を直接書き換えません。記録の段が毎ターン帳簿を確かめ、合わなければ止まります。
- **丸め**は `units.py` の関数（`round_money`・`money_of`・`part`・`scale_price` など）だけで行います。
  量は 1000 分の 1 単位、お金は整数、率は 1 万分の 1 の整数で持ちます。
- 板寄せの注文は、施設は番号の順、家計は層の順に出し、同じ値段の注文は優先順位と出した順で配ります。
- **島をまたぐ処理**（フェーズ3-1 の `trade`）は、全島の段の `resolve` で世界をまとめて書き換えます。
  島は番号の順、圏・契約・運ぶ途中の荷は番号の順、圏の市場の約定は近い島の組から割り当てるので、島の
  並び順は結果に効きません。島をまたぐお金は `trade/money.py` の `transfer` を通してだけ動かします
  （通貨の島の帳簿の上で払い、外の島の主体の分は外貨の持ち高として持つ）。

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

モジュール固有の状態は、島やマスの `ext`（拡張欄、JSON にできる値）に入れます。世界全体の状態は
`WorldState.ext` に入れます（フェーズ3-1 の貿易圏・契約・運ぶ途中の荷は `world.ext["trade"]`、島ごとの
レート・外貨の持ち高・圏と契約の見え方は `island.ext["trade"]`）。
フェーズ2の経済の状態は、島の `economy`（値段・賃金・物価の履歴、家計の層など）と `policy`（税率・
予算・政策金利など）、施設の `economy`（賃金・希望価格・操業目標など）に型付きで持ちます。
お金の残高（勘定）と国債の銘柄、帳簿の記録は、サーバーでは `server/economy` のアプリの表に入ります。
表が必要な大きいモジュールだけ、Django のアプリを対にして持ちます。

## 試す

```sh
python -m hakoniwa_core.simulate --islands 50 --turns 300             # 命令なし
python -m hakoniwa_core.simulate --islands 50 --turns 300 --autoplan  # 標準の計画で命令を並べる
python -m hakoniwa_core.simulate --islands 50 --turns 300 --autoplan --gate  # 関門の目安も見る（フェーズ2・3）
```

最後に出る「状態の指紋」は、同じ種なら何度回しても同じになります。
`tests/core/test_gate.py` は標準の計画で 300 ターン回し、鉄鉱石 → 鋼材 → 機械 → 新しい工場の連鎖が
回ること、在庫が尽きないこと、人口が急に増えたり消えたりしないこと、毎ターン帳簿が合うこと、
GDP の生産の側と支出の側が合うこと、島の順番や保存と読み込みで結果が変わらないことを確かめます。
50島の関門（`test_phase2_gate_50_islands`）は時間がかかるので、環境変数 `HAKONIWA_FULL_GATE=1`
のときだけ回します。
`tests/core/test_trade.py` は、標準の計画の貿易圏で島どうしが売り買いし荷が届くこと、為替が1ターンに
値幅より動かないこと、島をまたぐお金と運ぶ途中の荷が毎ターン合うこと、契約と海外の商人を確かめます。
