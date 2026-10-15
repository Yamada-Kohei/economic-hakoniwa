from django.urls import path

from server.board import api

# server/urls.py で "api/" の下に置く
urlpatterns = [
    path("board/threads", api.threads, name="api_board_threads"),
    path("board/threads/<int:thread_id>", api.thread, name="api_board_thread"),
    path("board/threads/<int:thread_id>/posts", api.posts, name="api_board_posts"),
]
