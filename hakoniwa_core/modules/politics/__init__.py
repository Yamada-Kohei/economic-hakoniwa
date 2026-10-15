"""政治の段（politics。フェーズ3-4）と、政治の命令。

政情の段は消費と人口・都市の発展の後に、島ごとに次の順で動きます。共通の部品（体制の特色・政治の
状態）は hakoniwa_core/politics.py にあります。

1. 政情の目標を名前付きの式 stability で出す（内訳ごと）:
   T* = 50 + 0.6 (H − 50) − 1.5 (U − 5)+ − 1.0 (π − 5)+ − 60 (G − 0.35)+ + R + E（重みと境は
   game.yaml の politics.weights）。H は幸福度、U は失業率（%）、π は年の物価上昇率（%）、G は
   家計の層の1人あたりの所得から出した格差の指数（ジニ係数）、R は体制の補正（共和制の選挙の補正を
   含む）、
   E は出来事の補正（債務不履行は −15 から 36 ターンかけて戻る、体制を変えた後の混乱期間は −10、
   首都の政府の建物が壊れている間は −10）。報道と集会を統制していれば、その分（制限 +5・統制
   +10）も足す。
2. 政情を「目標との差 × 動きやすさ」だけ動かす（動きやすさは体制の stability_turns_up・
   stability_turns_down の逆数）。初めてのターン（建国と移し替え）は目標から始める（いきなり暴動に
   しない）。
3. 統制しているとたまった不満が積もり、政情が politics.levels.riot を切ったら、まとめて引く。
4. 段階（落ち着いている・デモ・暴動・反乱の前触れ）を決め、変わったらターンログに書く。行政力は
   次のターンの命令の段で減る（hakoniwa_core/politics.py の adjust_admin）。暴動と反乱の前触れでは、
   都市のマス（町より大きい住宅地の近く）の施設を乱数で止め（理由「暴動」。次のターンから 1〜3
   ターン）、まれに国営の施設を壊す（直すには建てるときの資材と人手の politics.riot.repair_share が
   要り、国の工事として直す）。警察（施設の定義の police）の人手が足りているほど、止まる施設と壊れる
   施設が減る。反乱の前触れは効き目が 2 倍で、ターンログで知らせる（反乱と内戦はフェーズ4）。
5. 支持率を出す（政情に、直近 12 ターンの実質賃金の伸びを足し、失業率の伸びを引く）。民主主義の
   島では、politics.election_turns ターンごとに選挙をして、支持率を与党の議席の割合にする。共和制
   では選挙で支持率が politics.republic.support を超えると、次の politics.republic.turns ターンは
   R を足す。
6. 混乱期間の生産性の倍率を決め、首都の建物の建て替え（体制を変えたとき）を進め、君主の年齢を
   進める。

議会の採決: 民主主義の島では、orders.yaml の parliament（majority・two_thirds）のある命令を、
実行するときに採決する（命令の段の関所。ExecuteOrders が呼ぶ）。賛成の割合 = 与党の議席の割合 +
（今の支持率 − 選挙のときの支持率）÷ 100 × politics.parliament.swing。過半数は 1/2 を超えたら、
3 分の 2 は 2/3 以上で通る。否決された命令も行政力を使い、わけをターンログに書く。税率の命令は、
どれかの税を politics.parliament.tax_points ポイントより大きく変えるときだけ採決する。
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core import facilities as fac
from hakoniwa_core import ledger, politics, stock
from hakoniwa_core.defs import Defs
from hakoniwa_core.economy import (
    GENERAL,
    OWNERS,
    Payer,
    banks,
    default_accounting,
    facility_entity,
    funds,
    household,
    layers,
)
from hakoniwa_core.economy import plan as planning
from hakoniwa_core.economy.debt import underwrite
from hakoniwa_core.economy.households import move_people, split_persons
from hakoniwa_core.economy.stats import inflation, real_wage_change
from hakoniwa_core.geo import within
from hakoniwa_core.modules.population import subsistence_persons
from hakoniwa_core.registry import IslandStage, OrderFailed, OrderHandler, Registry
from hakoniwa_core.state import (
    ACCOUNTING_SELF,
    ACCOUNTING_TREASURY,
    OWNER_PRIVATE,
    OWNER_STATE,
    Facility,
    IslandState,
    LogEntry,
    OrderState,
)
from hakoniwa_core.turn.context import StageContext
from hakoniwa_core.units import BASIS, money_of

YEAR = 12
TAXES = ("income_tax", "corporate_tax", "consumption_tax")


# --- 政情の目標（名前付きの式） ------------------------------------------------------


def stability(island: IslandState, facts: dict[str, float], ctx: StageContext) -> dict[str, float]:
    """政情の目標の内訳（合計が目標。0〜100 に収めるのは呼ぶ側）。

    facts: happiness（0〜100）、unemployment・inflation（%）、inequality（ジニ係数）、
    regime（体制の補正 R）、events（出来事の補正 E）、press（報道と集会の統制の分）。"""
    w = ctx.config.get("politics.weights")
    return {
        "base": w["base"],
        "happiness": w["happiness"] * (facts["happiness"] - w["happiness_mid"]),
        "unemployment": -w["unemployment"]
        * max(0.0, facts["unemployment"] - w["unemployment_free"]),
        "inflation": -w["inflation"] * max(0.0, facts["inflation"] - w["inflation_free"]),
        "inequality": -w["inequality"] * max(0.0, facts["inequality"] - w["inequality_free"]),
        "regime": facts["regime"],
        "events": facts["events"],
        "press": facts["press"],
    }


# --- 島の様子 ----------------------------------------------------------------------


def happiness_of(island: IslandState) -> float:
    """島の幸福度（層の幸福度を人数で平均。層がなければマスの平均）。記録の段と同じ。"""
    found = island.scratch.get("layer_happiness", {})
    persons = {n: h["persons"] for n, h in island.economy.households.items()}
    weight = sum(persons.get(n, 0) for n in found)
    if weight:
        return sum(v * persons.get(n, 0) for n, v in found.items()) / weight
    people = sum(t.population for t in island.tiles)
    mood = sum(t.ext.get("happiness", 0) * t.population for t in island.tiles)
    return mood / people if people else 0.0


def unemployment_of(island: IslandState, defs: Defs) -> float:
    """失業率（0〜1。村で自給の農業をしている人は数えない。記録の段と同じ）。"""
    share = fac.labor_share(defs, island)
    labor = idle = 0
    for tile in island.tiles:
        if tile.population <= 0:
            continue
        force = fac.labor_force(defs, island, tile)
        employed = tile.ext.get("employed", 0)
        farming = min(force - employed, int(subsistence_persons(defs, island, tile) * share))
        labor += force
        idle += force - employed - farming
    return max(0, idle) / labor if labor else 0.0


def regime_bonus(island: IslandState, defs: Defs, turn: int) -> float:
    """体制の補正 R（君主制 +5・民主主義 +5・全体主義 −5 など）と、共和制の選挙の補正。"""
    state = politics.state_of(island)
    found = politics.traits(island, defs)
    value = float(found.get("stability_bonus", 0))
    if turn <= state.get("bonus_until", -1):
        value += found.get("election_bonus", 0)
    return value


def events_bonus(island: IslandState, ctx: StageContext) -> dict[str, float]:
    """出来事の補正 E の内訳（債務不履行・混乱期間・政府の建物）。"""
    e = ctx.config.get("politics.events")
    out: dict[str, float] = {}
    since = island.economy.finance.get("default_turn")
    if since is not None:
        passed = ctx.turn - since
        if 0 <= passed < e["default_turns"]:
            out["default"] = e["default"] * (1 - passed / e["default_turns"])
    if politics.in_transition(island, ctx.turn):
        out["transition"] = e["transition"]
    office = capital_office(island, ctx.defs)
    if office is not None and not office.built and office.ext.get("repair"):
        out["office"] = e["office"]
    return out


def capital_office(island: IslandState, defs: Defs) -> Facility | None:
    """首都のマスの政府の建物。"""
    if island.capital is None:
        return None
    f = island.facility_at(*island.capital)
    if f is None or defs.facilities[f.kind].get("category") != "admin":
        return None
    return f


def press_bonus(island: IslandState, ctx: StageContext) -> float:
    mode = politics.state_of(island).get("press", politics.FREE)
    return float(ctx.config.get("politics.press.bonus").get(mode, 0))


# --- 段 -------------------------------------------------------------------------


class Politics(IslandStage):
    name = "politics"

    def run_island(self, island: IslandState, ctx: StageContext) -> None:
        if not island.has_economy:
            return
        defs, config = ctx.defs, ctx.config
        state = politics.state_of(island)
        first = not politics.started(island)
        events = events_bonus(island, ctx)
        facts = {
            "happiness": happiness_of(island),
            "unemployment": unemployment_of(island, defs) * 100,
            "inflation": inflation(island.economy) * 100,
            "inequality": politics.gini(politics.income_groups(island, YEAR)),
            "regime": regime_bonus(island, defs, ctx.turn),
            "events": sum(events.values()),
            "press": press_bonus(island, ctx),
        }
        parts = ctx.formula("stability")(island, facts, ctx)
        target = max(0.0, min(100.0, sum(parts.values())))
        state["target"] = round(target, 2)
        state["parts"] = {k: round(v, 2) for k, v in parts.items()}
        state["facts"] = {k: round(v, 3) for k, v in facts.items()}
        state["events"] = {k: round(v, 2) for k, v in events.items()}
        before = state.get("level", politics.CALM)
        if first:
            value = target
        else:
            value = state["stability"]
            found = politics.traits(island, defs)
            default = config.get("politics.stability_turns")
            turns = found.get("stability_turns_up" if target > value else "stability_turns_down")
            value += (target - value) / max(1, turns or default)
        value = self._grievance(island, ctx, value)
        state["stability"] = round(max(0.0, min(100.0, value)), 3)
        level = politics.level_of(state["stability"], config)
        state["level"] = level
        state["police"] = round(police_coverage(island, ctx), 3)
        if not first and level != before:
            args = {"stability": round(state["stability"])}
            island.log.append(LogEntry(f"politics_{level}", args))
        if level in (politics.RIOT, politics.REBELLION):
            self._riot(island, ctx, level)
        if level == politics.REBELLION:
            args = {"stability": round(state["stability"])}
            island.log.append(LogEntry("politics_rebellion_warning", args))
        self._support(island, ctx, facts["unemployment"], first)
        self._election(island, ctx, first)
        self._transition(island, ctx)
        self._rebuild(island, ctx)
        if politics.trait(island, defs, "monarch", False):
            state.setdefault("monarch_age", config.get("politics.monarch_age"))
            if ctx.turn % YEAR == 0:
                state["monarch_age"] += 1
        island.stats["politics.stability"] = state["stability"]
        island.stats["politics.target"] = state["target"]
        island.stats["politics.support"] = state.get("support", 0.0)
        if politics.has_parliament(island, defs):
            island.stats["politics.seats"] = state.get("seats", 0.0)

    # --- 統制とたまった不満 --------------------------------------------------------

    @staticmethod
    def _grievance(island: IslandState, ctx: StageContext, value: float) -> float:
        """統制している間、統制の分 × press.grievance_rate ずつ不満を積み（press.grievance_max
        まで）、政情が暴動の境を切ったら、たまった分をまとめて引く。"""
        settings = ctx.config.get("politics.press")
        state = politics.state_of(island)
        bonus = settings["bonus"].get(state.get("press", politics.FREE), 0)
        held = state.get("grievance", 0.0)
        if bonus > 0:
            held = min(settings["grievance_max"], held + bonus * settings["grievance_rate"])
        if held > 0 and value < ctx.config.get("politics.levels.riot"):
            island.log.append(LogEntry("politics_grievance_released", {"amount": round(held)}))
            value -= held
            held = 0.0
        state["grievance"] = round(held, 3)
        return value

    # --- 暴動 ---------------------------------------------------------------------

    def _riot(self, island: IslandState, ctx: StageContext, level: str) -> None:
        """都市のマスの施設を乱数で止め、まれに国営の施設を壊す。"""
        defs, config = ctx.defs, ctx.config
        settings = config.get("politics.riot")
        rng = ctx.rng(island.id)
        power = 2 if level == politics.REBELLION else 1
        guarded = police_coverage(island, ctx) * settings["police_effect"]
        candidates = urban_facilities(island, ctx)
        count = len(candidates) * settings["halt_share"] * power * (1 - guarded)
        count = int(count) + (1 if rng.random() < count - int(count) else 0)
        if count <= 0:
            return
        low, high = settings["halt_turns"]
        chosen = rng.sample(candidates, min(count, len(candidates)))
        destroyed = 0
        for f in sorted(chosen, key=lambda f: f.id):
            if (
                f.owner == OWNER_STATE
                and rng.random() < settings["destroy_chance"] * power * (1 - guarded)
                and defs.facilities[f.kind].get("category") not in ("finance", "trade")
            ):
                damage(island, ctx, f)
                destroyed += 1
                continue
            until = ctx.turn + rng.randint(low, high)
            if until > f.ext.get("halted_until", 0):
                f.ext["halted_until"] = until
                f.ext["halt_reason"] = "riot"
        args = {"count": len(chosen) - destroyed}
        island.stats["politics.halted"] = len(chosen) - destroyed
        island.stats["politics.destroyed"] = destroyed
        if guarded > 0:
            args["police"] = round(guarded * 100)
        island.log.append(LogEntry("riot_halted_police" if guarded > 0 else "riot_halted", args))

    # --- 支持率と選挙 ----------------------------------------------------------------

    @staticmethod
    def _support(island: IslandState, ctx: StageContext, unemployment: float, first: bool) -> None:
        """支持率 = 政情 + 実質賃金の1年の伸び（%）× support.wage − 失業率の1年の伸び（ポイント）×
        support.unemployment（0〜100）。"""
        settings = ctx.config.get("politics.support")
        state = politics.state_of(island)
        history = [*state.get("unemployment", []), round(unemployment, 3)][-(YEAR + 1) :]
        state["unemployment"] = history
        change = history[-1] - history[0] if len(history) > YEAR else 0.0
        wage = real_wage_change(island.economy) * 100
        value = state["stability"] + settings["wage"] * wage - settings["unemployment"] * change
        state["support"] = round(max(0.0, min(100.0, value)), 3)

    @staticmethod
    def _election(island: IslandState, ctx: StageContext, first: bool) -> None:
        state = politics.state_of(island)
        if not politics.has_parliament(island, ctx.defs):
            return
        if first or "election" not in state:
            elect(island, ctx, announce=False)
            return
        if ctx.turn >= state["election"]:
            elect(island, ctx)

    @staticmethod
    def _transition(island: IslandState, ctx: StageContext) -> None:
        """混乱期間の生産性の倍率（−politics.transition.productivity から一様に戻る）。"""
        state = politics.state_of(island)
        t = state.get("transition")
        if t is None:
            state.pop("productivity", None)
            return
        if ctx.turn >= t["until"]:
            state.pop("transition", None)
            state.pop("productivity", None)
            island.log.append(LogEntry("transition_ended", {}))
            return
        total = max(1, t["until"] - t["since"])
        left = (t["until"] - ctx.turn) / total
        drop = ctx.config.get("politics.transition.productivity")
        state["productivity"] = round(1 - drop * left, 4)

    @staticmethod
    def _rebuild(island: IslandState, ctx: StageContext) -> None:
        """体制を変えた後の首都の建物の建て替え。

        建て替えのために買えた資材（politics.rebuild_stock）と国の倉庫にある資材を使い、そろったら
        新しい体制の建物にする。足りない分は、次のターンの市場で公共事業費で買う（市場の段の
        _rebuild_orders。ほかの工事より先に）。"""
        state = politics.state_of(island)
        need = state.get("rebuild")
        if not need:
            return
        office = capital_office(island, ctx.defs)
        bought = politics.rebuild_stock(island)
        for good, qty in sorted(need.items()):
            took = stock.take(bought, good, qty)
            took += stock.take(island.warehouse, good, qty - took)
            if took and office is not None:
                value = money_of(island.economy.prices.get(good, 0), took)
                office.economy["capital"] = office.economy.get("capital", 0) + value
            need[good] = qty - took
            if need[good] <= 0:
                del need[good]
        if not need:
            state.pop("rebuild", None)
            state.pop("building", None)
            for good in list(bought):  # 買いすぎた分は国の倉庫に移す
                stock.put_batches(island.warehouse, good, bought.pop(good))
            building = politics.capital_building(island, ctx.defs)
            args = {"building": f"capital_building_{building}" if building else "none"}
            island.log.append(LogEntry("capital_rebuilt", args))


def elect(island: IslandState, ctx: StageContext, announce: bool = True) -> None:
    """選挙（議会の解散も）: 今の支持率で与党の議席を選び直し、次の選挙を決める。共和制では元首の
    選挙も兼ね、支持率が politics.republic.support を超えたら次の politics.republic.turns ターンは
    政情の目標を足す。"""
    config = ctx.config
    state = politics.state_of(island)
    support = state.get("support", state.get("stability", 50.0))
    state["seats"] = round(support / 100, 4)
    state["elected"] = support
    state["election"] = ctx.turn + config.get("politics.election_turns")
    history = state.setdefault("elections", [])
    history.insert(0, {"turn": ctx.turn, "support": round(support, 1)})
    del history[config.get("politics.history") :]
    if not announce:
        return
    args = {"support": round(support), "seats": round(support)}
    island.log.append(LogEntry("election_held", args))
    found = politics.traits(island, ctx.defs)
    if "election_bonus" in found:
        if support > config.get("politics.republic.support"):
            state["bonus_until"] = ctx.turn + config.get("politics.republic.turns")
            island.log.append(LogEntry("election_head_of_state_strong", args))
        else:
            island.log.append(LogEntry("election_head_of_state", args))


# --- 暴動の部品 ---------------------------------------------------------------------


def urban_facilities(island: IslandState, ctx: StageContext) -> list[Facility]:
    """暴動で止まりうる施設: 町（politics.riot.urban）以上の住宅地から politics.riot.radius マス
    の中の、建っていてマスを持つ住宅でない施設。"""
    defs, config = ctx.defs, ctx.config
    settings = config.get("politics.riot")
    floor = defs.settlements[settings["urban"]]["min_population"]
    urban: set[tuple[int, int]] = set()
    for tile in island.tiles:
        if tile.population >= floor:
            urban.update(within(island, (tile.x, tile.y), settings["radius"]))
    return [
        f
        for f in sorted(island.facilities, key=lambda f: f.id)
        if f.built and fac.on_map(f) and not fac.is_housing(config, f) and (f.x, f.y) in urban
    ]


def police_coverage(island: IslandState, ctx: StageContext) -> float:
    """警察の人手が足りている割合（0〜1）。

    警察で働く人 ÷（住民 × politics.riot.police_per_person）。"""
    want = sum(t.population for t in island.tiles) * ctx.config.get(
        "politics.riot.police_per_person"
    )
    if want <= 0:
        return 0.0
    have = sum(
        f.workers
        for f in island.facilities
        if f.built and ctx.defs.facilities[f.kind].get("police")
    )
    return min(1.0, have / want)


def damage(island: IslandState, ctx: StageContext, f: Facility) -> None:
    """暴動で施設が壊れた: 工事中に戻し、残りの工事（建てるときの資材と人手の
    politics.riot.repair_share）を国の工事として直す。在庫と帳簿はそのまま。"""
    total = fac.total_progress(ctx.defs, f)
    share = ctx.config.get("politics.riot.repair_share")
    f.built = False
    f.progress = max(0, total - round(total * share))
    f.workers = 0
    f.status = ""
    f.ext["repair"] = True
    f.ext.pop("halted_until", None)
    f.ext.pop("halt_reason", None)
    island.log.append(LogEntry("riot_destroyed", {"facility": f.kind, "x": f.x, "y": f.y}))


# --- 議会 ---------------------------------------------------------------------------


def vote_rule(island: IslandState, order: OrderState, ctx: StageContext) -> str | None:
    """命令に議会の採決が要るなら、その決まり（majority・two_thirds）。"""
    rule = ctx.defs.orders.get(order.kind, {}).get("parliament")
    if rule is None:
        return None
    if order.kind == "set_taxes":
        points = ctx.config.get("politics.parliament.tax_points")
        policy = island.policy
        big = any(
            abs(order.payload[k] - getattr(policy, k) * 100 / BASIS) > points
            for k in TAXES
            if k in order.payload
        )
        if not big:
            return None
    return str(rule)


def parliament_gate(island: IslandState, order: OrderState, ctx: StageContext) -> None:
    """命令の段の関所: 民主主義の島で、議会の要る命令を採決する。否決なら OrderFailed。"""
    if not island.has_economy or not politics.has_parliament(island, ctx.defs):
        return
    rule = vote_rule(island, order, ctx)
    if rule is None:
        return
    config = ctx.config
    state = politics.state_of(island)
    if "seats" not in state:  # 政情の段がまだ一度も動いていない（建国した最初のターン）
        support = 50.0
        state.update({"seats": 0.5, "elected": support, "support": support})
    swing = config.get("politics.parliament.swing")
    yes = state["seats"] + (state.get("support", 50.0) - state.get("elected", 50.0)) / 100 * swing
    yes = max(0.0, min(1.0, yes))
    need = config.get(f"politics.parliament.{rule}")
    passed = yes >= need if rule == "two_thirds" else yes > need
    votes = state.setdefault("votes", [])
    votes.insert(
        0,
        {
            "turn": ctx.turn,
            "order": order.kind,
            "yes": round(yes, 3),
            "rule": rule,
            "passed": passed,
        },
    )
    del votes[config.get("politics.history") :]
    args = {"order": order.kind, "yes": round(yes * 100), "need": round(need * 100)}
    if not passed:
        raise OrderFailed("reason_parliament_rejected", **args)
    island.log.append(LogEntry("parliament_passed", args))


# --- 命令: 報道と集会・議会の解散・元首の名前・労働の動員 ------------------------------


def check_press(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    if payload["mode"] != politics.FREE and not politics.trait(island, defs, "press_control", True):
        raise OrderFailed("reason_press_control_forbidden")


def set_press(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
    """報道と集会の扱い（自由・制限・統制）。強めるとその場で政情がその差だけ上がり、ゆるめると
    たまった不満をまとめて引く。"""
    state = politics.state_of(island)
    bonus = ctx.config.get("politics.press.bonus")
    old = state.get("press", politics.FREE)
    new = payload["mode"]
    state["press"] = new
    value = state.get("stability")
    change = bonus.get(new, 0) - bonus.get(old, 0)
    if value is not None and change > 0:
        state["stability"] = min(100.0, value + change)
    elif value is not None and change < 0 and state.get("grievance", 0) > 0:
        held = state.pop("grievance")
        state["stability"] = max(0.0, value - held)
        island.log.append(LogEntry("politics_grievance_released", {"amount": round(held)}))
    island.log.append(LogEntry("press_set", {"mode": f"press_{new}"}))


def check_dissolve(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    if not politics.has_parliament(island, defs):
        raise OrderFailed("reason_no_parliament")


def dissolve(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
    """議会の解散: 今の支持率で議席を選び直す（次の選挙はそこから数え直す）。"""
    elect(island, ctx)


def check_rename_leader(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    if island.regime.get("head_of_state") != "republic":
        raise OrderFailed("reason_not_republic")


def rename_leader(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
    """共和制の元首の名前（RP。選挙で選ばれた新しい元首の名前をプレイヤーが決める）。"""
    island.names["leader"] = payload["name"].strip()
    island.log.append(LogEntry("leader_renamed", {"name": island.names["leader"]}))


def check_mobilize(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    if not politics.trait(island, defs, "mobilization", False):
        raise OrderFailed("reason_mobilization_forbidden")


def mobilize(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
    """労働の動員（全体主義）: 働く人の割合を politics.mobilization.share 増やし、失業者を国営施設に
    動員の賃金で割り当てる（雇用の段）。"""
    state = politics.state_of(island)
    if payload["on"]:
        state["mobilized"] = ctx.config.get("politics.mobilization.share")
        island.log.append(LogEntry("mobilization_started", {}))
    else:
        state.pop("mobilized", None)
        island.log.append(LogEntry("mobilization_stopped", {}))


# --- 命令: 憲法改正と経済体制の変更 ---------------------------------------------------


def _check_change(island: IslandState, defs: Defs, axes: dict[str, str]) -> None:
    # 混乱期間（政情の段が終わりのターンに消す）の間は、体制を変えられない
    t = island.ext.get(politics.KEY, {}).get("transition")
    if t is not None:
        raise OrderFailed("reason_in_transition", until=t["until"])
    choices = defs.regime_axes()
    changed = False
    for axis, value in axes.items():
        if value not in choices.get(axis, ()):
            raise OrderFailed("reason_unknown_regime")
        changed = changed or island.regime.get(axis) != value
    if not changed:
        raise OrderFailed("reason_same_regime")


def check_amend(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    _check_change(
        island, defs, {k: payload[k] for k in ("politics", "head_of_state") if k in payload}
    )


def amend(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
    """憲法改正（政治の仕組みと国家元首の形）。混乱期間に入り、政治の仕組みが変われば首都の建物を
    新しい体制のものに建て替える（資材は公共事業費で買う）。"""
    defs = ctx.defs
    state = politics.state_of(island)
    old_building = politics.capital_building(island, defs)
    old = dict(island.regime)
    for axis in ("politics", "head_of_state"):
        if axis in payload:
            island.regime[axis] = payload[axis]
    _start_transition(island, ctx, "politics")
    if island.regime.get("politics") != old.get("politics"):
        office = capital_office(island, defs)
        if office is not None:
            materials = defs.facilities[office.kind]["construction"].get("materials", {})
            state["rebuild"] = {g: int(q) for g, q in sorted(materials.items()) if q > 0}
            state["building"] = old_building
    if (
        not politics.trait(island, defs, "press_control", True)
        and state.get("press", "free") != "free"
    ):
        set_press(island, {"mode": politics.FREE}, ctx)
    if not politics.trait(island, defs, "mobilization", False):
        state.pop("mobilized", None)
    if politics.has_parliament(island, defs):
        elect(island, ctx)
    if politics.trait(island, defs, "monarch", False):
        state.setdefault("monarch_age", ctx.config.get("politics.monarch_age"))
    else:
        state.pop("monarch_age", None)
    args = {
        "politics": island.regime.get("politics", ""),
        "head_of_state": island.regime.get("head_of_state", ""),
    }
    island.log.append(LogEntry("constitution_amended", args))


def check_change_economy(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    _check_change(island, defs, {"economy": payload["economy"]})


def change_economy(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
    """経済体制の変更。計画経済に移ると、民営の施設と民間銀行を帳簿の純資産で国有化し（代金は
    中央銀行が引き受けた国債で払う。今の国有化の命令と同じ仕訳）、資産を持つ層を一般の層に
    まとめ、公定価格を今の値段から始める。市場経済や混合経済に移ると、国営施設はそのまま残り
    （民営化は命令で）、資産を持つ層を置き、民営工場と民間銀行の参入が始まる。"""
    defs = ctx.defs
    before = island.regime.get("economy")
    island.regime["economy"] = payload["economy"]
    _start_transition(island, ctx, "economy")
    if planning.is_planned(island, defs):
        _nationalize_all(island, ctx)
        _merge_owners(island, ctx)
        planning.start(island, ctx.config, ctx.turn)
    else:
        planning.stop(island)
        _add_owners(island, ctx)
    island.policy.bank_entry = ""  # 民間銀行の参入は新しい経済体制の既定
    args = {"before": before or "", "economy": payload["economy"]}
    island.log.append(LogEntry("economy_changed", args))


def _start_transition(island: IslandState, ctx: StageContext, kind: str) -> None:
    turns = ctx.config.get("politics.transition.turns")
    state = politics.state_of(island)
    state["transition"] = {"since": ctx.turn, "until": ctx.turn + turns, "kind": kind}
    state["productivity"] = round(1 - ctx.config.get("politics.transition.productivity"), 4)


def _nationalize_all(island: IslandState, ctx: StageContext) -> None:
    """民営の施設と民間銀行を帳簿の純資産で国有化する。代金の合計は、中央銀行が引き受ける国債で
    国庫に用意してから払う。民営工場の工事は取りやめる（参入の工事の取りやめと同じ）。"""
    from hakoniwa_core.modules.construction import close_accounts
    from hakoniwa_core.modules.failures import close_foreclosure
    from hakoniwa_core.modules.firms import EntryExit, _move_equity, pay_seller, restart

    defs, config = ctx.defs, ctx.config
    owners = household(OWNERS)
    for f in sorted(island.facilities, key=lambda f: f.id):
        if f.owner == OWNER_PRIVATE and not f.built:
            EntryExit._abandon(island, ctx, f)
    prices: list[tuple[Facility, str, int]] = []
    for f in sorted(island.facilities, key=lambda f: f.id):
        if f.owner != OWNER_PRIVATE and "foreclosed" not in f.economy:
            continue
        if f.kind == config.get("banking.facility"):
            entity = ledger.bank_entity(f.id)
            price = max(0, banks.net_worth(island, config, entity, ctx.turn))
        else:
            entity = facility_entity(f.id)
            seized = f.economy.get("foreclosed")
            price = seized["price"] if seized else banks.net_assets(island, f, entity)
        prices.append((f, entity, price))
    total = sum(p for _, _, p in prices)
    if total <= 0 and not prices:
        return
    term = max(spec["turns"] for spec in defs.bond_terms().values())
    underwrite(island, total, term, island.policy.policy_rate, ctx.turn)
    payer = Payer(ledger.GOV)
    count = 0
    for f, entity, price in prices:
        seized = f.economy.get("foreclosed")
        if seized:
            seller = seized.get("bank")
            close_foreclosure(island, f)
            funds.pay(island, payer, entity, price, "nationalization")
            pay_seller(island, entity, seller, price)
            restart(island, ctx, f, OWNER_STATE, price)
        else:
            funds.pay(island, payer, owners, price, "nationalization")
            _move_equity(island, entity, owners, ledger.GOV)
            f.owner = OWNER_STATE
            for key in ("closing", "idle_turns", "loss_streak"):
                f.economy.pop(key, None)
        count += 1
        if f.kind == config.get("banking.facility"):
            continue
        # 経済体制の既定の会計（計画経済は国庫直轄）にする
        wanted = default_accounting(defs, island, f.kind, OWNER_STATE)
        if wanted == ACCOUNTING_TREASURY and f.accounting == ACCOUNTING_SELF:
            close_accounts(island, f)
            f.accounting = ACCOUNTING_TREASURY
    island.log.append(LogEntry("nationalized_all", {"count": count, "price": total}))


def _merge_owners(island: IslandState, ctx: StageContext) -> None:
    """資産を持つ層を一般の層にまとめる（預金も一緒に移す）。"""
    households = island.economy.households
    if OWNERS not in households:
        return
    persons = households[OWNERS]["persons"]
    money = ledger.money(island, household(OWNERS))
    ledger.pay(island, household(OWNERS), household(GENERAL), money, "layer_transfer")
    del households[OWNERS]
    households[GENERAL]["persons"] += persons


def _add_owners(island: IslandState, ctx: StageContext) -> None:
    """資産を持つ層を置く（計画経済から移ったとき）。人数は住民の割合で、一般の層から預金と一緒に移す。"""
    households = island.economy.households
    if OWNERS in households:
        return
    spec = layers(ctx.config).get(OWNERS)
    if spec is None or "share" not in spec:
        return
    households[OWNERS] = {"persons": 0, "share": spec["share"], "income": []}
    population = sum(h["persons"] for h in households.values())
    move_people(island, split_persons(island, ctx.defs, population))


def register(registry: Registry) -> None:
    registry.add_stage(Politics())
    registry.add_formula("stability", stability)
    registry.add_order_gate(parliament_gate)
    registry.add_order(OrderHandler("set_press", execute=set_press, check=check_press))
    registry.add_order(OrderHandler("dissolve_parliament", execute=dissolve, check=check_dissolve))
    registry.add_order(
        OrderHandler("rename_leader", execute=rename_leader, check=check_rename_leader)
    )
    registry.add_order(OrderHandler("mobilize_labor", execute=mobilize, check=check_mobilize))
    registry.add_order(OrderHandler("amend_constitution", execute=amend, check=check_amend))
    registry.add_order(
        OrderHandler("change_economy", execute=change_economy, check=check_change_economy)
    )
