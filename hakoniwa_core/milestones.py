"""節目（グラフィック強化 案27）: 建国記念日と、人口・GDP の節目を祝う記録。

建国から満○年の記念日（1年目と10年刻み）と、人口が10万人刻み・GDP（1ターン）が桁上がりの
節目を初めて越えたターンに、ターンログへ残します。国のページでは、記念日の年（満○年を
迎えた後の1年間）に「建国○年」の印を出します（server/web/country.py が anniversary_years を
使います。年表への表示は、ログの種類を server/web/top.py の HEADLINES に入れることで、
ほかの主な出来事と同じ並びに載ります）。

越えた節目の記録は ``island.ext["milestones"]`` に持ち、同じ節目を二度祝いません。記録が
まだ無い島は、今の到達点を黙って記録し（既にある国の過去の節目をまとめて祝わない）、
次に越えた節目から残します。計算・バランスは変えず、記録と表示だけです。
"""

from __future__ import annotations

from hakoniwa_core import monuments
from hakoniwa_core.dates import TURNS_PER_YEAR
from hakoniwa_core.state import IslandState, LogEntry

MILESTONES = "milestones"  # island.ext: {"population": 越えた節目, "gdp": 越えた節目}
POPULATION_STEP = 100_000  # 人口の節目の刻み（人）
GDP_MIN = 100_000  # GDP の節目の下限（これ未満の桁は祝わない）


def is_milestone_year(years: int) -> bool:
    """記念日にする年: 1年目と、10年刻み。"""
    return years == 1 or (years > 0 and years % 10 == 0)


def anniversary_years(founded_turn: int, turn: int) -> int | None:
    """いまが記念日の年（建国から満○年を迎えた後の1年間）なら、その年数。違えば None。"""
    age = turn - founded_turn
    if age < TURNS_PER_YEAR:
        return None
    years = age // TURNS_PER_YEAR
    return years if is_milestone_year(years) else None


def _population_mark(population: float) -> int:
    """人口が越えている一番大きい節目（10万人刻み。越えていなければ 0）。"""
    return int(population) // POPULATION_STEP * POPULATION_STEP


def _gdp_mark(gdp: float) -> int:
    """GDP（1ターン）が越えている一番大きい桁（10の冪。GDP_MIN 未満は 0）。"""
    mark = 0
    value = GDP_MIN
    while value <= gdp:
        mark = value
        value *= 10
    return mark


def celebrate(island: IslandState, turn: int) -> None:
    """このターンの統計（island.stats）を見て、節目を迎えていたらターンログに残す。

    記録の段（records）が統計をそろえた後に呼びます。記念日は建国ターンから決まるので
    記録は要りません。人口と GDP は、越えた節目を ext に残して二度祝わないようにします。
    """
    founded = monuments.founded(island)
    age = turn - founded
    if age > 0 and age % TURNS_PER_YEAR == 0:
        years = age // TURNS_PER_YEAR
        if is_milestone_year(years):
            island.log.append(LogEntry("anniversary_founding", {"years": years}))
    marks = island.ext.setdefault(MILESTONES, {})
    population = _population_mark(island.stats.get("population", 0))
    if _crossed(marks, "population", population):
        island.log.append(LogEntry("milestone_population", {"count": population}))
    gdp = _gdp_mark(island.stats.get("gdp", 0))
    if _crossed(marks, "gdp", gdp):
        island.log.append(LogEntry("milestone_gdp", {"amount": gdp}))


def _crossed(marks: dict, key: str, mark: int) -> bool:
    """節目を新しく越えたら True。記録がまだ無い島は、今の到達点を黙って記録して False
    （既にある国の過去の節目をまとめて祝わない）。"""
    if key not in marks:
        marks[key] = mark
        return False
    if mark > int(marks[key]):
        marks[key] = mark
        return True
    return False
