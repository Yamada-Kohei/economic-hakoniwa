# 使っている外部のもの

外部のライブラリや素材を足すときは、同じ変更でこの表も更新してください。
どれもこのプロジェクトの MIT ライセンスと両立するものだけを使います。

## Python のライブラリ（pip で入れるもの。リポジトリには含めません）

| 名前 | 用途 | ライセンス | 出典 |
|---|---|---|---|
| Django | サーバー | BSD-3-Clause | https://www.djangoproject.com/ |
| PyYAML | 定義データの読み込み | MIT | https://pyyaml.org/ |
| jsonschema | 定義データの検証 | MIT | https://github.com/python-jsonschema/jsonschema |
| psycopg（任意） | PostgreSQL への接続 | LGPL-3.0 | https://www.psycopg.org/ |
| pytest（開発用） | 自動テスト | MIT | https://pytest.org/ |
| pytest-django（開発用） | 自動テスト | BSD-3-Clause | https://github.com/pytest-dev/pytest-django |
| Ruff（開発用） | 書式と誤りの検査 | MIT | https://github.com/astral-sh/ruff |

psycopg は LGPL ですが、ライブラリとして import して使うだけで、コードを取り込んだり改変したりはしないため、
このプロジェクトを MIT で公開することとは衝突しません。PostgreSQL を使うときだけ入れます。

## ブラウザ側

外部のライブラリは使っていません。

## 絵・音などの素材

まだありません。マップは色付きのひし形と立方体をコードで描いています。
出典とライセンスが分からない素材は使いません。

## GitHub Actions

| 名前 | ライセンス |
|---|---|
| actions/checkout | MIT |
| actions/setup-python | MIT |
