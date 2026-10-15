from django.urls import path

from server.letters import api

# server/urls.py で "api/" の下に置く
urlpatterns = [
    path("letters", api.letters, name="api_letters"),
    path("letters/<int:letter_id>", api.letter, name="api_letter"),
]
