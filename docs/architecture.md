# 全体の構成

```
ブラウザ（server/static, server/templates）
   ↓ HTML と JSON API
Django（server/）……保存・アカウント・命令の受付・ターン更新の実行
   ↓ 状態を渡して計算させる
コア（hakoniwa_core/）……ルール本体。Django も DB も知らない
   ↓ 読む
定義データ（packs/、schemas/、game.yaml）
```

## コア（hakoniwa_core）

| 場所 | 中身 |
|---|---|
| `config.py` | `game.yaml` の読み込み |
| `defs/` | パックの読み込みと検証。結果は書き換えられない `Defs` |
| `state/` | 世界・島・マス・命令の状態（`WorldState` など）と、JSON との変換 |
| `registry.py` | モジュールが登録する段・命令・式の形 |
| `modules/` | 標準のモジュール（`planning`: 命令の実行、`basics`: 島の名前の変更） |
| `turn/` | 段の進行役（`Engine`）、段に渡すもの（`StageContext`）、乱数 |
| `orders.py` | 命令の検証（受付時と実行時の両方で使う） |
| `log.py` | ターンログの文面づくり |
| `game.py` | 設定・定義データ・モジュールをまとめて組み立てる `load_game()` |
| `simulate.py` | 画面も DB もなしで回す一括シミュレーション |

## 1回のターン更新

1. cron などが `manage.py run_turn` を呼ぶ。次の更新時刻になっていなければ何もしない。
2. 更新中の印を立てる（その後に届いた命令は次のターンに回る）。
3. DB から世界全体を `WorldState` に読み込み、写しをバックアップに取る。
4. `game.yaml` の `stages:` の順に段を実行する。
5. 変わったものだけを書き戻し、ターンログを足し、ターン番号を進める。

3〜5 は1つのトランザクションです。途中で失敗したら何も書かず、ターンも進めず、管理者にメールします。

## Django のアプリ

| アプリ | 中身 |
|---|---|
| `accounts` | 登録・ログイン |
| `world` | 世界・島・マスのモデル、DB とコアの変換（`repository.py`）、定義データの起動時検査 |
| `orders` | 命令のモデル |
| `turns` | ターン更新、バックアップと巻き戻し、ターンログ |
| `web` | 画面と JSON API |

## JSON API（フェーズ0）

| URL | 中身 |
|---|---|
| `GET /api/defs` | 画面が使う定義データと表示名、テーマ |
| `GET /api/islands/<id>/map` | 島のマス（地形と住民の数を y × size + x の順に並べたもの） |
| `GET /api/islands/<id>/tiles/<x>,<y>` | 1マスの詳しい情報 |

命令の読み書き（`/api/islands/<id>/orders`）はフェーズ1で足します。
