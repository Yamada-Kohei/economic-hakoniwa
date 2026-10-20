from django.urls import path

from server.country import api

# server/urls.py で "api/" の下に置く
urlpatterns = [
    path("islands/<int:island_id>/country/edit", api.edit, name="api_country_edit"),
    path("islands/<int:island_id>/flag", api.flag, name="api_country_flag"),
    path(
        "islands/<int:island_id>/monuments/<int:number>/image",
        api.monument_image,
        name="api_monument_image",
    ),
    path("islands/<int:island_id>/country/notes", api.note, name="api_country_note"),
    path(
        "islands/<int:island_id>/country/notes/<int:note_id>",
        api.note_hide,
        name="api_country_note_hide",
    ),
]
