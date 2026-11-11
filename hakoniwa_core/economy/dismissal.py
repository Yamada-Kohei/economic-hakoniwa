"""解雇の決まり（LB-43。2026-11-10、W-251）。

売れ行きが落ちて雇いたい人数が減っても、施設が1か月に減らせるのは、前のターンの人数の、国の政策
「解雇の決まり」（policy.dismissal: weak・normal・strong。"" は経済体制の既定 regimes.yaml の
dismissal、決めていなければ weak）の段ごとの割合（labor.dismissal の rate）と、自然に辞める人の分
（labor.attrition）までです。余った人は雇ったまま賃金を払い、作る量は操業目標までにします
（economy["held"] に、操業目標に要る人数を超えて抱えている人数を残す）。

解雇した人（自然に辞める人の分を超えて減らした人）には、その施設の月の賃金 × 段の月数
（labor.dismissal の months）の解雇手当を、仕事を失った家計の層に払います（技術者の席の人は技術者の
層に、技術者の賃金で）。払えない分は未払い賃金（倒産の分け方で一番先）にします。閉鎖・取り壊しは割合
によらず全員を解雇し、手当を払います（on_removal）。

決まりにかかるのは、建っていて動いている施設のうち、計画経済の島で人を抱える国営施設（PL-57）・
人数を荷や売上や預金で決める施設（運送業・港・海運業・商社・銀行・商業とサービス業）・畑で作物を
育てる農場（種まきと収穫の季節の人の出入り）を除いたものです（covered）。工事の作業員は施設でない
ので外です。

現実: 米国（弱い）は予告と手当の決まりがほとんどなく、売れ行きに合わせてすぐ減らせる。日本（ふつう）
は 30 日前の予告と整理解雇の要件があり、不況でも人を抱えて操業を落とす（雇用調整助成金のころ）。
南欧（強い）は協議と手厚い手当が要り、減らすのに 1 年ほどかかる。

入って labor.fixed_term か月までの人は有期（月ごとに更新する契約）です。減らすときは新しい人から
で、有期の人は割合の上限にも手当にもかからず、更新しないで辞めてもらいます（解雇に数えない）。月数
を過ぎた人は期間の定めのない雇用になり、決まりにかかります（fixed_term・track_fixed_term）。
現実: 日本の企業は先の見えない増産を有期・派遣で雇い、売れ行きが落ちるとまずそこを減らす（2008 年
の派遣切り）。正社員は試用の後に本採用。欧州でも有期の守りは弱く、景気の上下は有期の人が受ける。
決まりだけだと、売り切れて試しに雇った人を翌月減らすたびに手当がかかり、企業の連鎖倒産と銀行の
救済が増えた（解雇の約半分が入って半年以内の人だった。2026-11-11 の比べ）。
"""

from __future__ import annotations

import math
from typing import Any

from hakoniwa_core import facilities as fac
from hakoniwa_core import ledger
from hakoniwa_core.config import GameConfig
from hakoniwa_core.defs import Defs
from hakoniwa_core.economy import (
    ENGINEERS,
    Payer,
    arrears,
    books,
    funds,
    household,
    is_bank,
    is_transport,
    payer_of,
    services,
)
from hakoniwa_core.economy import plan as planning
from hakoniwa_core.economy.households import add_income, jobless_layer
from hakoniwa_core.state import Facility, IslandState

WEAK, NORMAL, STRONG = "weak", "normal", "strong"
LEVELS = (WEAK, NORMAL, STRONG)
HELD = "held"  # f.economy: 操業目標に要る人数を超えて抱えている人（解雇の決まり）
CARRY = "dismissal_carry"  # f.economy: 減らせる人数の端数（次のターンに足す）
NEEDS = "dismissal_needs"  # f.economy: 直近の月の雇いたい人数（新しい月が後ろ。手当の月数分）
FIXED_TERM = "fixed_term"  # f.economy: 有期の人数（入った月ごと。新しい月が後ろ）
SEVERANCE = "severance"  # 仕訳の種類（解雇手当。施設・政府 → 家計）


def level(island: IslandState, defs: Defs) -> str:
    """島の解雇の決まりの段（決めていなければ経済体制の既定、それもなければ弱い）。"""
    if island.policy.dismissal:
        return island.policy.dismissal
    regime = defs.regimes.get(island.regime.get("economy", ""), {})
    return str(regime.get("dismissal", WEAK))


def terms(island: IslandState, defs: Defs, config: GameConfig) -> dict[str, float]:
    """段の決まり: rate（1か月に減らせる割合。自然に辞める分を除く）と months（解雇手当の月数）。"""
    table: dict[str, dict[str, Any]] = config.get("labor.dismissal")
    row = table.get(level(island, defs)) or table[WEAK]
    return {"rate": float(row["rate"]), "months": float(row["months"])}


def covered(island: IslandState, defs: Defs, config: GameConfig, f: Facility, turn: int) -> bool:
    """解雇の決まり（割合の上限と解雇手当）にかかる施設か。止まった施設（災害・暴動）は一時の休み
    なので外す。閉鎖に入った施設は割合の上限の外だが、手当はかかる（呼ぶ側が分ける）。"""
    from hakoniwa_core.trade import staff  # trade は economy を読むので、ここで読む

    if not island.has_economy or not f.built or fac.is_housing(config, f):
        return False
    if planning.hoards(island, defs, f) or fac.is_halted(f, turn):
        return False
    if is_transport(config, f) or is_bank(config, f) or services.is_services(config, f):
        return False
    if staff.wanted(config, defs, f) is not None:  # 港・海運業・商社は扱う荷に合わせて
        return False
    return fac.grow_turns(defs, f.kind, f.recipe) <= 0  # 畑の農場は季節で人が出入りする


def floor(
    island: IslandState, defs: Defs, config: GameConfig, f: Facility, before: int
) -> tuple[int, float]:
    """前のターンの人数 before から、このターンに残す人数の下限と、減らせる人数の端数を返す。
    下限は設備の傷んだ分を除いた全力の人数まで（抱え込み PL-57 と同じ）。"""
    rate = terms(island, defs, config)["rate"] + config.get("labor.attrition")
    allowed = before * rate + f.economy.get(CARRY, 0.0)
    full = fac.scale(fac.workers_of(defs, f), fac.equipment_cap(f))
    return max(0, min(full, before - int(allowed))), allowed - int(allowed)


def recent_peak(island: IslandState, defs: Defs, config: GameConfig, f: Facility, need: int) -> int:
    """直近の手当の月数（端数は切り上げ）のうちに雇いたかった人数の一番多い月の人数。このターンの
    雇いたい人数 need を覚えて、次のターンからの分にする。手当のない段（弱い）は 0。

    解雇して手当を払い、また同じ人数を雇い直すより、要らない月の賃金を払って抱えておくほうが安い
    間は解雇しない（手当の月数分の賃金と、抱えておく月の賃金が釣り合う）。売れ行きの上下で操業
    目標が月ごとに行き来する施設が、毎月解雇しては雇い直して手当を払い続けないようにする。
    現実: 解雇に費用のかかる国の企業は、一時の落ち込みでは人を減らさず抱える（労働の保蔵）。"""
    months = math.ceil(terms(island, defs, config)["months"])
    if months <= 0:
        f.economy.pop(NEEDS, None)
        return 0
    past = list(f.economy.get(NEEDS, []))[-months:]
    f.economy[NEEDS] = (past + [need])[-months:]
    return max(past, default=0)


def fixed_term(f: Facility) -> int:
    """有期の人数（入って labor.fixed_term か月までの人）。"""
    return sum(f.economy.get(FIXED_TERM, []))


def track_fixed_term(config: GameConfig, f: Facility, before: int, after: int) -> None:
    """月の終わりに有期の人数を見直す。前のターンの人数 before から after に減った分は新しい人から
    抜き（辞めるのも、辞めてもらうのも新しい人から）、増えた分はこの月に入った有期の人にする。
    labor.fixed_term か月を過ぎた月の人は、期間の定めのない雇用に変わる（並びから落ちる）。"""
    months = int(config.get("labor.fixed_term"))
    terms_ = list(f.economy.get(FIXED_TERM, []))
    gone = max(0, before - after)
    for i in range(len(terms_) - 1, -1, -1):  # 月の並びは残す（後ろから数えた位置が入った月）
        take = min(gone, terms_[i])
        terms_[i] -= take
        gone -= take
    terms_ = (terms_ + [max(0, after - before)])[-months:] if months > 0 else []
    while terms_ and not terms_[0]:
        terms_.pop(0)
    if terms_:
        f.economy[FIXED_TERM] = terms_
    else:
        f.economy.pop(FIXED_TERM, None)


def natural_leavers(config: GameConfig, before: int) -> int:
    """前のターンの人数 before のうち、自然に辞める人（手当を払わない）。"""
    return round(before * config.get("labor.attrition"))


def pay(
    island: IslandState,
    defs: Defs,
    config: GameConfig,
    f: Facility,
    general: int,
    engineers: int = 0,
) -> int:
    """解雇した一般の人 general と技術者の席の人 engineers に解雇手当を払う。払い手のお金で足りない
    分は未払い賃金にする。払った額と未払いにした額の和を返す（施設の帳簿の賃金に足す）。"""
    months = terms(island, defs, config)["months"]
    wage = f.economy.get("wage", 0)
    if months <= 0 or wage <= 0 or general + engineers <= 0:
        return 0
    payer = payer_of(defs, config, f)
    engineer_wage = fac.engineer_wage_of(config, f, wage) if engineers else 0
    total = 0
    for layer, count, monthly in (
        (jobless_layer(island), general, wage),
        (ENGINEERS, engineers, engineer_wage),
    ):
        amount = round(count * monthly * months)
        if amount <= 0:
            continue
        # 国庫直轄の施設は、年の予算の月割りの残りで払い、足りない分はどの項目にも取り置いて
        # いない国庫のお金から払う（法で決まった手当は、予算の項目の上限で止めない義務的な支出）。
        # それでも払えない分は未払い賃金（次のターンに先に払う）
        paid = 0
        for source in _sources(payer):
            part = max(0, min(amount - paid, funds.available(island, source)))
            if part:
                funds.pay(island, source, household(layer), part, SEVERANCE)
                paid += part
        if paid:
            # 退職の所得は控除が大きく、ほとんど税がかからないので差し引かない
            add_income(island, layer, paid)
        if amount > paid:
            arrears.add_wages(island, payer.entity, household(layer), amount - paid)
        total += amount
    if total:
        if f.built:
            books.add(island, f.id, books.WAGES, total)
        stats = island.stats
        stats["labor.severance"] = stats.get("labor.severance", 0) + total
    return total


def _sources(payer: Payer) -> tuple[Payer, ...]:
    """解雇手当を払うお金の出どころ（先の順）。国庫直轄の施設は、施設の予算の項目の後に、どの項目
    にも取り置いていない国庫のお金。"""
    if payer.entity == ledger.GOV and payer.item:
        return payer, Payer(ledger.GOV)
    return (payer,)


def on_removal(island: IslandState, defs: Defs, config: GameConfig, f: Facility, turn: int) -> None:
    """閉鎖・取り壊しで施設がなくなるとき、まだ働いている人を全員解雇し、手当を払う（割合の上限に
    よらない）。預金の分け方（未払い賃金が一番先）の前に呼ぶ。"""
    if f.workers <= 0 or not covered(island, defs, config, f, turn):
        return
    engineers = f.economy.get("engineers", 0)
    # 有期の人は契約を終える（手当はない）
    general = f.workers - engineers - f.economy.get("mobilized", 0) - fixed_term(f)
    count = max(0, general) + engineers
    pay(island, defs, config, f, max(0, general), engineers)
    stats = island.stats
    stats["labor.dismissed"] = stats.get("labor.dismissed", 0) + count


def actual_profit(f: Facility, profit: int) -> int:
    """全力で動いたときの変動費の利益 profit を、人を抱えている施設では、抱えた人の賃金を引いた
    額にする（撤退 FM-20 と管財人の運転資金の審査 BK-50 が見る。LB-43）。
    抱えた人の賃金を見ないと、余った人の賃金で撤退が早まらず、お金が尽きて倒産するだけになる。"""
    held = f.economy.get(HELD, 0)
    if held <= 0:
        return profit
    # 全力の利益から抱えた人の賃金を引く（抱えた人が 0 人のときと続いた値で、下げる向きだけ）
    return profit - held * f.economy.get("wage", 0)
