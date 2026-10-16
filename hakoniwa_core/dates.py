"""暦。1ターンはゲームの中の1か月で、TURNS_PER_YEAR ターンで1年です。

「1年＝12ターン」はここだけに書きます。年率の利子・年の予算・直近1年の統計なども、
ここの TURNS_PER_YEAR を使います。

年と月は、ターン1 を暦の最初の年の1月として数えます。ターンが TURNS_PER_YEAR の倍数のときが
12月（年の終わり）で、年に1度の処理（政情の年の処理、経験の薄れなど）はこのターンに動きます
（is_year_end）。世界の暦の名前と最初の年は game.yaml の calendar で決めます（world_calendar）。
ターン 0 は暦の始まる前です。

国ごとの数え方（建国から数える年・元号。フェーズ4）は、同じ Calendar を、数え始めるターン
（start_turn）を変えて作れます。月は世界の暦と同じで、数え始めたターンの年を最初の年（元年）とし、
次の1月で2年になります（現実の元号や民国紀元と同じ数え方）。

暦の文面（「箱庭暦32年11月（ターン 383）」）は、定義データの messages から作ります
（hakoniwa_core/log.py の format_turn）。
"""

from __future__ import annotations

from dataclasses import dataclass

from hakoniwa_core.config import GameConfig

TURNS_PER_YEAR = 12  # 1年のターン数（1ターン＝1か月）


def year_index(turn: int) -> int:
    """ターン1 の年を 0 として数えた、ターン turn の年。"""
    return (turn - 1) // TURNS_PER_YEAR


def month_of(turn: int) -> int:
    """ターン turn の月（1〜12）。"""
    return (turn - 1) % TURNS_PER_YEAR + 1


def is_year_end(turn: int) -> bool:
    """ターン turn が年の終わり（12月）か。年に1度の処理はこのターンに動く。"""
    return turn % TURNS_PER_YEAR == 0


@dataclass(frozen=True)
class Calendar:
    """暦の数え方。世界の暦は start_turn が 1。"""

    name: str  # 暦の名前（「箱庭暦」）
    first_year: int = 1  # start_turn の年を何年と数えるか
    start_turn: int = 1  # 数え始めるターン（国ごとの数え方なら、建国や改元のターン）

    def date(self, turn: int) -> tuple[int, int] | None:
        """ターン turn の（年, 月）。数え始める前なら None。"""
        if turn < self.start_turn:
            return None
        year = self.first_year + year_index(turn) - year_index(self.start_turn)
        return year, month_of(turn)


def world_calendar(config: GameConfig) -> Calendar:
    """game.yaml の calendar（name と first_year）から、世界の暦を作る。"""
    name = config.get("calendar.name")
    first_year = config.get("calendar.first_year")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("game.yaml の calendar.name には暦の名前を書きます")
    if not isinstance(first_year, int) or isinstance(first_year, bool) or first_year < 1:
        raise ValueError("game.yaml の calendar.first_year は 1 以上の整数です")
    return Calendar(name=name.strip(), first_year=first_year)
