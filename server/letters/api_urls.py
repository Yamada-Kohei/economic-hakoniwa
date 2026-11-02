from django.urls import path

from server.letters import api, push_views

# server/urls.py で "api/" の下に置く
urlpatterns = [
    path("letters", api.letters, name="api_letters"),
    path("letters/push", push_views.subscription, name="api_letters_push"),
    path("letters/<int:letter_id>", api.letter, name="api_letter"),
]
