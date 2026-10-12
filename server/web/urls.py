from django.urls import path, register_converter

from server.web import views


class SignedIntConverter:
    regex = "-?[0-9]+"

    def to_python(self, value: str) -> int:
        return int(value)

    def to_url(self, value: int) -> str:
        return str(value)


register_converter(SignedIntConverter, "sint")

urlpatterns = [
    path("", views.top, name="top"),
    path("islands/<int:island_id>/", views.island_page, name="island"),
    path("api/defs", views.api_defs, name="api_defs"),
    path("api/islands/<int:island_id>/map", views.api_island_map, name="api_island_map"),
    path("api/islands/<int:island_id>/tiles/<sint:x>,<sint:y>", views.api_tile, name="api_tile"),
]
