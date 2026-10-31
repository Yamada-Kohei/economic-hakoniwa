from django.contrib import admin
from django.urls import include, path

from server.web import server_status

urlpatterns = [
    # 管理者だけの「サーバーの様子」。/admin/ の下に置き、管理画面と同じ守りに入れる
    path("admin/server/", admin.site.admin_view(server_status.view), name="server_status"),
    path("admin/", admin.site.urls),
    path("accounts/", include("server.accounts.urls")),
    path("board/", include("server.board.urls")),
    path("hall/", include("server.hall.urls")),
    path("letters/", include("server.letters.urls")),
    path("api/", include("server.board.api_urls")),
    path("api/", include("server.letters.api_urls")),
    path("api/", include("server.hall.api_urls")),
    path("api/", include("server.feedback.api_urls")),
    path("api/", include("server.country.api_urls")),
    path("", include("server.web.urls")),
]
