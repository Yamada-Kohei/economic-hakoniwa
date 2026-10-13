"""お金と市場の保存（フェーズ2）。計算はコア（hakoniwa_core）にあり、ここは保存だけを受け持つ。

勘定・国債・融資は島の今の状態なので、ターンごとに島の行を消して書き直す。仕訳・約定・市場の記録・
施設の帳簿はターンごとの記録で、古い行は game.yaml の records の期間を過ぎたら消す。
主体・勘定・財の名前は文字列で持ち、外部キーにしない（追加パックで種類が増えても移行が要らない）。
"""

from __future__ import annotations

from django.db import models

from server.world.models import Island


class Account(models.Model):
    """勘定の残高。名前は「主体/持ち物/相手」（例: hh:general/deposit/bank:12）。"""

    island = models.ForeignKey(Island, on_delete=models.CASCADE, related_name="accounts")
    name = models.CharField("勘定", max_length=128)
    balance = models.BigIntegerField("残高")

    class Meta:
        verbose_name = verbose_name_plural = "勘定"
        constraints = [
            models.UniqueConstraint(fields=["island", "name"], name="unique_account_name"),
        ]

    def __str__(self) -> str:
        return f"{self.island} {self.name}"


class Bond(models.Model):
    """国債の銘柄。持ち手ごとの額面は勘定（<持ち手>/bond:<番号>/gov）で持つ。"""

    island = models.ForeignKey(Island, on_delete=models.CASCADE, related_name="bonds")
    number = models.IntegerField("島の中の番号")
    issued = models.IntegerField("発行したターン")
    term = models.IntegerField("期間（ターン）")
    rate = models.IntegerField("年の利率（1万分の1）")
    face = models.BigIntegerField("発行額")

    class Meta:
        verbose_name = verbose_name_plural = "国債"
        constraints = [
            models.UniqueConstraint(fields=["island", "number"], name="unique_bond_number"),
        ]

    def __str__(self) -> str:
        return f"{self.island} 国債 {self.number}"


class Loan(models.Model):
    """融資の1件（フェーズ2-2）。残高は勘定（<貸し手>/loan:<番号>/<借り手>）で持つ。"""

    island = models.ForeignKey(Island, on_delete=models.CASCADE, related_name="loans")
    number = models.IntegerField("島の中の番号")
    lender = models.CharField("貸し手", max_length=64)
    borrower = models.CharField("借り手", max_length=64)
    purpose = models.CharField("使い道", max_length=32)
    principal = models.BigIntegerField("貸した額")
    term = models.IntegerField("返す期間（ターン）")
    start = models.IntegerField("貸したターン")
    rate = models.IntegerField("年利（1万分の1）", default=0)
    installment = models.BigIntegerField("毎ターン返す元金", default=0)
    repay_from = models.IntegerField("返し始めるターン", default=0)
    arrears = models.IntegerField("続けて遅れた回数", default=0)

    class Meta:
        verbose_name = verbose_name_plural = "融資"
        constraints = [
            models.UniqueConstraint(fields=["island", "number"], name="unique_loan_number"),
        ]

    def __str__(self) -> str:
        return f"{self.island} 融資 {self.number}"


class JournalEntry(models.Model):
    """仕訳の1行。debit の残高が amount だけ増え、credit の残高が amount だけ減る。"""

    island = models.ForeignKey(Island, on_delete=models.CASCADE, related_name="journal")
    turn = models.IntegerField("ターン")
    kind = models.CharField("種類", max_length=64)
    debit = models.CharField("借り方", max_length=128)
    credit = models.CharField("貸し方", max_length=128)
    amount = models.BigIntegerField("金額")

    class Meta:
        verbose_name = verbose_name_plural = "仕訳"
        indexes = [models.Index(fields=["island", "turn"])]

    def __str__(self) -> str:
        return f"{self.island} ターン {self.turn} {self.kind}"


class Trade(models.Model):
    """約定の1件（買い手と売り手の組ごと）。量は 1000 分の 1 単位。"""

    island = models.ForeignKey(Island, on_delete=models.CASCADE, related_name="trades")
    turn = models.IntegerField("ターン")
    good = models.CharField("財", max_length=64)
    buyer = models.CharField("買い手", max_length=64)
    seller = models.CharField("売り手", max_length=64)
    quantity = models.BigIntegerField("量（1000 分の 1）")
    amount = models.BigIntegerField("代金")

    class Meta:
        verbose_name = verbose_name_plural = "約定"
        indexes = [models.Index(fields=["island", "turn"])]

    def __str__(self) -> str:
        return f"{self.island} ターン {self.turn} {self.good}"


class MarketRecord(models.Model):
    """財ごとの、1ターンの市場の記録。量は 1000 分の 1 単位。"""

    island = models.ForeignKey(Island, on_delete=models.CASCADE, related_name="market_records")
    turn = models.IntegerField("ターン")
    good = models.CharField("財", max_length=64)
    price = models.BigIntegerField("値段")
    volume = models.BigIntegerField("売買の量")
    demand = models.BigIntegerField("買いたかった量")
    supply = models.BigIntegerField("売りたかった量")

    class Meta:
        verbose_name = verbose_name_plural = "市場の記録"
        indexes = [models.Index(fields=["island", "good", "turn"])]

    def __str__(self) -> str:
        return f"{self.island} ターン {self.turn} {self.good}"


class FacilityBook(models.Model):
    """施設の帳簿の1ターン分。facility は島の中の施設の番号（取り壊した後も記録は残す）。"""

    island = models.ForeignKey(Island, on_delete=models.CASCADE, related_name="facility_books")
    turn = models.IntegerField("ターン")
    facility = models.IntegerField("施設の番号")
    items = models.JSONField("収支の項目ごとの額", default=dict, blank=True)
    deposit = models.BigIntegerField("預金", default=0)
    debt = models.BigIntegerField("借入", default=0)
    inventory = models.BigIntegerField("在庫の値打ち", default=0)

    class Meta:
        verbose_name = verbose_name_plural = "施設の帳簿"
        indexes = [models.Index(fields=["island", "facility", "turn"])]

    def __str__(self) -> str:
        return f"{self.island} ターン {self.turn} 施設 {self.facility}"
