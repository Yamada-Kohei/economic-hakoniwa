"""国際会議場の画面。読むのは誰でも、声明を出せるのは自分の島を持つ人だけ。"""

from __future__ import annotations

from django import forms
from django.conf import settings
from django.core.paginator import Paginator
from django.shortcuts import redirect, render

from server.hall import services
from server.turns.services import get_world
from server.web import diplomacy


class StatementForm(forms.Form):
    title = forms.CharField(label="題名", max_length=services.TITLE_MAX)
    body = forms.CharField(
        label="本文", max_length=services.BODY_MAX, widget=forms.Textarea(attrs={"rows": 5})
    )


def hall_page(request):
    """国際会議場: 戦争の状態・効いている条約・声明と出来事の並び。島を持つ人には声明の入力欄も。"""
    world = get_world()
    island = services.own_island(request.user)
    form = StatementForm(request.POST or None) if island is not None else None
    if request.method == "POST":
        if form is None:
            return redirect("login")
        if form.is_valid():
            data = form.cleaned_data
            try:
                services.post_statement(request.user, island, data["title"], data["body"])
            except services.HallError as e:
                form.add_error(None, e.text)
            else:
                return redirect("hall")
    page = Paginator(services.timeline(world), services.PAGE_SIZE).get_page(request.GET.get("page"))
    context = {
        "page": page,
        "form": form,
        "limit": settings.HAKONIWA_HALL_PER_DAY,
        **diplomacy.world_view(world),
    }
    return render(request, "hall/hall.html", context)
