from django.contrib.auth import login
from django.shortcuts import redirect, render

from server.accounts.forms import SignupForm


def signup(request):
    """登録。同じ IP からの重複登録の制限はフェーズ1で入れる。"""
    if request.method == "POST":
        form = SignupForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            return redirect("top")
    else:
        form = SignupForm()
    return render(request, "accounts/signup.html", {"form": form})
