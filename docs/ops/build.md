# 建て方（sevilla-server にゲームを入れる）

> 状態: 1〜9 と 11 は 2026-10-27 に実機で通した（9 の HP の取り込みは初回の版に切り替わるまで、11 のトンネルは Access の入口の確認まで）。10（電気が戻ったら自動で起動）は後で通す。

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

## 11. トンネルと Access（8-5）

外からの受け口はルーターに作らない。機械から Cloudflare へつなぐだけ（外向き 7844 番。ルーターの許可リストに `region1.v2.argotunnel.com`・`region2.v2.argotunnel.com` がある）。

**本人の手が要るもの（1 回だけ、画面の名前どおり）**
1. Cloudflare の「Zero Trust」を始める: チーム名（`sevilla-lab`）、プランは Free、支払いカードを登録（無料枠でも要る。請求は出ない）。
2. Zero Trust → Integrations → Identity providers → Add new identity provider → One-time PIN を保存。
3. Zero Trust → Access controls → Applications → Create new application → Self-hosted and private。公開ホスト名は `hakoniwa.sevilla-lab.com`・`sevilla-lab.com`・`www.sevilla-lab.com`（Domain は `sevilla-lab.com`、Subdomain は `hakoniwa`・空・`www`）。ポリシーは名前 `本人だけ`、Action は Allow、Include は **Emails（本人のメール）だけ**。Login Method を Include に足してはいけない（Include は「どれかに当てはまれば通す」なので、他人のメールでも One-time PIN で入れてしまう）。ログイン方法は One-time PIN だけ、Session Duration は 24 hours。
4. 機械が出す「許可」の URL を、待たせずに 1 回開いて `sevilla-lab.com` を選び、承認を押す（長く待たせると機械が受け取れず、やり直しになる）。

**機械の側（Claude）**

```sh
# 部品（Cloudflare の公式の apt の置き場。ルーターの許可リストに pkg.cloudflare.com がある）
sudo mkdir -p --mode=0755 /usr/share/keyrings
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main' | sudo tee /etc/apt/sources.list.d/cloudflared.list
sudo apt-get update && sudo apt-get install -y cloudflared
sudo useradd --system --home-dir /var/lib/cloudflared --create-home --shell /usr/sbin/nologin cloudflared
sudo install -d -o root -g cloudflared -m 750 /etc/cloudflared

# ログインの URL を出す（ここだけ、ルーターの許可リストに api.cloudflare.com と login.cloudflareaccess.org を一時的に足す）
sudo -u cloudflared env HOME=/var/lib/cloudflared sh -c 'setsid nohup cloudflared tunnel login > /var/lib/cloudflared/login.log 2>&1 < /dev/null &'
sudo cat /var/lib/cloudflared/login.log        # URL を本人に渡す。許可が済むと .cloudflared/cert.pem ができる
sudo -u cloudflared env HOME=/var/lib/cloudflared cloudflared tunnel create hakoniwa-sevilla   # UUID と鍵の JSON ができる
# 鍵の JSON を /etc/cloudflared/<UUID>.json に root:cloudflared 0640 で移し、etc/cloudflared-config.yml.example を /etc/cloudflared/config.yml に写して UUID を入れる
sudo cloudflared --config /etc/cloudflared/config.yml tunnel ingress validate
sudo /srv/hakoniwa/app/tools/deploy/prod/install.sh units && sudo systemctl enable --now cloudflared
# Access のアプリを作ってから、名前をトンネルへつなぐ（先につなぐと Access なしで届く瞬間ができる）
for h in hakoniwa.sevilla-lab.com sevilla-lab.com www.sevilla-lab.com; do
  sudo -u cloudflared env HOME=/var/lib/cloudflared cloudflared tunnel route dns hakoniwa-sevilla $h
done
# 後始末: 証明書と元の JSON を消し、ルーターの一時の名前を外す（cert.pem は DNS を書き換えられる強い権限）
sudo shred -u /var/lib/cloudflared/.cloudflared/cert.pem /var/lib/cloudflared/.cloudflared/<UUID>.json
```

確かめ（家の中の PC から外向きに）: 3 つの名前とも `curl -I https://<名前>/` が `302` で `sevilla-lab.cloudflareaccess.com/cdn-cgi/access/login/...` へ向かう。
スマホ（携帯回線）で、メールの番号を入れると入れる。DNS の行は CNAME の 3 つだけで、家の IP はどこにも書かれない。
止めるボタンは [runbook.md](runbook.md) の 5。

## 12. 要らない外向きの定期通信を止める

Ubuntu は、ファームウェア情報（fwupd）・お知らせ（motd-news、update-notifier、ua-timer、apt-news）を定期に外へ取りにいく。この機械には要らず、ルーターの許可の外なので、止めないと「許していない先へ出ようとしました」の知らせが鳴る（10-27 に fwupd で実際に鳴った）。許可の名前は足さず、止める。

```sh
sudo systemctl mask --now fwupd-refresh.timer fwupd-refresh.service motd-news.timer \
  update-notifier-download.timer update-notifier-motd.timer ua-timer.timer
sudo pro config set apt_news=false
```

戻すときは `systemctl unmask`。確かめは `systemctl list-timers --all` に上の名前が出ないこと。

## 入れ直し（OS から作り直すとき）

1 から 12 を同じ順に行い、7 の代わりに `hakoniwa-restore-check --file <選んだ写し>` と同じ要領で本番の DB に戻す
（[runbook.md](runbook.md) の「入られた疑いのとき」）。
