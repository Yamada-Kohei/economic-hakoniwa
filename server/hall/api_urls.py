from django.urls import path

from server.hall import api

# server/urls.py で "api/" の下に置く
urlpatterns = [
    path("hall", api.hall, name="api_hall"),
]
