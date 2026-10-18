# 開発に参加する

改造や修正を歓迎します。小さな変更でも PR を出してください。

## 手元で動かす

README の「動かし方」のとおりです。変更したら次の2つが通ることを確かめてください。

```sh
ruff check . && ruff format --check .
pytest
```

PostgreSQL でも確かめるときは `DATABASE_URL=postgres://... pytest`。CI は SQLite と PostgreSQL の両方で回ります。
変えたファイルが `docs/` の下だけの PR では、CI は lint とゲームの試験を飛ばし、文書の確かめ（`tools/spec/design_index.py check` と docs/ を読む試験）だけを回します。

## コードの決まり

- **コアの中で Django を import しない。** `hakoniwa_core/` は Django もデータベースも知りません。
  自動テスト（`tests/core/test_boundaries.py`）がこの依存を検査します。
- **コード中に数値を直接書かない。** ゲームの数値は定義データ（`packs/`）かサーバー設定（`game.yaml`）に置きます。
- **関数には型の注釈（type hints）を付ける。** 読む人の助けにするためで、型検査の道具は必須にしません。
- **名前は英語、説明は日本語。** コードの名前とコミットメッセージは英語、画面の文字列・説明書・コメントは日本語で書きます。
- **定義データは文字列の名前で参照する。** 施設の種類などを DB の外部キーにしません。
- **量は整数で持つ。** 財の量は 1000 分の 1 単位、お金は通貨の最小単位の整数です。丸めは `hakoniwa_core/units.py` だけで行います。
- **DB 固有の書き方をしない。** Django の ORM（どうしても要るときは標準の SQL だけ）を使い、SQLite でも PostgreSQL でも動くようにします。
- **島の処理順に結果が左右されないようにする。** 島ごとの段は自分の島だけを書き換え、乱数は `ctx.rng(island.id)` から取ります。
  詳しくは [docs/modules.md](docs/modules.md)。
- **AI の入り口も人と同じにそろえる。** 命令・画面・画面に出す値・地図の重ね表示を足したり変えたりしたら、同じ PR で
  JSON API と MCP の道具（`server/web/mcp.py`）と国の要約・差分（`server/web/overview.py`）も合わせます。
  AI が人と同じことをできるようにするためで、合わせ漏れは `tests/server/test_ai_parity.py` が見つけます
  （[docs/architecture.md](docs/architecture.md) の「AI 用の入り口」）。

## 外部のものを足すとき

外部のライブラリや素材（絵・音・フォント）を足すときは、同じ変更で [THIRD_PARTY.md](THIRD_PARTY.md) に
出典とライセンスを書いてください。MIT と両立しないもの、出典やライセンスが分からないものは使えません。
他のゲームのコードや素材を写すこともしません。

## マップの絵

島の俯瞰図の絵の決まり（名前・大きさ・基準点・形式・確かめ方）は [docs/map-art.md](docs/map-art.md) にあります。
絵がないものは色の箱で描くので、1枚ずつ足せます。AI エージェント向けの説明は [AGENTS.md](AGENTS.md) です。

## PR

- 1つの PR では1つのことをします。
- 定義データの形（`schemas/`）を変えるときは、`docs/defs.md` も一緒に直します。
- 段を足す・並べ替えるときは、`docs/modules.md` の段の表も一緒に直します（`tests/core/test_docs_match.py` が
  `game.yaml` の `stages:` と比べます）。
- 設定や定義データの数字を変えたら、`python tools/spec/settings.py diff origin/main` の出力を PR の本文に
  貼ります。今の数字の一覧は `python tools/spec/settings.py markdown` で読み物の形にできます。
- DB のモデルを変えたら `python manage.py makemigrations` で移行ファイルを作って含めます。
