"""記念碑（フェーズ4-1。設計書 タブ 2 の PO-47）: 建てられる条件・建てたときの記録・国の歴史。

記念碑は働く人も産出もない施設（facilities.yaml の ``monument``）で、建てられる条件は ``problem``。
条件が読む「国の歴史」は ``island.ext["history"]`` に、記念碑の段（modules/monuments）が毎ターン
ターンのログから書き足します（暴動や災害・戦争の最後のターン、亡くなった人の数、持っている称号）。
建てるときの記録（碑文に刻む数字）は、命令を実行したときに施設の ``ext["monument"]["facts"]`` に
写し、後で変えません。碑文の文は server/web/monuments.py が作ります。
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core.defs import Defs
from hakoniwa_core.state import Facility, IslandState

NOTE_LINES = (
    2  # 遊ぶ人が足せる碑文の行数と、1 行の字数（orders.yaml の note の maxLength と合わせる）
)
NOTE_CHARS = 40
HISTORY = "history"
MONUMENT = (
    "monument"  # 施設の ext: {"facts": {...}, "turn": 建てた（発注した）ターン, "note": 足した文}
)

# 暴動・反乱・災害・戦争の始まりと終わり（平和記念碑の「静かだった期間」を切る出来事）。
# 名前が _ で終わるものは、その始まりの種類全部（server/web/awards.py と同じ並び）
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
WAR_STARTED = ("war_declared", "war_declared_on")
WAR_DEAD = "war"  # 亡くなった人の原因（戦死者は 4-3 の戦闘が add_dead で足す）
KILLS = "kills"  # 戦争で殺した相手の数の累計（平和賞。4-3 の戦闘が add_kills で足す）
HUNGER_DEAD = "hunger"
FREE_POLITICS = "democracy"


def history(island: IslandState) -> dict[str, Any]:
    return island.ext.setdefault(HISTORY, {})


def is_monument(defs: Defs, kind: str) -> bool:
    return "monument" in defs.facilities.get(kind, {})


def founded(island: IslandState) -> int:
    return max(1, int(island.ext.get("founded_turn") or 1))


def add_dead(island: IslandState, cause: str, count: int) -> None:
    """亡くなった人を原因ごとに足す（慰霊碑の条件と碑文。戦死者は戦闘の段が WAR_DEAD で足す）。"""
    if count > 0:
        dead = history(island).setdefault("dead", {})
        dead[cause] = int(dead.get(cause, 0)) + int(count)


def add_kills(island: IslandState, count: int) -> None:
    """戦争で相手を殺した数を足す（平和賞。元の箱庭諸島と同じ、戦争で殺した人数に贈る皮肉の賞）。"""
    if count > 0:
        h = history(island)
        h[KILLS] = int(h.get(KILLS, 0)) + int(count)


def age(island: IslandState) -> int:
    """建国から数えたターン数（前のターンまで）。"""
    return int(history(island).get("turn", founded(island))) - founded(island)


def quiet_turns(island: IslandState) -> int:
    """災害・暴動・反乱・戦争の始まりと終わりが、建国から（前のターンまで）続けてないターン数。"""
    last = max(int(history(island).get("unrest") or 0), founded(island))
    return int(history(island).get("turn", founded(island))) - last


def worst_dead(island: IslandState) -> tuple[str, int]:
    """亡くなった人がいちばん多い原因と人数（なければ ("", 0)）。"""
    dead = history(island).get("dead", {})
    if not dead:
        return ("", 0)
    cause, count = max(sorted(dead.items()), key=lambda kv: kv[1])
    return (cause, int(count))


def has_metropolis(defs: Defs, island: IslandState) -> bool:
    top = defs.settlement_levels()[-1]
    return any(
        f.built
        and defs.facilities[f.kind].get("category") == "housing"
        and f.ext.get("level") == top
        for f in island.facilities
    )


def problem(
    defs: Defs, island: IslandState, kind: str, rule: dict[str, Any] | None = None
) -> tuple[str, dict[str, Any]] | None:
    """記念碑 kind を建てられないときは (理由, 引数)。満たしていれば（記念碑でなくても）None。

    しきい値（game.yaml の monuments）は rule で渡す。命令を受けるときの検査は設定を読めないので、
    記念碑の段が毎ターン歴史に写した値（history["rule"]）を使う（まだ段が動いていない島は建てられない）。
    """
    condition = defs.facilities.get(kind, {}).get("monument")
    if condition is None or condition == "always":
        return None
    rule = rule or history(island).get("rule") or {}

    def need(key: str) -> int | None:
        value = rule.get(key)
        return int(value) if value is not None else None

    if condition in ("founding", "ledger", "verification"):
        turns = need(f"{condition}_turns")
        if turns is None or age(island) < turns:
            return ("reason_monument_age", {"facility": kind, "turns": turns or 0})
    elif condition == "peace":
        turns = need("peace_turns")
        if turns is None or quiet_turns(island) < turns:
            return ("reason_monument_peace", {"facility": kind, "turns": turns or 0})
    elif condition == "battle":
        if "war" not in history(island):
            return ("reason_monument_battle", {"facility": kind})
    elif condition == "cenotaph":
        dead = need("cenotaph_dead")
        if dead is None or worst_dead(island)[1] < dead:
            return ("reason_monument_cenotaph", {"facility": kind, "dead": dead or 0})
    elif condition == "leader":
        if island.regime.get("politics", FREE_POLITICS) == FREE_POLITICS:
            return ("reason_monument_leader", {"facility": kind})
    elif condition == "worker":
        if not history(island).get("titles"):
            return ("reason_monument_worker", {"facility": kind})
    elif condition == "tower" and not has_metropolis(defs, island):
        return ("reason_monument_tower", {"facility": kind})
    return None


def facts(defs: Defs, island: IslandState, kind: str) -> dict[str, Any]:
    """建てるときの記録（碑文に刻む数字。後で変えない）。"""
    condition = defs.facilities[kind].get("monument")
    h = history(island)
    out: dict[str, Any] = {}
    if condition == "founding":
        out = {"founded": founded(island), "leader": island.names.get("leader", "")}
    elif condition == "peace":
        out = {"turns": quiet_turns(island)}
    elif condition == "battle":
        war = h.get("war", {})
        out = {
            "country": war.get("country", ""),
            "turn": war.get("turn", 0),
            "dead": int(h.get("dead", {}).get(WAR_DEAD, 0)),
        }
    elif condition == "cenotaph":
        cause, count = worst_dead(island)
        out = {"cause": cause, "dead": count}
    elif condition == "leader":
        out = {"leader": island.names.get("leader", "")}
    elif condition == "ledger":
        out = dict(h.get("first_entry", {}))
    elif condition == "worker":
        titles = h.get("titles", {})
        industry = sorted(titles, key=lambda i: (titles[i], i))[0] if titles else ""
        out = {"industry": industry, "turn": titles.get(industry, 0)}
    return out


def inscription_note(note: str) -> str:
    """足した文を整える（空白を詰め、行ごとに空白を削る）。"""
    lines = [" ".join(line.split()) for line in note.replace("\r\n", "\n").split("\n")]
    return "\n".join(line for line in lines if line)


def stamp(defs: Defs, island: IslandState, f: Facility, turn: int, note: str = "") -> None:
    """建てた施設 f に、記念碑の記録（ext["monument"]）を写す。"""
    f.ext[MONUMENT] = {
        "facts": facts(defs, island, f.kind),
        "turn": turn,
        "note": inscription_note(note),
    }
