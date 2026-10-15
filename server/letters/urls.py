from django.urls import path

from server.letters import views

urlpatterns = [
    path("", views.inbox, name="letters"),
    path("sent/", views.sent, name="letters_sent"),
    path("new/", views.new_letter, name="letter_new"),
    path("<int:letter_id>/", views.letter_page, name="letter"),
]
