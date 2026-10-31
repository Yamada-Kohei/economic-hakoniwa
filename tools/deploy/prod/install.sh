#!/usr/bin/env bash
# 台本・サービスの定義・Caddy の設定を、機械の決まった場所に置く（root で、リポジトリの中から）。
#   sudo tools/deploy/prod/install.sh [scripts|units|caddy|all]
# 置き場所の持ち主は root にする（ゲームのユーザーが書き換えられると、root の台本を乗っ取られるため）。
# 秘密のファイル（/etc/hakoniwa/*.env）と、ユーザー・DB・HDD の用意はここではしない（家の資料の「公開サーバーの建て方」）。
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
what="${1:-all}"
[[ "$(id -u)" == 0 ]] || { echo "root で動かしてください" >&2; exit 1; }

do_scripts() {
  install -d -o root -g root -m 755 /usr/local/lib/hakoniwa
  install -o root -g root -m 755 "$here"/bin/* /usr/local/lib/hakoniwa/
  install -o root -g root -m 440 "$here/etc/sudoers-hakoniwa" /etc/sudoers.d/hakoniwa-deploy
  visudo -cf /etc/sudoers.d/hakoniwa-deploy >/dev/null
  install -o root -g root -m 644 "$here/etc/tmpfiles-hakoniwa.conf" /etc/tmpfiles.d/hakoniwa.conf
  systemd-tmpfiles --create /etc/tmpfiles.d/hakoniwa.conf
  install -o root -g root -m 644 "$here/etc/cron-hakoniwa-soft-update" /etc/cron.d/hakoniwa-soft-update
  install -d -o root -g root -m 755 /srv/hakoniwa/maintenance
  install -o root -g root -m 644 "$here/maintenance/maintenance.html" /srv/hakoniwa/maintenance/maintenance.html
}
do_units() {
  install -o root -g root -m 644 "$here"/systemd/* /etc/systemd/system/
  # Caddy はパッケージのサービス定義のまま（落ちても起き直さない）なので、起き直す設定だけを足す
  install -D -o root -g root -m 644 "$here/etc/caddy-restart.conf" /etc/systemd/system/caddy.service.d/hakoniwa-restart.conf
  systemctl daemon-reload
}
do_caddy() {
  install -o root -g root -m 644 "$here/caddy/Caddyfile" /etc/caddy/Caddyfile
  caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
  # 管理の窓口（admin）を止めているので reload は使えない。再起動する（数秒、ゲームの画面が一瞬つながらなくなる）
  systemctl enable caddy
  systemctl restart caddy
}

case "$what" in
scripts) do_scripts ;;
units) do_units ;;
caddy) do_caddy ;;
all) do_scripts; do_units; do_caddy ;;
*) echo "使い方: $0 [scripts|units|caddy|all]" >&2; exit 2 ;;
esac
echo "置きました（$what）。タイマーを有効にするのは家の資料の「公開サーバーの建て方」の手順で"
