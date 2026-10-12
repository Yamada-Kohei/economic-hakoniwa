from django.urls import path

from server.board import views

urlpatterns = [
    path("", views.thread_list, name="board"),
    path("new/", views.new_thread, name="board_new"),
    path("<int:thread_id>/", views.thread_page, name="board_thread"),
]
