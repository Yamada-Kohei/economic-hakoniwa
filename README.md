# 経済版箱庭諸国

箱庭諸国の流れをくむ、ブラウザで遊ぶ島の育成ゲームです。農業と各種の工業によるサプライチェーン、
市場と通貨、価格の決まり方、市場経済から計画経済までの経済体制、陸海の軍事を遊べるようにすることを
目指しています。既存の箱庭諸国のコードは使わず、新しく書いています。

いまは **フェーズ0（基盤）** です。空の島が並ぶ世界をターン更新で進め、島を俯瞰図で表示できます。
ゲームとして遊べるようになるのはフェーズ1（建国・施設・生産の連鎖）からです。

## 動かし方

Python 3.12 以上が要ります。

```sh
pip install -e ".[dev]"          # 1. 必要なものを入れる
python manage.py setup_dev       # 2. DB とサンプルの世界（島3つ）を作る
python manage.py runserver       # 3. http://127.0.0.1:8000/ を開く
```

管理画面（http://127.0.0.1:8000/admin/）を使うには `python manage.py createsuperuser` で管理者を作ります。
管理画面では島を足したり、ターンを進めたり、バックアップから巻き戻したりできます。

### よく使うコマンド

| コマンド | すること |
|---|---|
| `python manage.py run_turn` | 次の更新時刻になっていれば1ターン進める（cron などから数分おきに呼ぶ） |
| `python manage.py run_turn --force` | 時刻に関係なく今すぐ進める |
| `python manage.py rollback_turn 12` | ターン 12 の更新前の状態に戻す |
| `python manage.py check_defs` | 定義データ（パック）を検査する |
| `python manage.py create_sample_world --islands 50` | 試験用の島を足す |
| `python -m hakoniwa_core.simulate --islands 50 --turns 300` | 画面も DB もなしでターンを回す |
| `pytest` | 自動テスト |
| `ruff check . && ruff format --check .` | 書式と誤りの検査 |

### データベース

既定では SQLite（`db.sqlite3`）を使います。公開サーバーには PostgreSQL を勧めます
（ターン更新中も画面からの読み書きが止まりにくいため）。

```sh
pip install -e ".[postgres]"
export DATABASE_URL=postgres://user:password@localhost:5432/hakoniwa
python manage.py migrate
```

公開サーバーでは `DJANGO_SECRET_KEY`、`DJANGO_DEBUG=0`、`DJANGO_ALLOWED_HOSTS` も設定してください
（`server/settings.py` の先頭に一覧があります）。

## 構成

| 場所 | 中身 |
|---|---|
| `hakoniwa_core/` | ルール本体（コア）。Django も DB も使わない純粋な Python |
| `packs/base/` | 本体パック。財・地形・施設・命令などの定義データと表示名 |
| `schemas/` | 定義データの形（JSON Schema） |
| `server/` | Django のサーバー。保存・アカウント・画面・JSON API・ターン更新 |
| `server/static/js/map/` | 島の俯瞰図（Canvas、ビルド不要の ES モジュール） |
| `game.yaml` | サーバー設定（使うパック、段の並び、ゲームの数値） |
| `docs/` | 改造のための説明書 |
| `tests/` | 自動テスト（`core/` と `server/`） |

依存は「ブラウザ → Django → コア → 定義データ」の一方向です。詳しくは [docs/architecture.md](docs/architecture.md)。

## 改造する

- 数値や施設を足す・変える: [docs/defs.md](docs/defs.md)（YAML を書くだけ）
- 追加パックを作る: [docs/packs.md](docs/packs.md)
- 計算の式を差し替える・新しい仕組みを足す: [docs/modules.md](docs/modules.md)
- 決まりごとと PR の出し方: [CONTRIBUTING.md](CONTRIBUTING.md)

## ライセンス

[MIT](LICENSE)。使っている外部のものは [THIRD_PARTY.md](THIRD_PARTY.md) にまとめています。
