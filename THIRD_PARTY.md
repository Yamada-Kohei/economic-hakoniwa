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

マップチップは、下表の新規作成した素材を使用します。
出典とライセンスが分からない素材は使いません。

マップの絵（`server/static/themes/` の下）を足すときは、下の表に1行ずつ足します（決まりは
[docs/map-art.md](docs/map-art.md)）。パスはファイルか、まとめて同じ条件ならフォルダで書けます。
AI で作った絵は、作った道具の名前と、依頼した人を書きます。自動テストが、表に載っていない絵を見つけます。

| パス | 作った人 | ライセンス | 出典 |
|---|---|---|---|
| `server/static/themes/default/sprites/terrain/` | OpenAI の組み込み画像生成（image_gen）。依頼者: Yamada-Kohei | MIT（本プロジェクトの LICENSE） | 2026-10-14 に新規生成。提供された見本は雰囲気の参照に用い、元画像の転載・切り出し・なぞりは行っていません。 |

## GitHub Actions

| 名前 | ライセンス |
|---|---|
| actions/checkout | MIT |
| actions/setup-python | MIT |
