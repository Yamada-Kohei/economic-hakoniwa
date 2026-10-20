"""賞（フェーズ4-1）: 島の一覧と国の公開ページに並べる印。

賞は記録を持たず、そのつどターンログと統計から数える（巻き戻せば賞も巻き戻る）。しきい値は
game.yaml の ``awards``。元の箱庭諸島の賞の名前を使う（繁栄賞・超繁栄賞・究極繁栄賞・平和賞・
災難賞・ターン杯）。

- 繁栄賞・超繁栄賞・究極繁栄賞: 人口がしきい値に届いたことがある（いちばん高い段だけ出す）
- 平和賞: 戦争で殺した相手の数（累計）が ``peace_kills`` 人に届いたとき。元の箱庭諸島の平和賞は
  戦争で殺した人数に贈るもので、名前と中身の食い違いは皮肉。数は国の記録 ``ext["history"]["kills"]``
  （戦闘の仕組み 4-3 が足す）を読む
- 平穏賞: 災害・暴動・反乱・戦争の始まりと終わりが、建国から数えて続けて ``calm_turns`` ターン
  なく、いま戦争中でもない
- 災難賞: 災害・暴動・反乱・飢え・倒産・債務不履行・銀行の破綻の出来事が ``calamity_count`` 件以上
- ターン杯: 休止中でない国のうち建国がいちばん古い 1 国（``cup_turns`` ターン以上続いたとき）
"""

from __future__ import annotations

from typing import Any

from django.db.models import Count, Max, Q

from server.game import get_game
from server.turns.models import TurnLog
from server.world import models as m

PROSPERITY = ("繁栄賞", "超繁栄賞", "究極繁栄賞")

# 災難の出来事（ターンログの文の種類。名前が _ で終わるものは、その始まりの種類全部）
CALAMITIES = (
    "disaster_",
    "politics_riot",
    "politics_rebellion",
    "riot_destroyed",
    "population_starved",
    "population_hunger_deaths",
    "facility_bankrupt",
    "firm_bankrupt",
    "bank_failed_",
    "bond_default",
)
# 平穏を破る出来事（災害・暴動・反乱・戦争の始まり）
UNREST = (
    "disaster_",
    "politics_riot",
    "politics_rebellion",
    "politics_rebellion_warning",
    "riot_destroyed",
    "war_declared",
    "war_declared_on",
    "war_resumed",
    "war_ended",
)


def _kinds(kinds: tuple[str, ...]) -> Q:
    q = Q()
    for kind in kinds:
        q |= Q(kind__startswith=kind) if kind.endswith("_") else Q(kind=kind)
    return q


def settings() -> dict[str, Any]:
    config = get_game().config
    return {
        "prosperity": [int(n) for n in config.get("awards.prosperity")],
        "peace_kills": int(config.get("awards.peace_kills")),
        "calm_turns": int(config.get("awards.calm_turns")),
        "calamity_count": int(config.get("awards.calamity_count")),
        "cup_turns": int(config.get("awards.cup_turns")),
    }


def holders(world: m.World, only: int | None = None) -> dict[int, list[dict[str, Any]]]:
    """島の番号 → その島の賞の一覧（{"id", "name", "detail"}）。賞のない島は載らない。
    only に島の番号を渡すと、その島の賞だけ数える（ターン杯は世界の中でいちばん古い国の 1 つなので、
    どの島かだけを調べる）。"""
    rule = settings()
    islands = list(
        world.islands.values("id", "inactive", "ext__founded_turn", "ext__history__kills")
    )
    island_ids = [i["id"] for i in islands] if only is None else [only]
    levels = sorted(rule["prosperity"])
    fighting = {
        side
        for w in ((world.ext or {}).get("diplomacy") or {}).get("wars", [])
        if w["state"] != "ended"
        for side in (w["attacker"], w["defender"])
    }
    peak = dict(
        m.StatRecord.objects.filter(island__in=island_ids, name="population")
        .values("island")
        .annotate(top=Max("value"))
        .values_list("island", "top")
    )
    calamities = dict(
        TurnLog.objects.filter(_kinds(CALAMITIES), island__in=island_ids)
        .values("island")
        .annotate(n=Count("id"))
        .values_list("island", "n")
    )
    unrest = dict(
        TurnLog.objects.filter(_kinds(UNREST), island__in=island_ids)
        .values("island")
        .annotate(last=Max("turn"))
        .values_list("island", "last")
    )
    founded = {i["id"]: max(1, int(i["ext__founded_turn"] or 1)) for i in islands}
    kills = {i["id"]: int(i["ext__history__kills"] or 0) for i in islands}
    out: dict[int, list[dict[str, Any]]] = {}

    def give(island: int, award: str, name: str, detail: str) -> None:
        out.setdefault(island, []).append({"id": award, "name": name, "detail": detail})

    for i in island_ids:
        level = min(sum(1 for n in levels if peak.get(i, 0) >= n), len(PROSPERITY))
        if level:
            give(
                i,
                f"prosperity_{level}",
                PROSPERITY[level - 1],
                f"人口が {levels[level - 1]:,} 人に届きました",
            )
        if kills[i] >= rule["peace_kills"]:
            give(i, "peace", "平和賞", f"戦争で {kills[i]:,} 人を殺しました")
        quiet = world.turn - max(unrest.get(i, 0), founded[i])
        if quiet >= rule["calm_turns"] and i not in fighting:
            give(i, "calm", "平穏賞", f"{quiet} ターンのあいだ、災害も暴動も戦争もありません")
        if calamities.get(i, 0) >= rule["calamity_count"]:
            give(i, "calamity", "災難賞", f"災難の出来事が {calamities[i]} 件ありました")
    alive = [i for i in islands if not i["inactive"]]
    if alive:
        oldest = min(alive, key=lambda i: (founded[i["id"]], i["id"]))["id"]
        age = world.turn - founded[oldest]
        if age >= rule["cup_turns"] and oldest in island_ids:
            give(oldest, "cup", "ターン杯", f"建国から {age} ターン、いちばん長く続いています")
    return out
