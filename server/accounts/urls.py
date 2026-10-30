from django.contrib.auth import views as auth_views
from django.urls import path

from server.accounts import views

urlpatterns = [
    path("signup/", views.signup, name="signup"),
    path("login/", auth_views.LoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("settings/", views.account, name="account"),
    path("keys/", views.api_keys, name="api_keys"),
]
