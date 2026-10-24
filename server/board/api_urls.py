from django.urls import path

from server.board import api

# server/urls.py で "api/" の下に置く
urlpatterns = [
    path("board/boards", api.boards, name="api_board_boards"),
    path("board/threads", api.threads, name="api_board_threads"),
    path("board/threads/<int:thread_id>", api.thread, name="api_board_thread"),
    path("board/threads/<int:thread_id>/posts", api.posts, name="api_board_posts"),
    path("board/threads/<int:thread_id>/posts/<int:number>", api.one_post, name="api_board_post"),
    path("board/threads/<int:thread_id>/posts/<int:number>/like", api.like, name="api_board_like"),
    path(
        "board/threads/<int:thread_id>/posts/<int:number>/report",
        api.report,
        name="api_board_report",
    ),
    path("board/notices", api.notices, name="api_board_notices"),
    path("board/ng", api.ng, name="api_board_ng"),
    path("board/ng/<int:rule_id>", api.ng_delete, name="api_board_ng_delete"),
    path("board/search", api.search, name="api_board_search"),
]
