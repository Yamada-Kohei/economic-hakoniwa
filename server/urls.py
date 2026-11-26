from django.contrib import admin
from django.urls import include, path
from django.views.i18n import JavaScriptCatalog

from server.letters import push_views
from server.web import server_status

urlpatterns = [
    # 画面ではなくブラウザ用のファイル。ルートの scope で worker を動かす。
    path("sw.js", push_views.service_worker),
    path("manifest.webmanifest", push_views.manifest),
    # JS の文言の訳（多言語対応の JS の仕組み、docs/i18n.md）。Python 側と
    # 同じ server/locale の djangojs ドメインを、JS の gettext へ渡す。
    path("jsi18n/", JavaScriptCatalog.as_view(), name="javascript-catalog"),
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
