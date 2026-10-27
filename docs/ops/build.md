# 建て方（sevilla-server にゲームを入れる）

> 状態: 1〜8（ゲームと DB と受付）は 2026-10-27 に実機で通した。9（HP の取り込み）と 10 は 8-3 で通す。

前提: Ubuntu 24.04、ssh は鍵だけ、ufw は家の LAN からの 22 番だけ、自動更新あり（`docs/design/` タブ 14 の 2026-10-25）。
作業は X870 から `ssh sevilla@sevilla-server`（ルーターで予約した 192.168.50.10）で行う。外へは 80・443・7844 番だけ出られる
（段 8-4 で行き先を絞る。絞った後は、新しく外へ出る先が要るたびにルーターに足す）。

## 1. 部品を入れる

```sh
sudo apt-get update
sudo apt-get install -y postgresql python3-venv python3-dev git curl libpq-dev debian-keyring debian-archive-keyring apt-transport-https
# Caddy は公式の apt の置き場から（Ubuntu のものは古い）。手順は https://caddyserver.com/docs/install#debian-ubuntu-raspbian
```

## 2. 使う人・フォルダ・HDD

```sh
sudo useradd --system --create-home --home-dir /srv/hakoniwa --shell /usr/sbin/nologin hakoniwa
sudo useradd --system --create-home --home-dir /srv/hp --shell /usr/sbin/nologin hp
sudo groupadd --system hakoniwa-notify && sudo usermod -aG hakoniwa-notify hakoniwa && sudo usermod -aG hakoniwa-notify hp
sudo install -d -o hakoniwa -g hakoniwa -m 750 /srv/hakoniwa/state /srv/hakoniwa/var
sudo chmod 755 /srv/hakoniwa /srv/hp
sudo install -d -o root -g hakoniwa -m 750 /etc/hakoniwa
```

HDD（500GB、ext4）は `/srv/backup` に据える。機械を入れた時点で `/bk` に据えてあった（中身は空）ので、`/etc/fstab` の行を
`/srv/backup` に直し、起動を止めないよう `nofail` を付けた（2026-10-27、直す前の控え `/etc/fstab.bak-20261027`）。新しい円盤のときは
`lsblk -f` で円盤の名前を確かめてから、`mkfs.ext4`（中身が消える）と同じ形の行を足す。

```sh
sudo sed -i 's#^\(/dev/disk/by-uuid/<HDD の UUID>\) /bk ext4 defaults 0 1#\1 /srv/backup ext4 defaults,nofail,x-systemd.device-timeout=10 0 2#' /etc/fstab
sudo umount /bk; sudo install -d -m 700 /srv/backup; sudo systemctl daemon-reload; sudo mount /srv/backup; sudo chmod 700 /srv/backup
```

## 3. PostgreSQL（機械の中だけで受ける）

```sh
# listen_addresses は既定で localhost だけ。確かめる
sudo -u postgres psql -c 'SHOW listen_addresses;'
PW="$(openssl rand -hex 24)"      # この値を DATABASE_URL に入れる。チャットに貼らない
sudo -u postgres psql -v ON_ERROR_STOP=1 <<SQL
CREATE ROLE hakoniwa LOGIN PASSWORD '$PW' NOSUPERUSER NOCREATEDB NOCREATEROLE;
CREATE DATABASE hakoniwa OWNER hakoniwa ENCODING 'UTF8';
CREATE DATABASE hakoniwa_trial OWNER hakoniwa ENCODING 'UTF8';   -- 反映の試しと戻し試験で使う
SQL
```

## 4. ゲームを置く

```sh
sudo -u hakoniwa git clone --branch release https://github.com/Yamada-Kohei/economic-hakoniwa.git /srv/hakoniwa/app
sudo -u hakoniwa python3 -m venv /srv/hakoniwa/venv
sudo -u hakoniwa /srv/hakoniwa/venv/bin/pip install -e "/srv/hakoniwa/app[postgres]" -r /srv/hakoniwa/app/tools/deploy/prod/requirements-prod.txt
```

## 5. 秘密と設定

`tools/deploy/prod/etc/*.example` を `/etc/hakoniwa/hakoniwa.env`（`root:hakoniwa` 0640）と `notify.env`（`root:hakoniwa-notify` 0640）に写し、
`<…>` を機械の中で作った値で埋める。Django の鍵は `openssl rand -base64 64 | tr -d '\n='`。
公開用の値（https の cookie など）はゲーム本体の守りの担当が決めた分を足す。

## 6. 台本とサービスを置く

```sh
sudo /srv/hakoniwa/app/tools/deploy/prod/install.sh all
```

台本は root の持ち物としてコピーする（ゲームのユーザーが書き換えられないように）。台本を変えた版を出したときは、反映の後にこの 1 行をもう一度。

## 7. 初めの DB と世界

```sh
sudo -u hakoniwa bash -c 'set -a; . /etc/hakoniwa/hakoniwa.env; set +a; cd /srv/hakoniwa/app &&
  /srv/hakoniwa/venv/bin/python manage.py migrate && /srv/hakoniwa/venv/bin/python manage.py check_defs &&
  /srv/hakoniwa/venv/bin/python manage.py collectstatic --noinput &&
  /srv/hakoniwa/venv/bin/python manage.py check --deploy'
```

管理者と世界を作る手順は、通し稽古（段 8-6）で確かめた形をここに書く。

## 8. 動かす

```sh
sudo systemctl enable --now hakoniwa-gunicorn.service
sudo systemctl enable --now hakoniwa-turn.timer hakoniwa-deploy.timer hakoniwa-backup.timer hakoniwa-restore-check.timer hakoniwa-watch.timer hakoniwa-weekly.timer hakoniwa-health.timer
# 画面は X870 から ssh のトンネルで確かめる（外には開けない）
ssh -L 8080:127.0.0.1:8080 sevilla@sevilla-server      # → http://localhost:8080（Host が違うので curl -H 'Host: hakoniwa.sevilla-lab.com'）
```

## 9. HP の取り込み（8-3）

```sh
sudo -u hp ssh-keygen -t ed25519 -N '' -C hp-deploy -f /srv/hp/.ssh/deploy_key   # 公開の側を本人が Deploy keys（読むだけ）に登録
sudo -u hp bash -c 'ssh-keyscan -p 443 ssh.github.com > /srv/hp/.ssh/known_hosts'   # 指紋は GitHub の公式の値と照らす
sudo systemctl enable --now hp-fetch.timer
```

## 10. 電気が戻ったら自動で起動する

起動の画面（BIOS）の AC Recovery を「Power On」にする。OS から入れられるかは 8-3 で調べる。

## 入れ直し（OS から作り直すとき）

1 から 9 を同じ順に行い、7 の代わりに `hakoniwa-restore-check --file <選んだ写し>` と同じ要領で本番の DB に戻す
（[runbook.md](runbook.md) の「入られた疑いのとき」）。
