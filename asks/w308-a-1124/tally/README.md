# W-308 設計 3 の集計（読むだけの台本。計算は変えない）

入力: 4 組 w308-a / w308-w / w308-k2 / w308-wk -1124 の結果 probe12-<組>-plan-s<種>-t1200.json（組 A・W・K2・WK、種 17〜24）。

    python -I w308_p12_tally.py A=... (8 本) W=... K2=... WK=... --out w308-p12.md
    python -I w308_p11_tally.py A=... W=... --ref A --cmp W --out w308-p11-AW.md
    python -I w308_p11_tally.py K2=... WK=... --ref K2 --cmp WK --out w308-p11-K2WK.md
    python -I w308_wealthcut_tally.py A=... W=... K2=... WK=... --out w308-wealth.md   # 最初の組 A が順位の基準

p11 の 4 分けの表に「**不一致**」が出たら、直さずにそのまま知らせてください。読み解きは Claude がします。
