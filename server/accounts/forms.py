"""登録とアカウントの画面のフォーム。

ログイン名は国のページ・世界地図・順位・公電などで誰にでも見えるので、メールアドレスに見える名前
（@ を含む名前）は登録でも変更でも断る。前からある名前はそのまま使える。
"""

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm

NAME_HELP = (
    "国のページ・世界地図・順位などで誰にでも見えます。メールアドレスや本名は使わないでください。"
    "150 字まで、文字・数字と . + - _ が使えます。"
)


def check_public_name(name: str) -> str:
    if "@" in name:
        raise forms.ValidationError(
            "ログイン名は誰にでも見えるので、メールアドレス（@ を含む名前）は使えません。"
        )
    return name


class SignupForm(UserCreationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].label = "ログイン名"
        self.fields["username"].help_text = NAME_HELP

    def clean_username(self):
        return check_public_name(super().clean_username())


class _PasswordChecked(forms.Form):
    """今のパスワードで本人かを確かめるフォーム（ログインしたまま席を離れた画面を使われないように）。"""

    password = forms.CharField(label="今のパスワード", widget=forms.PasswordInput, strip=False)

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_password(self):
        password = self.cleaned_data["password"]
        if not self.user.check_password(password):
            raise forms.ValidationError("パスワードが違います。")
        return password


class RenameForm(_PasswordChecked):
    username = forms.CharField(label="新しいログイン名", max_length=150, help_text=NAME_HELP)

    field_order = ["username", "password"]

    def clean_username(self):
        name = self.cleaned_data["username"].strip()
        model = get_user_model()
        model._meta.get_field("username").run_validators(name)
        check_public_name(name)
        if name == self.user.username:
            raise forms.ValidationError("今のログイン名と同じです。")
        taken = model.objects.filter(username__iexact=name).exclude(pk=self.user.pk)
        if taken.exists():
            raise forms.ValidationError("そのログイン名はほかの人が使っています。")
        return name


class ReleaseForm(_PasswordChecked):
    island_name = forms.CharField(
        label="手放す国の名前", help_text="間違えて押さないように、国名をそのまま打ってください。"
    )

    field_order = ["island_name", "password"]

    def __init__(self, user, island, *args, **kwargs):
        self.island = island
        super().__init__(user, *args, **kwargs)

    def clean_island_name(self):
        name = self.cleaned_data["island_name"].strip()
        if name != self.island.name:
            raise forms.ValidationError("国名が違います。")
        return name
