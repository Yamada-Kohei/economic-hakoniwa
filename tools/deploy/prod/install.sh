#!/usr/bin/env bash
# 台本・サービスの定義・Caddy の設定を、機械の決まった場所に置く（root で、リポジトリの中から）。
#   sudo tools/deploy/prod/install.sh [scripts|units|caddy|all]
# 置き場所の持ち主は root にする（ゲームのユーザーが書き換えられると、root の台本を乗っ取られるため）。
# 秘密のファイル（/etc/hakoniwa/*.env）と、ユーザー・DB・HDD の用意はここではしない（docs/ops/build.md）。
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
what="${1:-all}"
[[ "$(id -u)" == 0 ]] || { echo "root で動かしてください" >&2; exit 1; }

scripts() {
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
units() {
  install -o root -g root -m 644 "$here"/systemd/* /etc/systemd/system/
  systemctl daemon-reload
}
caddy() {
  install -o root -g root -m 644 "$here/caddy/Caddyfile" /etc/caddy/Caddyfile
  caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
  systemctl reload caddy
}

case "$what" in
scripts) scripts ;;
units) units ;;
caddy) caddy ;;
all) scripts; units; caddy ;;
*) echo "使い方: $0 [scripts|units|caddy|all]" >&2; exit 2 ;;
esac
echo "置きました（$what）。タイマーを有効にするのは docs/ops/build.md の手順で"
