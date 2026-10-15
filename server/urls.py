from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("server.accounts.urls")),
    path("board/", include("server.board.urls")),
    path("letters/", include("server.letters.urls")),
    path("api/", include("server.board.api_urls")),
    path("api/", include("server.letters.api_urls")),
    path("", include("server.web.urls")),
]
