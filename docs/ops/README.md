# 公開サーバーの建て方と運用

家に置く専用の機械で、ベータ版のゲームと管理者の HP を動かすための形の説明です。
決めた理由は設計書のタブ 14（台帳 2026-10-25・2026-10-26）にあります。

建て方と運用の手順書（家のネットワークの形・番地・見張りの中身を含む）は、公開のリポジトリには置かず、家の資料の保管庫の「手順/公開サーバーの建て方.md」「手順/公開サーバーの運用の手順.md」を正本にしています（2026-10-28 から。手順の更新も保管庫で行います）。

| 読むもの | 使うとき |
|---|---|
| [../../tools/deploy/prod/](../../tools/deploy/prod/) | 機械に置く台本・サービスの定義・Caddy の設定（このリポジトリが正本） |
| [../rate-limits.md](../rate-limits.md) | ログイン・登録などの回数の上限と、Cloudflare の側の案 |

## 全体の形

```
遊ぶ人 ─HTTPS─ Cloudflare ══トンネル（家から外へつなぐだけ）══ cloudflared
                                                                 │ http://127.0.0.1:8080
                                                              Caddy ─ /static/ はファイルを直接
                                                                 │ http://127.0.0.1:8000
                                                             gunicorn ─ PostgreSQL（127.0.0.1:5432）
sevilla-lab.com・www は Caddy が /srv/hp/public の静的なファイルを返す
```

- 受け口はどれも 127.0.0.1 だけ。ルーターの外向きの受け口は開けない。
- 動かす人（OS のユーザー）: `hakoniwa`（ゲーム・ターン・反映）、`hp`（HP の取り込み）。root の台本は `/usr/local/lib/hakoniwa/`（ゲームのユーザーが書き換えられない）。
- 置き場: ゲーム `/srv/hakoniwa/`、HP `/srv/hp/`、毎晩の写し `/srv/backup/`（HDD、root だけ）、秘密 `/etc/hakoniwa/`。
- 知らせ: 本人のスマホの ntfy（版が入った・失敗・見張り）。ターンの遅れ・写しが古い・失敗したサービスは機械の中の点検（30 分ごと）が、機械そのものが応答しないときは機械の外の見張りが、ntfy で知らせる。
- Claude は Cloudflare Access を通らず、家の中から ssh で機械に入って管理する。

## 自動で動くもの

| 名前 | いつ | 何をする | 誰で動く |
|---|---|---|---|
| `hakoniwa-turn.timer` | 1 分ごと | 時刻になっていれば 1 ターン進める | hakoniwa |
| `hakoniwa-deploy.timer` | 5 分ごと | `release` の枝が進んでいれば、本番の写しで試してから反映 | hakoniwa |
| `hakoniwa-backup.timer` | 毎晩 03:20 | DB 丸ごとを HDD に 30 日分、指紋を ntfy へ | root |
| `hakoniwa-health.timer` | 30 分ごと | ターンが遅れていないか・写しが古くないか・失敗したサービスがないかを見て、おかしければ ntfy へ | root |
| `hakoniwa-weekly.timer` | 毎週日曜 08:00 | 週のまとめを ntfy へ | root |
| `hakoniwa-restore-check.timer` | 毎月 1 日 05:10 | 最新の写しを試し用の DB に戻し、1 ターン回せるか確かめる | root |
| `hakoniwa-watch.timer` | 毎日 06:40 | 前の日と比べて、違えば ntfy へ | root |
| cron `hakoniwa-soft-update` | 毎日 04:10（日〜金）／土曜 05:10 | 安全の更新／apt 全部。何が入ったかを ntfy へ。要る再起動はターンの鍵が空いてから | root |
| `hp-fetch.timer` | 5 分ごと | HP の `release` の枝を取り、確かめて向き先を切り替える | hp |
