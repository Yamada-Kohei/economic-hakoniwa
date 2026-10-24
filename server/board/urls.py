from django.urls import path

from server.board import views

urlpatterns = [
    path("", views.board_index, name="board"),
    path("b/<slug:slug>/", views.board_forum, name="board_forum"),
    path("b/<slug:slug>/log/", views.board_log, name="board_log"),
    path("t/<int:thread_id>/", views.board_thread, name="board_thread"),
    path("search/", views.board_search, name="board_search"),
    path("notices/", views.board_notices, name="board_notices"),
    path("ng/", views.board_ng, name="board_ng"),
    path("<int:thread_id>/", views.board_thread_old_url),  # 以前の住所
]
