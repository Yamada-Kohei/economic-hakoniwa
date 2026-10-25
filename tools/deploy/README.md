# 自動の反映（LAN のサーバー）

main にマージされた版を、LAN のサーバーの2つの世界（ポート 8000 と 8001）へ自動で反映する仕組みです。
サーバーの systemd のユーザータイマーが5分おきに `autodeploy.sh` を呼びます。

## すること

1. `git fetch` して、origin/main が今の版より進んでいなければ何もしない。
2. 新しい版を別の作業ツリーに出し、両方の世界の DB の写しで migrate・check_defs・3ターン・主なページ（`/` `/help/` `/world/` `/board/`）を試す。
   動いている世界には触らない。`pyproject.toml` が変わったときだけ、試し用の venv を作る。
   世界の行がまだない（建国前の空の）DB は、ターンを進められないので、ターンの試しだけ飛ばす（migrate・check_defs・ページは見る。W-189）。
3. 両方のサービスを止め、DB を `~/apps/hakoniwa-backups/` に控える（`db.sqlite3.main-<コミット>-<日時>`、`codex-db.sqlite3.main-…`）。
4. 新しい版にして `pip install -e .`・両方の migrate・check_defs をし、再起動して主なページが 200 か確かめる。
5. 3・4 で失敗したら、DB の控えと前の版に戻して動かし直す。

失敗した版は `~/apps/hakoniwa-deploy/failed-<コミット>` に印を付け、main がさらに進むまで試し直しません。
自動の控えは世界ごとに新しい 30 個だけ残します。

## 見る所と止め方（サーバーの `~/apps/hakoniwa-deploy/`）

- `last`: 最後の結果（反映した・止めた・戻した・要対応）と版。
- `deploy.log`: 詳しい記録。
- `paused` を置くと止まり、消すと再開します。
- `autodeploy.sh --trial <コミット>`: 写しの試しだけを回します（反映しない）。
- `worlds`: 反映する世界の一覧（1 行に「サービス ポート DB 控えの名前の頭」）。あればスクリプトの中の 2 つの世界の代わりに読みます。2026-10-25 から試し遊び2回目の 8002（hakoniwa-play2）と 8003（hakoniwa-play3）も入れて 4 つです。

## 置き方

```sh
mkdir -p ~/apps/hakoniwa-deploy ~/.config/systemd/user
cp tools/deploy/autodeploy.sh ~/apps/hakoniwa-deploy/
cp tools/deploy/hakoniwa-autodeploy.service tools/deploy/hakoniwa-autodeploy.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now hakoniwa-autodeploy.timer
```

タイマーが呼ぶ `~/apps/hakoniwa-deploy/autodeploy.sh` は、反映が済むたびに新しい版のこのフォルダのものに合わせ直します。
タイマーとサービスのファイルを変えたときは、上の `cp` と `daemon-reload` をやり直します。
