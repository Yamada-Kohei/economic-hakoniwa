from django.urls import path

from server.hall import views

urlpatterns = [
    path("", views.hall_page, name="hall"),
]
