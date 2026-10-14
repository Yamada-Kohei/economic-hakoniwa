from django.urls import path, register_converter

from server.web import api, founding, views


class SignedIntConverter:
    regex = "-?[0-9]+"

    def to_python(self, value: str) -> int:
        return int(value)

    def to_url(self, value: int) -> str:
        return str(value)


register_converter(SignedIntConverter, "sint")

urlpatterns = [
    path("", views.top, name="top"),
    path("found/", founding.page, name="found"),
    path("world/", views.world_map, name="world"),
    path("help/", views.help_page, name="help"),
    path("islands/<int:island_id>/", views.island_page, name="island"),
    path("islands/<int:island_id>/log/", views.turn_log, name="turn_log"),
    path("islands/<int:island_id>/economy/", views.economy_page, name="island_economy"),
    path("islands/<int:island_id>/market/", views.market_page, name="island_market"),
    path("islands/<int:island_id>/facilities/", views.facilities_page, name="island_facilities"),
    path("islands/<int:island_id>/fiscal/", views.fiscal_page, name="island_fiscal"),
    path("islands/<int:island_id>/finance/", views.finance_page, name="island_finance"),
    path("api/defs", api.defs, name="api_defs"),
    path("api/world", api.world, name="api_world"),
    path("api/world/shapes", api.world_shapes, name="api_world_shapes"),
    path("api/islands/<int:island_id>/map", api.island_map, name="api_island_map"),
    path("api/islands/<int:island_id>/tiles/<sint:x>,<sint:y>", api.tile, name="api_tile"),
    path("api/islands/<int:island_id>/economy", api.economy, name="api_island_economy"),
    path("api/islands/<int:island_id>/market/<str:good>", api.market, name="api_island_market"),
    path(
        "api/islands/<int:island_id>/facilities/<int:number>/book",
        api.facility_book,
        name="api_facility_book",
    ),
    path("api/islands/<int:island_id>/finance", api.finance, name="api_island_finance"),
    path("api/islands/<int:island_id>/orders", api.orders, name="api_orders"),
    path("api/islands/<int:island_id>/orders/check", api.check, name="api_order_check"),
    path("api/founding/cells", founding.world_cells, name="api_founding_cells"),
    path("api/founding/random-shape", founding.random_shape, name="api_founding_shape"),
    path("api/founding/terrain", founding.terrain, name="api_founding_terrain"),
    path("api/founding", founding.found, name="api_founding"),
]
