# AGENTS.md

このリポジトリで作業する AI エージェント（Codex など）向けの説明です。人が読んでもかまいません。

## このプロジェクト

「経済版箱庭諸国」は、ブラウザで遊ぶ島の育成ゲームです（Python 3.12 以上・Django 5.2、画面は素の JavaScript と Canvas）。
全体は [README.md](README.md)、コードの決まりは [CONTRIBUTING.md](CONTRIBUTING.md)、構成は [docs/architecture.md](docs/architecture.md) にあります。

- 説明・コメント・PR の文章は **日本語** で書きます。コードの名前は英語です。
- MIT ライセンスで公開しています。外部のライブラリや素材は MIT と両立するものだけを使い、
  足したら同じ変更で [THIRD_PARTY.md](THIRD_PARTY.md) に出典とライセンスを書きます。

## 動かし方と確かめ方

```sh
pip install -e ".[dev]"
python manage.py setup_dev        # DB とサンプルの世界
python manage.py runserver        # http://127.0.0.1:8000/
ruff check . && ruff format --check .
pytest
```

PR を出す前に、ruff と pytest が通ることを確かめます。

## 分担

ゲームの中身（経済のシミュレーション）は、別に Claude が並行して開発しています。
衝突しないよう、頼まれた作業に要るもの以外は変えないでください。特に次は、頼まれない限り触りません。

- `hakoniwa_core/`（シミュレーションのコア）
- `packs/`（定義データ）と `schemas/`
- `server/` の Python のコード（モデル・移行ファイル・ターンの処理など）
- `game.yaml`

## マップの絵を頼まれたとき

島の俯瞰図の絵（スプライト）は、[docs/map-art.md](docs/map-art.md) の仕様どおりに作ります。名前・大きさ・基準点・
形式・ライセンス・確かめ方・見直しの観点は、すべてそこにあります。

- 変えてよいのは次だけです。
  - `server/static/themes/` の下（絵のファイルと `theme.yaml` の `sprites`）
  - `THIRD_PARTY.md`（絵の出典とライセンス）
  - `tools/sprites/`（絵を生成するスクリプトを書いたとき。標準ライブラリだけで動かす）
  - 仕様どおりに描けない不具合があったときだけ `server/static/js/map/` の描画のコード。そのときは PR に理由を書く
- 仕様を変えたほうがよいと思ったら、勝手に変えずに PR の「気になっている点」に書きます。
- 1 つの PR では 1 つのまとまり（例: 地形 5 つ、住宅 4 段階、工場）にします。見直しやすくするためです。
- 見本ページ `http://127.0.0.1:8000/static/map-preview.html` で、絵あり・`?sprites=0`・`?scale=2`・
  `?overlay=population` を見て確かめます。画面を撮れるなら、PR に画像を付けます。
- `pytest tests/server/test_theme_sprites.py` は、名前・ファイル・大きさ・危ない SVG・THIRD_PARTY.md の書き忘れを調べます。

### PR の書き方

- タイトルは「[マップの絵] 〜」で始めます（例「[マップの絵] 地形の絵を足す」）。
- 本文には次の見出しを付けます。

```markdown
## 足した絵
（名前の一覧。docs/map-art.md の表の名前で）

## 確かめたこと
（見本ページと実際の島で見たこと、通したテスト）

## 出典とライセンス
（自分で描いた・生成した、外部の素材ならその URL とライセンス）

## 気になっている点
（仕様で迷ったこと、直したほうがよさそうなこと。なければ「なし」）
```

### 見直しのあと

PR は Claude が [docs/map-art.md](docs/map-art.md) の「見直しの観点」で見直し、PR にコメントします。
直すように書かれたコメントには、同じ PR に変更を足して応えます（新しい PR は作りません）。
直さないと判断したものは、そのコメントに理由を返します。
