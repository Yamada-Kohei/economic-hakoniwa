"""掲示板に書く・読む決まり。画面（views.py）と JSON API（api.py）の両方が使う。

書けるのはログインした人だけ。荒らしと連投の歯止めは、登録してしばらくたつまで書けない（どんぐり相当）・
連投の間隔・24 時間の書き込みとスレッドの数・同じ文の連投禁止で、数はサーバーの設定
（HAKONIWA_BOARD_*）で決める。管理者（staff）は歯止めの外。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Count, F, Max, Q, QuerySet
from django.utils import timezone

from server.board import identity as ident_mod
from server.board.models import (
    THREAD_POST_LIMIT,
    Board,
    Like,
    NgRule,
    Post,
    Reply,
    Report,
    Thread,
)
from server.board.render import anchor_numbers
from server.letters.services import own_island

TITLE_MAX = Thread._meta.get_field("title").max_length
BODY_MAX = 2000  # 1 つの書き込みの文字数
BODY_LINES_MAX = 40
REASON_MAX = Report._meta.get_field("reason").max_length
PAGE_SIZE = 100  # スレッドの 1 ページの書き込みの数
THREAD_LOG_PAGE_SIZE = 50  # 過去ログの 1 ページのスレッドの数
ACTIVE_THREADS_MAX = 500  # 落ちていないスレッドを 1 度に返す数（板の上限より十分大きい）
SEARCH_PAGE_SIZE = 50
NG_MAX = 100  # 1 人が持てる NG の設定の数
WORD_MAX = 60
IDENTITIES = ("anon", "country", "staff")  # 名無し（名前は任意）・国名で書く・管理者として書く


class BoardError(Exception):
    """書けないわけ。text は画面と API にそのまま出し、status は API の HTTP の状態にする。"""

    def __init__(self, text: str, status: int = 400):
        super().__init__(text)
        self.text = text
        self.status = status


# --- 読む ---------------------------------------------------------------------


def boards() -> QuerySet[Board]:
    return Board.objects.all()


def get_board(slug: str) -> Board | None:
    return Board.objects.filter(slug=slug).first()


def board_summaries() -> list[dict[str, Any]]:
    """板の一覧（板ごとの、落ちていないスレッドの数と最後に書かれた時刻）。"""
    rows = Board.objects.annotate(
        thread_count=Count("threads", filter=Q(threads__archived=False, threads__hidden=False)),
    ).order_by("order", "id")
    latest = dict(
        Thread.objects.filter(archived=False, hidden=False)
        .values_list("board_id")
        .annotate(last=Max("updated_at"))
    )
    return [
        {
            "slug": b.slug,
            "name": b.name,
            "description": b.description,
            "staff_only": b.staff_only,
            "threads": b.thread_count,
            "updated_at": latest.get(b.id),
        }
        for b in rows
    ]


def board_threads(board: Board | None = None, *, archived: bool = False) -> QuerySet[Thread]:
    """板のスレッド（固定が先、あとは上げた順）。archived=True なら過去ログ。"""
    rows = Thread.objects.filter(archived=archived, hidden=False).select_related("board")
    if board is not None:
        rows = rows.filter(board=board)
    return rows.order_by("-bumped_at", "-id") if archived else rows


def recent_threads(limit: int = 10) -> QuerySet[Thread]:
    """どの板でも、最近書き込みがあったスレッド。"""
    return (
        Thread.objects.filter(archived=False, hidden=False)
        .select_related("board")
        .order_by("-updated_at", "-id")[:limit]
    )


def visible_thread(thread_id: int) -> Thread | None:
    """見られるスレッド（過去ログも）。非表示は None。"""
    return Thread.objects.filter(pk=thread_id, hidden=False).select_related("board").first()


def thread_view(thread: Thread) -> dict[str, Any]:
    return {
        "id": thread.id,
        "board": {"slug": thread.board.slug, "name": thread.board.name},
        "title": thread.title,
        "posts": thread.post_count,
        "created_at": thread.created_at,
        "updated_at": thread.updated_at,
        "bumped_at": thread.bumped_at,
        "closed": thread.closed,
        "archived": thread.archived,
        "pinned": thread.pinned,
        "limit": THREAD_POST_LIMIT,
    }


def face_of(post: Post) -> str:
    """書き込みの「書き方の別」: 名無しか、どの国名か。人の NG はこの別ごとに効き、同じ人の名無しと
    国名を結びつけない。"""
    return f"island:{post.island_id or 0}" if post.island_name else "anon"


@dataclass
class NgFilter:
    """見る人の NG の設定。人は誰かを明かさずに保存してあり、ID はその日の分だけ当たる。"""

    persons: set[tuple[int, str]] = field(default_factory=set)  # （アカウント, 書き方の別）
    idents: set[str] = field(default_factory=set)
    words: list[str] = field(default_factory=list)

    @classmethod
    def of(cls, user) -> NgFilter:
        found = cls()
        if user is None or not user.is_authenticated:
            return found
        for rule in NgRule.objects.filter(user=user):
            if rule.kind == NgRule.PERSON and rule.target_id:
                found.persons.add((rule.target_id, rule.word))
            elif rule.kind == NgRule.IDENT:
                found.idents.add(rule.word)
            elif rule.kind == NgRule.WORD:
                found.words.append(rule.word.lower())
        return found

    def matches(self, post: Post) -> bool:
        if post.by_staff or post.hidden:
            return False
        if (post.author_id, face_of(post)) in self.persons or (
            post.ident and post.ident in self.idents
        ):
            return True
        text = f"{post.name}\n{post.island_name}\n{post.body}".lower()
        return any(word in text for word in self.words)


def post_view(
    post: Post,
    *,
    ng: NgFilter | None = None,
    liked: set[int] | None = None,
    replied_by: dict[int, list[int]] | None = None,
) -> dict[str, Any]:
    """画面と API が同じに使う、書き込み 1 件の形。非表示の書き込みは本文を出さない（あぼーん）。"""
    island = None
    if post.island_name:
        island = {"id": post.island_id, "name": post.island_name}
    if post.by_staff:
        name = ident_mod.STAFF_NAME
    elif island:
        name = island["name"]
    else:
        name = post.name or ident_mod.NO_NAME
    return {
        "number": post.number,
        "name": name,
        "trip": post.trip,
        "id": post.ident,
        "island": island,
        "staff": post.by_staff,
        "sage": post.sage,
        "body": None if post.hidden else post.body,
        "created_at": post.created_at,
        "likes": post.like_count,
        "liked": post.pk in liked if liked is not None else False,
        "replied_by": (replied_by or {}).get(post.pk, []),
        "hidden": post.hidden,
        "ng": bool(ng and ng.matches(post)),
    }


def read_posts(
    thread: Thread,
    user=None,
    *,
    page: str | int | None = None,
    latest: int | None = None,
    start: int | None = None,
    end: int | None = None,
) -> dict[str, Any]:
    """スレッドの書き込みを読む。latest=N は最後の N 件、start・end は番号の範囲、他はページ。"""
    rows = thread.posts.select_related("island")
    paging: dict[str, int]
    if latest:
        latest = max(1, min(latest, PAGE_SIZE))
        picked = list(rows.filter(number__gt=thread.post_count - latest))
        paging = {"page": 1, "pages": 1, "count": thread.post_count}
    elif start:
        last = min(end or start + PAGE_SIZE - 1, start + PAGE_SIZE - 1)
        picked = list(rows.filter(number__gte=start, number__lte=last))
        paging = {"page": 1, "pages": 1, "count": thread.post_count}
    else:
        paginator = Paginator(rows, PAGE_SIZE)
        number = paginator.num_pages if page == "last" else page
        found = paginator.get_page(number)
        picked = list(found)
        paging = {"page": found.number, "pages": paginator.num_pages, "count": paginator.count}
    ids = [p.pk for p in picked]
    liked = (
        set(Like.objects.filter(user=user, post_id__in=ids).values_list("post_id", flat=True))
        if user is not None and user.is_authenticated
        else set()
    )
    ng = NgFilter.of(user)
    replied_by: dict[int, list[int]] = {}
    for reply in (
        Reply.objects.filter(target_id__in=ids, source__hidden=False)
        .select_related("source")
        .order_by("source__number")
    ):
        if not ng.matches(reply.source):  # NG の人の返信は「返信:」にも出さない
            replied_by.setdefault(reply.target_id, []).append(reply.source.number)
    return {
        **paging,
        "posts": [post_view(p, ng=ng, liked=liked, replied_by=replied_by) for p in picked],
    }


def one_post(thread: Thread, number: int, user=None) -> dict[str, Any] | None:
    shown = read_posts(thread, user, start=number, end=number)["posts"]
    return shown[0] if shown else None


def page_of(number: int) -> int:
    """書き込みの番号が載るページ。"""
    return (number - 1) // PAGE_SIZE + 1


# --- 書く ---------------------------------------------------------------------


def _clean_body(body: str) -> str:
    body = body.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not body:
        raise BoardError("本文を書いてください")
    if len(body) > BODY_MAX:
        raise BoardError(f"本文は {BODY_MAX} 字までです")
    if body.count("\n") + 1 > BODY_LINES_MAX:
        raise BoardError(f"本文は {BODY_LINES_MAX} 行までです")
    return body


def _clean_title(title: str) -> str:
    title = " ".join(title.replace("\x00", "").split())
    if not title:
        raise BoardError("題名を書いてください")
    if len(title) > TITLE_MAX:
        raise BoardError(f"題名は {TITLE_MAX} 字までです")
    return title


def _check_newcomer(user) -> None:
    """登録してしばらくは、書き込みもいいねも通報もできない（管理者は除く）。"""
    if user.is_staff:
        return
    wait = timedelta(minutes=settings.HAKONIWA_BOARD_NEWCOMER_MINUTES) - (
        timezone.now() - user.date_joined
    )
    if wait > timedelta(0):
        minutes = int(wait.total_seconds() // 60) + 1
        raise BoardError(f"登録してしばらくは使えません。あと {minutes} 分ほどお待ちください", 429)


def _check_writer(user, board: Board, body: str, *, new_thread: bool) -> None:
    """書いてよい人か。登録してすぐ・連投・書きすぎ・同じ文の連投を断る（管理者は除く）。"""
    if not user.is_authenticated:
        raise BoardError("書き込むにはログインしてください", 403)
    if user.is_staff:
        return
    if board.staff_only:
        raise BoardError("この板は管理者だけが書けます", 403)
    _check_newcomer(user)
    now = timezone.now()
    mine = Post.objects.filter(author=user)
    last = mine.order_by("-created_at").values_list("created_at", flat=True).first()
    gap = timedelta(seconds=settings.HAKONIWA_BOARD_INTERVAL_SECONDS)
    if last is not None and now - last < gap:
        seconds = int((gap - (now - last)).total_seconds()) + 1
        raise BoardError(f"連続では書けません。あと {seconds} 秒お待ちください", 429)
    since = now - timedelta(days=1)
    if mine.filter(created_at__gte=since).count() >= settings.HAKONIWA_BOARD_POSTS_PER_DAY:
        raise BoardError("24 時間に書ける数の上限に達しました", 429)
    if mine.filter(created_at__gte=since, body=body).exists():
        raise BoardError("同じ内容の書き込みはできません", 429)
    if (
        new_thread
        and Thread.objects.filter(author=user, created_at__gte=since).count()
        >= settings.HAKONIWA_BOARD_THREADS_PER_DAY
    ):
        raise BoardError("24 時間に立てられるスレッドの数の上限に達しました", 429)


def _writer(user, board: Board, name: str, identity: str) -> dict[str, Any]:
    """名前・トリップ・国名・管理者の印・ID（Post の欄の値）。"""
    if identity not in IDENTITIES:
        raise BoardError("identity は anon・country・staff のどれかです")
    shown, trip = ident_mod.split_name(name or "")
    if not user.is_staff and ident_mod.STAFF_NAME in shown:
        raise BoardError("名前に「運営」は使えません")
    fields: dict[str, Any] = {
        "name": shown,
        "trip": trip,
        "island": None,
        "island_name": "",
        "by_staff": False,
        "ident": ident_mod.day_ident(timezone.localdate(), board.id, user.id),
    }
    if identity == "country":
        island = own_island(user)
        if island is None:
            raise BoardError("国名で書けるのは、自分の国を持つ人だけです", 403)
        # 国名で書く（と運営として書く）ときは ID を付けない（名無しで書いた書き込みと結びつけない）
        fields.update(name="", island=island, island_name=island.name, ident="")
    elif identity == "staff" or (board.staff_only and user.is_staff):
        if not user.is_staff:
            raise BoardError("管理者だけが選べます", 403)
        fields.update(name="", by_staff=True, ident="")
    return fields


def start_thread(
    user,
    board: Board,
    title: str,
    body: str,
    *,
    name: str = "",
    identity: str = "anon",
    sage: bool = False,
) -> Thread:
    """スレッドを立て、1 番を書く。板のスレッドが上限を超えたら、古いものを過去ログへ落とす。"""
    title, body = _clean_title(title), _clean_body(body)
    writer = _writer(user, board, name, identity)
    with transaction.atomic():
        _lock_writer(user)
        _check_writer(user, board, body, new_thread=True)
        thread = Thread.objects.create(board=board, title=title, author=user)
        _write(thread, user, body, writer, sage=False)
        prune(board)
    thread.refresh_from_db()
    return thread


def add_post(
    thread: Thread,
    user,
    body: str,
    *,
    name: str = "",
    identity: str = "anon",
    sage: bool = False,
) -> Post:
    """スレッドの最後に書き込む。1000 に届いたらスレッドを閉じる。"""
    body = _clean_body(body)
    writer = _writer(user, thread.board, name, identity)
    with transaction.atomic():
        _lock_writer(user)
        _check_writer(user, thread.board, body, new_thread=False)
        return _write(thread, user, body, writer, sage=sage)


def _lock_writer(user) -> None:
    """同じアカウントの書き込みを 1 つずつにする（同時に送って連投の歯止めを抜けられないように。
    SQLite は書き込みが元々 1 つずつ）。"""
    get_user_model().objects.select_for_update().filter(pk=user.pk).first()


def _write(thread: Thread, user, body: str, writer: dict[str, Any], *, sage: bool) -> Post:
    locked = Thread.objects.select_for_update().get(pk=thread.pk)
    if locked.archived or locked.hidden:
        raise BoardError("このスレッドは過去ログです", 409)
    if locked.closed or locked.post_count >= THREAD_POST_LIMIT:
        raise BoardError(f"このスレッドは {THREAD_POST_LIMIT} 件に届いて書けません", 409)
    number = locked.post_count + 1
    try:
        with transaction.atomic():
            post = Post.objects.create(
                thread=locked, number=number, author=user, body=body, sage=sage, **writer
            )
    except IntegrityError as e:  # 同時に同じ番号を取った
        raise BoardError("ほかの書き込みと重なりました。もう一度送ってください", 409) from e
    now = timezone.now()
    changes: dict[str, Any] = {
        "post_count": number,
        "updated_at": now,
        "closed": number >= THREAD_POST_LIMIT,
    }
    if not sage:
        changes["bumped_at"] = now
    Thread.objects.filter(pk=locked.pk).update(**changes)
    for field_name, value in changes.items():
        setattr(thread, field_name, value)
    _record_replies(post, user)
    return post


def _record_replies(post: Post, user) -> None:
    """本文の `>>番号` を、返された書き込みとの結びつきにする（自分への返信は知らせない）。"""
    numbers = [n for n in anchor_numbers(post.body) if n < post.number]
    if not numbers:
        return
    targets = Post.objects.filter(thread_id=post.thread_id, number__in=numbers)
    Reply.objects.bulk_create(
        Reply(source=post, target=t, seen=(t.author_id is None or t.author_id == user.id))
        for t in targets
    )


def prune(board: Board) -> int:
    """板のスレッドが上限を超えたぶん、上げた時刻が古いものから過去ログへ落とす（固定は残す）。"""
    live = Thread.objects.filter(board=board, archived=False, pinned=False)
    excess = live.count() - board.max_threads
    if excess <= 0:
        return 0
    ids = list(live.order_by("bumped_at", "id").values_list("pk", flat=True)[:excess])
    return Thread.objects.filter(pk__in=ids).update(archived=True)


# --- いいね・通報・NG ------------------------------------------------------------


def find_post(thread: Thread, number: int) -> Post:
    post = thread.posts.filter(number=number).first()
    if post is None:
        raise BoardError("その番号の書き込みはありません", 404)
    if post.hidden:
        raise BoardError("この書き込みは非表示です", 404)
    return post


def toggle_like(user, post: Post) -> dict[str, Any]:
    """いいねを付ける・外す。自分の書き込みには付けられない。"""
    if not user.is_authenticated:
        raise BoardError("ログインしてください", 403)
    if post.author_id == user.id:
        raise BoardError("自分の書き込みにはいいねできません")
    _check_newcomer(user)
    with transaction.atomic():
        like, created = Like.objects.get_or_create(post=post, user=user)
        if created:
            Post.objects.filter(pk=post.pk).update(like_count=F("like_count") + 1)
        elif like.delete()[0]:
            Post.objects.filter(pk=post.pk, like_count__gt=0).update(like_count=F("like_count") - 1)
    post.refresh_from_db(fields=["like_count"])
    return {"number": post.number, "liked": created, "likes": post.like_count}


def report_post(user, post: Post, reason: str) -> Report:
    """荒らし・不適切な書き込みを管理者に通報する（ゲームの不具合の報告とは別）。"""
    if not user.is_authenticated:
        raise BoardError("ログインしてください", 403)
    if post.author_id == user.id:
        raise BoardError("自分の書き込みは通報できません")
    _check_newcomer(user)
    reason = reason.replace("\x00", "").strip()[:REASON_MAX]
    report, created = Report.objects.get_or_create(
        post=post, reporter=user, defaults={"reason": reason}
    )
    if not created:
        raise BoardError("この書き込みはすでに通報しています", 409)
    return report


def ng_rules(user) -> list[dict[str, Any]]:
    return [
        {
            "id": r.id,
            "kind": r.kind,
            "word": r.word,
            "label": r.label,
            "created_at": r.created_at,
        }
        for r in NgRule.objects.filter(user=user)
    ]


def add_ng(
    user, kind: str, *, word: str = "", thread: Thread | None = None, number: int | None = None
) -> NgRule:
    """NG を足す。word は言葉・ID、人（person）と ID は thread と number で書き込みを指す。"""
    if not user.is_authenticated:
        raise BoardError("ログインしてください", 403)
    mine = NgRule.objects.filter(user=user)
    if mine.count() >= NG_MAX:
        raise BoardError(f"NG は {NG_MAX} 件までです")
    word = word.strip()[:WORD_MAX]
    target = None
    label = ""
    if kind in (NgRule.PERSON, NgRule.IDENT) and thread is not None and number is not None:
        post = find_post(thread, number)
        if post.by_staff:
            raise BoardError("運営の書き込みは NG にできません")
        if post.author_id == user.id:
            raise BoardError("自分は NG にできません")
        label = f"{thread.title} の {post.number} 番"
        if kind == NgRule.PERSON:
            target = post.author
            if target is None:
                raise BoardError("この書き込みの人は NG にできません")
            word = face_of(post)  # 名無しで書いたものか、その国名で書いたものだけに効く
        else:
            word = post.ident
    elif kind not in (NgRule.IDENT, NgRule.WORD):
        raise BoardError("書き込みを指してください（thread と number）")
    if not word:
        raise BoardError("NG にする言葉か ID を書いてください")
    existing = mine.filter(kind=kind, word=word, target=target).first()
    if existing is not None:
        return existing
    return NgRule.objects.create(user=user, kind=kind, word=word, target=target, label=label)


def delete_ng(user, rule_id: int) -> bool:
    return NgRule.objects.filter(user=user, pk=rule_id).delete()[0] > 0


# --- 返信の知らせ ----------------------------------------------------------------


def _unread() -> Q:
    return Q(seen=False, source__hidden=False, source__thread__hidden=False)


def unread_count(user) -> int:
    """未読の返信の数（NG にした人からの返信は数えない）。"""
    if not user.is_authenticated:
        return 0
    rows = Reply.objects.filter(_unread(), target__author=user).select_related("source")
    ng = NgFilter.of(user)
    return sum(1 for r in rows if not ng.matches(r.source))


def notices(user, *, mark: bool = False, limit: int = 50) -> list[dict[str, Any]]:
    """自分の書き込みへの返信（新しい順）。mark=True なら、いま見えているぶんを既読にする。"""
    rows = list(
        Reply.objects.filter(
            target__author=user, source__hidden=False, source__thread__hidden=False
        )
        .select_related("source__thread__board", "source__island", "target")
        .order_by("-id")[:limit]
    )
    ng = NgFilter.of(user)
    shown = [
        {
            "thread": {"id": r.source.thread_id, "title": r.source.thread.title},
            "to": r.target.number,
            "post": post_view(r.source, ng=ng),
            "unread": not r.seen,
        }
        for r in rows
    ]
    if mark:
        Reply.objects.filter(pk__in=[r.pk for r in rows], seen=False).update(seen=True)
    return shown


# --- 検索 ----------------------------------------------------------------------


def search(query: str, board: Board | None = None, user=None) -> QuerySet[Post]:
    """題名か本文に、空白で分けた言葉がすべて入っている書き込み（新しい順。過去ログも）。"""
    terms = [t for t in query.split() if t][:5]
    rows = Post.objects.filter(hidden=False, thread__hidden=False).select_related(
        "thread__board", "island"
    )
    if board is not None:
        rows = rows.filter(thread__board=board)
    for term in terms:
        rows = rows.filter(Q(body__icontains=term) | Q(thread__title__icontains=term))
    return rows.order_by("-created_at", "-id") if terms else rows.none()
