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
| pytest-xdist（開発用） | 自動テストを同時にいくつも回す | MIT | https://github.com/pytest-dev/pytest-xdist |
| execnet（開発用、pytest-xdist が使う） | 自動テストを同時にいくつも回す | MIT | https://github.com/pytest-dev/execnet |
| Ruff（開発用） | 書式と誤りの検査 | MIT | https://github.com/astral-sh/ruff |

psycopg は LGPL ですが、ライブラリとして import して使うだけで、コードを取り込んだり改変したりはしないため、
このプロジェクトを MIT で公開することとは衝突しません。PostgreSQL を使うときだけ入れます。

## ブラウザ側

外部のライブラリは使っていません。

## 絵・音などの素材

マップの絵は下の表のとおりです。絵のないものは、色付きのひし形と立方体をコードで描きます。
出典とライセンスが分からない素材は使いません。

マップの絵（`server/static/themes/` の下）を足すときは、下の表に1行ずつ足します（決まりは
[docs/map-art.md](docs/map-art.md)）。パスはファイルか、まとめて同じ条件ならフォルダで書けます。
AI で作った絵は、作った道具の名前と、依頼した人を書きます。自動テストが、表に載っていない絵を見つけます。

| パス | 作った人 | ライセンス | 出典 |
|---|---|---|---|
| server/static/themes/default/sprites | Claude（tools/sprites/silhouette.py で生成。依頼: セビーリャ） | MIT | このリポジトリ |
| server/static/themes/default/sprites/facility/barracks.svg | OpenAI Codex（幾何の形で作成。依頼: セビーリャ） | MIT | このリポジトリ |
| server/static/themes/default/sprites/facility/naval_base.svg | OpenAI Codex（幾何の形で作成。依頼: セビーリャ） | MIT | このリポジトリ |
| server/static/themes/default/sprites/facility/naval_base@sw.svg | OpenAI Codex（幾何の形で作成。依頼: セビーリャ） | MIT | このリポジトリ |
| server/static/themes/default/sprites/facility/naval_base@ne.svg | OpenAI Codex（幾何の形で作成。依頼: セビーリャ） | MIT | このリポジトリ |
| server/static/themes/default/sprites/facility/naval_base@nw.svg | OpenAI Codex（幾何の形で作成。依頼: セビーリャ） | MIT | このリポジトリ |
| server/static/themes/default/sprites/facility/radar.svg | OpenAI Codex（幾何の形で作成。依頼: セビーリャ） | MIT | このリポジトリ |
| server/static/themes/default/sprites/unit/infantry.svg | OpenAI Codex（幾何の形で作成。依頼: セビーリャ） | MIT | このリポジトリ |
| server/static/themes/default/sprites/fleet/frigate.svg | OpenAI Codex（幾何の形で作成。依頼: セビーリャ） | MIT | このリポジトリ |
| server/static/themes/default/sprites/overlay/trench_back.svg | OpenAI Codex（幾何の形で作成。依頼: セビーリャ） | MIT | このリポジトリ |
| server/static/themes/default/sprites/overlay/trench_front.svg | OpenAI Codex（幾何の形で作成。依頼: セビーリャ） | MIT | このリポジトリ |

## GitHub Actions

| 名前 | ライセンス |
|---|---|
| actions/checkout | MIT |
| actions/setup-python | MIT |
