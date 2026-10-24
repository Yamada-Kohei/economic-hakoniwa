from django.urls import path

from server.feedback import api

# server/urls.py で "api/" の下に置く
urlpatterns = [
    path("bug-reports", api.bug_reports, name="api_bug_reports"),
]
