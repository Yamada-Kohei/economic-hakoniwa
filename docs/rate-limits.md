# 回数制限（ログインと AI 用の入り口）

公開サーバーで、パスワードの総当たりと、AI 用の入り口の使いすぎを抑える仕組みです。決まりは
[設計書 11 の UI-47〜UI-49](design/11-ui.md)、試験は `tests/server/test_rate_limits.py` です。

## しくみ

| 何を | どこで | 数え方 |
|---|---|---|
| ログインの失敗 | `server/accounts/loginguard.py`（ミドルウェア）。`/accounts/login/` と `/admin/login/` の POST | 接続元 × アカウント名の組み合わせごと。5 回失敗したら 15 分、その組み合わせだけ断る |
| ログインの送信数 | 同上 | 接続元ごと。1 分に 30 回まで（アカウント名を変えても、2 つの画面でも合算） |
| AI 用の鍵の処理数 | `server/accounts/keys.py` の `charge`。`/api/` はミドルウェア、`/mcp` は `server/web/mcp.py` の `endpoint` | 鍵ごと。1 分に 60 件まで。`/api/` は 1 回 1 件、`/mcp` は含まれるメッセージの数 |
| `/mcp` のまとめ送信 | 同上 | 1 回に 10 件まで。超えたら 1 件も実行せず断る |

数えは DB の表（`accounts_ratecounter`・`accounts_loginattempt`）で、Gunicorn の複数のワーカーや複数のプロセスが同じ数を
見ます。枠の確認と使い込みは 1 つの UPDATE（足りなければ何も足さない）なので、同時に送られても上限を超えて受けません。
断った送信は数に足さないので、断られ続けても待ち時間は延びません。

- 窓は固定の区切り（1 分）です。区切りの変わり目に、続けて 2 回分まで通ることがあります。どのワーカーが数えても同じ答えに
  なる単純さを優先しました（滑る窓にするには送信ごとの記録が要り、DB の負担が増えます）。
- ログインは、パスワードの照合の前に 1 回分を先に数えます。照合のあとで、成功なら記録を消し、失敗なら上限に達したか見て
  停止します。CSRF の印の誤りなど、照合に進まなかった送信は失敗に数えません。
- 画面の案内は、停止の理由やアカウントの有無によらず同じ文です（429 と `Retry-After` を返します）。
- IP もアカウント名も、サーバーの秘密鍵で混ぜたハッシュで持ちます（アカウント名の欄にパスワードを打つ人がいるため）。
  ログにも、日時・画面・ハッシュの頭の 12 文字・回数・待ちの秒数だけを出し、パスワードや見出しは出しません。

## 設定（環境変数）

| 名前 | 既定 | 意味 |
|---|---|---|
| `HAKONIWA_LOGIN_MAX_FAILURES` | 5 | 失敗できる回数（0 で失敗による停止なし） |
| `HAKONIWA_LOGIN_LOCK_MINUTES` | 15 | 停止の長さ（分） |
| `HAKONIWA_LOGIN_FAILURE_WINDOW_MINUTES` | 15 | 最初の失敗からこの間に回数に達すると停止 |
| `HAKONIWA_LOGIN_POSTS_PER_WINDOW` | 30 | 接続元ごとのログインの送信数（0 で上限なし） |
| `HAKONIWA_LOGIN_POST_WINDOW_SECONDS` | 60 | 上の区切りの秒 |
| `HAKONIWA_AI_CALLS_PER_MINUTE` | 60 | 鍵 1 つが 1 分に使える処理の数（0 で枠なし） |
| `HAKONIWA_MCP_MAX_BATCH` | 10 | `/mcp` に 1 回で送れるメッセージの数（鍵の枠が 0 でも効く。0 以下は 1 として扱う） |
| `HAKONIWA_THROTTLE_KEEP_HOURS` | 24 | 記録を消すまでの時間 |
| `HAKONIWA_CLIENT_IP_HEADER` | `REMOTE_ADDR` | 接続元を読む場所 |
| `HAKONIWA_TRUSTED_PROXIES` | `127.0.0.1,::1` | その見出しを信じてよい中継元（IP / CIDR、カンマ区切り） |

## 接続元の読み方

- 既定は、つないできた相手の IP です。中継（Caddy・Cloudflare Tunnel など）の後ろでは、`HAKONIWA_CLIENT_IP_HEADER` に
  中継が書く見出し（例: `HTTP_CF_CONNECTING_IP`）を指定します。ただし、つないできた相手が `HAKONIWA_TRUSTED_PROXIES`
  にあるときだけ信じます。ない相手が書いた見出しは捨てて、その相手の IP を使います（ログに警告）。
- `HTTP_X_FORWARDED_FOR` のように IP が並ぶ見出しは、右から見て、信頼する中継元でない最初の IP を使います。
- IPv6 は /64 を 1 つの接続元とします（登録の制限と同じ）。IPv4 を写した IPv6 は IPv4 に直します。
- 読めないとき（見出しがない・壊れている）は、ログインの制限の対象外にはせず、1 つの箱にまとめて数えます。
  この箱は 1 分 30 回の上限を全員で分けるので、頻繁に出るときは設定の間違いです（ログに「接続元が読めない」の警告）。

## 配備・切り戻し

1. 配備: 通常どおり `git pull` → `python manage.py migrate`（`accounts` の 0004 が 2 つの表を足すだけ。既存の表は変えない）→
   再起動。自動の反映（`tools/deploy`）はこの順で動く。
2. 設定の確認: 中継の後ろなら `HAKONIWA_CLIENT_IP_HEADER` と `HAKONIWA_TRUSTED_PROXIES` を設定し、ログインを数回失敗して
   管理画面の「ログインの失敗の記録」に組み合わせが 1 つ入ることと、ログに「接続元が読めない」が出ないことを見る。
3. 解除: 管理画面の「ログインの失敗の記録」で行を消すと、その組み合わせの停止が解ける。全部止めるときは
   `HAKONIWA_LOGIN_MAX_FAILURES=0`・`HAKONIWA_LOGIN_POSTS_PER_WINDOW=0` を設定して再起動する。
4. 切り戻し: 前の版に戻しても、足した 2 つの表は残るだけで害はない（前の版は使わない）。表も消すなら
   `python manage.py migrate accounts 0003`。
5. 片づけ: 古い記録は新しい記録を作るときに消える。使われない期間のために `python manage.py purge_throttle` も使える。

## 公開側の案（未適用。環境の設定は変えていない）

公開の構成の案（`/mnt/project-files/beta-env/home-hosting-plan-20261025.md`）は、Cloudflare → cloudflared（Tunnel）→ Caddy
→ gunicorn です。以下は案で、実機では未確認です。

- **接続元**: Cloudflare が付ける `CF-Connecting-IP` を読む。`HAKONIWA_CLIENT_IP_HEADER=HTTP_CF_CONNECTING_IP`、
  `HAKONIWA_TRUSTED_PROXIES=127.0.0.1`（Caddy は同じ機械）。Caddy・gunicorn にポートを開けず、Tunnel からだけ届く形にする
  （外から直接届くと見出しを偽れる）。gunicorn を UNIX ソケットで受けるときは `REMOTE_ADDR` が空になる（gunicorn の場合。実機で未確認）ので、同じ機械の中の
  相手として信じる。
- **ログインの POST の連打**: Cloudflare の WAF のレート制限で、`/accounts/login/` と `/admin/login/` への POST を接続元ごとに
  絞る（例: 10 秒に 10 回を超えたら 10 秒ブロック。無料の枠で選べる期間と回数はダッシュボードで要確認）。Caddy の標準では
  レート制限がないので、アプリ側の上限を最後の守りとする。
- **管理画面**: `/admin/*` を Cloudflare Access で本人だけに絞る案が計画にある。この場合、管理画面のログインはアプリまで
  届く前に Access で断られ、アプリ側の上限は二重の守りになる。利用する管理の経路（VPN か Access か）が決まるまで、
  接続の設定は変えない。
