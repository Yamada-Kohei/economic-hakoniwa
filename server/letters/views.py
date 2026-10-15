"""手紙の画面（受信箱・送った手紙・1通・書く）。ログインして自分の島を持つ人だけが使う。"""

from __future__ import annotations

from django import forms
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import redirect, render

from server.letters import services
from server.world.models import Island


class LetterForm(forms.Form):
    to = forms.TypedChoiceField(label="宛先", coerce=int)
    subject = forms.CharField(label="題名", max_length=services.SUBJECT_MAX)
    body = forms.CharField(
        label="本文", max_length=services.BODY_MAX, widget=forms.Textarea(attrs={"rows": 10})
    )

    def __init__(self, *args, sender: Island, **kwargs):
        super().__init__(*args, **kwargs)
        # 宛先は同じ世界の、持ち主のいるほかの島（持ち主のいない島は読む人がいない）
        islands = (
            Island.objects.filter(world=sender.world, owner__isnull=False)
            .exclude(pk=sender.pk)
            .select_related("owner")
            .order_by("id")
        )
        self.fields["to"].choices = [("", "（宛先の島を選んでください）")] + [
            (i.id, f"{i.name}（{i.id}・{i.owner.username}）") for i in islands
        ]


def _no_island(request):
    return render(request, "letters/no_island.html")


@login_required
def inbox(request):
    return _box(request, "inbox")


@login_required
def sent(request):
    return _box(request, "sent")


def _box(request, box: str):
    island = services.own_island(request.user)
    if island is None:
        return _no_island(request)
    rows = services.inbox(island) if box == "inbox" else services.sent_box(island)
    page = Paginator(rows, services.PAGE_SIZE).get_page(request.GET.get("page"))
    return render(request, "letters/list.html", {"island": island, "box": box, "page": page})


@login_required
def letter_page(request, letter_id: int):
    letter = services.visible_letter(request.user, letter_id)
    if letter is None:
        raise Http404
    services.open_letter(request.user, letter)
    incoming = letter.recipient.owner_id == request.user.id
    return render(request, "letters/letter.html", {"letter": letter, "incoming": incoming})


@login_required
def new_letter(request):
    """手紙を書く。?to=島の番号 で宛先を、?re=手紙の番号 で返事の宛先と題名を選んでおく。"""
    island = services.own_island(request.user)
    if island is None:
        return _no_island(request)
    initial = {"to": request.GET.get("to", "")}
    reply_to = request.GET.get("re", "")
    original = services.visible_letter(request.user, int(reply_to)) if reply_to.isdigit() else None
    if original is not None:
        other = original.sender if original.recipient_id == island.id else original.recipient
        initial = {"to": other.id, "subject": services.reply_subject(original.subject)}
    form = LetterForm(request.POST or None, sender=island, initial=initial)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            letter = services.send_letter(
                request.user, island, data["to"], data["subject"], data["body"]
            )
        except services.LetterError as e:
            form.add_error(None, e.text)
        else:
            return redirect("letter", letter_id=letter.id)
    context = {"form": form, "island": island, "limit": settings.HAKONIWA_LETTERS_PER_DAY}
    return render(request, "letters/new.html", context)
