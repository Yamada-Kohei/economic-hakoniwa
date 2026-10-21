"""軍事（フェーズ4-2 から。設計書 docs/design/15-military.md）。

置き場所の予定は docs/architecture.md の「フェーズ4」。このパッケージは 4-2 の①で銅の鉱脈の置き方
（`veins.py`）から始まり、②以降で兵と軍の維持、部隊と艦隊、動き、見える範囲が加わります。
"""

# 持ち主だけに見せるターンログの種類（MI-55。ほかの国の人には出さない）。兵・部隊・艦隊の動きと状態
PRIVATE_LOGS = (
    "defense_set",
    "recruited",
    "conscripted",
    "recruit_none",
    "discharged",
    "military_wages_late",
    "military_starved",
    "military_deserted",
    "unit_forming",
    "unit_ready",
    "unit_disbanded",
    "unit_arrived",
    "unit_blocked",
    "unit_no_fuel",
    "unit_order_failed",
    "trench_built",
    "fleet_forming",
    "fleet_ready",
    "fleet_arrived",
    "fleet_no_fuel",
    "battle",
    "battle_on_land",
    "unit_retreated",
    "unit_surrendered",
    "prisoners_taken",
    "prisoners_returned",
    "unit_landed",
    "unit_demobilized_abroad",
    "war_facility_ruined",
)
