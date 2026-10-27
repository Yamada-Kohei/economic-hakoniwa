# 公開サーバーの台本が読む共通の部品。/usr/local/lib/hakoniwa/lib.sh に置く（root の持ち物）。
# shellcheck shell=bash

# shellcheck disable=SC2034 # 読む側の台本が使う
HAKONIWA_ROOT="${HAKONIWA_ROOT:-/srv/hakoniwa}"
APP_DIR="$HAKONIWA_ROOT/app"
VENV="$HAKONIWA_ROOT/venv"
# shellcheck disable=SC2034
STATE_DIR="$HAKONIWA_ROOT/state"
# shellcheck disable=SC2034
LOCK_FILE="${HAKONIWA_LOCK:-/run/hakoniwa/turn.lock}"

# 秘密や宛先の読み込み。無くても動く（その場合は知らせを送れない）。
load_env() {
  local f
  for f in /etc/hakoniwa/hakoniwa.env /etc/hakoniwa/notify.env; do
    if [[ -r "$f" ]]; then
      set -a
      # shellcheck disable=SC1090
      source "$f"
      set +a
    fi
  done
}

log() { printf '%s %s\n' "$(date '+%F %T')" "$*"; }

# 本人のスマホへ知らせる。notify タイトル 本文 [優先度 1-5]。送れなくても台本は止めない。
notify() {
  local title="$1" body="${2:-}" prio="${3:-3}"
  [[ -n "${NTFY_URL:-}" ]] || { log "知らせ（宛先なし）: $title $body"; return 0; }
  # 見出しの日本語は RFC 2047 の形で送る（ntfy が読める）
  curl -fsS -m 20 -H "Title: =?UTF-8?B?$(printf '%s' "$title" | base64 -w0)?=" \
    -H "Priority: $prio" -d "$body" "$NTFY_URL" >/dev/null 2>&1 ||
    log "知らせを送れませんでした: $title"
}

# Django の管理命令を、ゲームの環境で回す。
manage() { (cd "$APP_DIR" && "$VENV/bin/python" manage.py "$@"); }
