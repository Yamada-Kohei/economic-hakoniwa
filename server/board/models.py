"""掲示板: 板・スレッド・番号付きの書き込み。昔の 2 ちゃんねるの形（設計書 PL-35・PL-51〜PL-55）。

板（Board）の中にスレッド（Thread）が並び、スレッドの中に 1 から始まる番号の書き込み（Post）が並ぶ。
書き込みの番号は欠けない（非表示にした書き込みも番号は残り、画面では「あぼーん」の枠になる）。
誰が書いたかは `author`（アカウント）に残し、管理者だけが見られる。画面と API には名前・トリップ・
ID・国名だけを出す。
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone

THREAD_POST_LIMIT = 1000  # 1 つのスレッドの書き込みの数（超えると書けなくなる）


class Board(models.Model):
    slug = models.SlugField("URL の名前", max_length=20, unique=True)
    name = models.CharField("板の名前", max_length=20)
    description = models.CharField("説明", max_length=200, blank=True)
    order = models.SmallIntegerField("並び順", default=0)
    staff_only = models.BooleanField("管理者だけが書ける", default=False)
    max_threads = models.PositiveSmallIntegerField(
        "スレッドの上限",
        default=100,
        help_text="超えると、最後の書き込みが一番古いものから過去ログへ落ちる",
    )

    class Meta:
        verbose_name = verbose_name_plural = "板"
        ordering = ["order", "id"]

    def __str__(self) -> str:
        return self.name


class Thread(models.Model):
    board = models.ForeignKey(
        Board, verbose_name="板", on_delete=models.CASCADE, related_name="threads"
    )
    title = models.CharField("題名", max_length=100)
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="立てた人", on_delete=models.SET_NULL, null=True
    )
    created_at = models.DateTimeField("立てた時刻", auto_now_add=True)
    updated_at = models.DateTimeField("最後の書き込み", auto_now_add=True)
    bumped_at = models.DateTimeField(
        "上げた時刻", default=timezone.now, db_index=True
    )  # sage では変えない
    post_count = models.PositiveSmallIntegerField("書き込みの数", default=0)  # 非表示の分も数える
    closed = models.BooleanField(
        "書き込み不可", default=False
    )  # 1000 に届いた、または管理者が閉じた
    archived = models.BooleanField("過去ログ", default=False)  # スレッドの上限で落ちた
    pinned = models.BooleanField("固定", default=False)  # 一覧の先頭に置き、過去ログへ落とさない
    hidden = models.BooleanField("非表示", default=False)

    class Meta:
        verbose_name = verbose_name_plural = "スレッド"
        ordering = ["-pinned", "-bumped_at", "-id"]
        indexes = [models.Index(fields=["board", "archived", "-bumped_at"])]

    def __str__(self) -> str:
        return self.title


class Post(models.Model):
    thread = models.ForeignKey(Thread, on_delete=models.CASCADE, related_name="posts")
    number = models.PositiveSmallIntegerField("番号")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="書いた人", on_delete=models.SET_NULL, null=True
    )
    name = models.CharField("名前", max_length=20, blank=True)  # 空なら「名無し」
    trip = models.CharField("トリップ", max_length=12, blank=True)
    ident = models.CharField("ID", max_length=12, blank=True)  # 板ごと・日ごとに変わる
    island = models.ForeignKey(
        "world.Island",
        verbose_name="国名で書いた島",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="board_posts",
    )
    island_name = models.CharField("書いたときの国名", max_length=32, blank=True)
    by_staff = models.BooleanField("管理者として書いた", default=False)
    sage = models.BooleanField("sage", default=False)
    body = models.TextField("本文", max_length=4000)
    created_at = models.DateTimeField("書いた時刻", auto_now_add=True)
    like_count = models.PositiveIntegerField("いいねの数", default=0)
    hidden = models.BooleanField("非表示", default=False)

    class Meta:
        verbose_name = verbose_name_plural = "書き込み"
        ordering = ["thread_id", "number"]
        constraints = [
            models.UniqueConstraint(fields=["thread", "number"], name="unique_post_number")
        ]
        indexes = [models.Index(fields=["author", "-created_at"])]

    def __str__(self) -> str:
        return f"{self.thread} #{self.number}"


class Reply(models.Model):
    """`>>番号` の返信。返された書き込みの持ち主への知らせ（seen）と、「返信: >>12」の元になる。"""

    source = models.ForeignKey(Post, on_delete=models.CASCADE, related_name="replies_made")
    target = models.ForeignKey(Post, on_delete=models.CASCADE, related_name="replies_received")
    seen = models.BooleanField("知らせを見た", default=False)

    class Meta:
        verbose_name = verbose_name_plural = "返信"
        constraints = [models.UniqueConstraint(fields=["source", "target"], name="unique_reply")]

    def __str__(self) -> str:
        return f"{self.source} -> #{self.target.number}"


class Like(models.Model):
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name="likes")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = verbose_name_plural = "いいね"
        constraints = [models.UniqueConstraint(fields=["post", "user"], name="unique_like")]

    def __str__(self) -> str:
        return f"{self.post} <- {self.user}"


class NgRule(models.Model):
    """見ない書き込みの設定（NG）。人は誰かを明かさずに保存し、ID はその日の分だけ効く。"""

    PERSON, IDENT, WORD = "person", "ident", "word"
    KINDS = [(PERSON, "人"), (IDENT, "ID"), (WORD, "言葉")]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="board_ng_rules"
    )
    kind = models.CharField("種類", max_length=8, choices=KINDS)
    word = models.CharField("言葉・ID", max_length=60, blank=True)  # kind が word・ident のとき
    target = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="+",
    )  # kind が person のとき（画面と API には出さない）
    label = models.CharField("覚え書き", max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = verbose_name_plural = "NG の設定"
        ordering = ["id"]

    def __str__(self) -> str:
        return f"{self.user} {self.kind} {self.word}".rstrip()


class Report(models.Model):
    """書き込みの通報（荒らし・不適切な書き込みを管理者へ）。ゲームの不具合の報告とは別の窓口。"""

    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name="reports")
    reporter = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="board_reports"
    )
    reason = models.CharField("理由", max_length=200, blank=True)
    created_at = models.DateTimeField("通報した時刻", auto_now_add=True)
    handled = models.BooleanField("対応済み", default=False)

    class Meta:
        verbose_name = verbose_name_plural = "通報"
        ordering = ["handled", "-created_at"]
        constraints = [models.UniqueConstraint(fields=["post", "reporter"], name="unique_report")]

    def __str__(self) -> str:
        return f"{self.post} {self.reason[:20]}"
