#!/usr/bin/env bash
# LAN のサーバーに main を自動で反映する。systemd のタイマー（hakoniwa-autodeploy.timer）から5分おきに呼ぶ。
# タイマーが呼ぶのは $STATE_DIR/autodeploy.sh に置いた写しで、反映が済むたびにこのファイルに合わせ直す。
#
# origin/main が今の版より進んでいたら、次の順に進める。
#   1. 新しい版を別の作業ツリーに出し、両方の世界の DB の写しで migrate・check_defs・数ターン・主なページを試す
#   2. サービスを止めて DB を控え（$BACKUP_DIR）、新しい版にして pip install・migrate・check_defs をし、再起動する
#   3. 主なページが 200 か確かめる
# 2・3 で失敗したら、DB の控えと前の版に戻して再起動する。失敗した版は、main がさらに進むまで試し直さない。
#
# 止めたいときは $STATE_DIR/paused を置く（消せば再開）。結果は $STATE_DIR/last と deploy.log に書く。
# 使い方と置き方は tools/deploy/README.md。
set -uo pipefail

# 作業ツリーの版の更新でこのファイルが書き換わっても困らないよう、写しを動かす。
if [[ -z "${HAKONIWA_DEPLOY_COPY:-}" ]]; then
  copy="$(mktemp)"
  cp "$0" "$copy"
  HAKONIWA_DEPLOY_COPY="$copy" exec bash "$copy" "$@"
fi
trap 'rm -f "$HAKONIWA_DEPLOY_COPY"' EXIT

APP_DIR="${HAKONIWA_APP_DIR:-$HOME/apps/economic-hakoniwa}"
BACKUP_DIR="${HAKONIWA_BACKUP_DIR:-$HOME/apps/hakoniwa-backups}"
STATE_DIR="${HAKONIWA_DEPLOY_STATE:-$HOME/apps/hakoniwa-deploy}"
BRANCH="${HAKONIWA_DEPLOY_BRANCH:-main}"
TRIAL_TURNS="${HAKONIWA_DEPLOY_TRIAL_TURNS:-3}"
KEEP_BACKUPS="${HAKONIWA_DEPLOY_KEEP_BACKUPS:-30}"
PY="$APP_DIR/.venv/bin/python"
PAGES=(/ /help/ /world/ /board/)
# 世界ごとに「サービス名 ポート DB のファイル 控えの名前の頭」。$STATE_DIR/worlds があればそちらを読む。
WORLDS=(
  "hakoniwa 8000 $APP_DIR/db.sqlite3 db.sqlite3"
  "hakoniwa-codex 8001 $HOME/apps/hakoniwa-codex/db.sqlite3 codex-db.sqlite3"
)

mkdir -p "$STATE_DIR" "$BACKUP_DIR"
if [[ -f "$STATE_DIR/worlds" ]]; then
  mapfile -t WORLDS < <(grep -v '^\s*\(#\|$\)' "$STATE_DIR/worlds")
fi
LOG="$STATE_DIR/deploy.log"

log() { printf '%s %s\n' "$(date '+%F %T')" "$*" | tee -a "$LOG"; }

# 説明を付けて命令を回し、出力は deploy.log に残す。失敗したら 1 を返す。
step() {
  local what="$1"
  shift
  if "$@" >>"$LOG" 2>&1; then
    return 0
  fi
  log "失敗: $what"
  return 1
}

finish() {
  # 結果を1行目に、詳しくは deploy.log に。
  printf '%s %s\n前の版 %s → main %s\n' "$(date '+%F %T')" "$1" "${OLD:0:7}" "${NEW:0:7}" >"$STATE_DIR/last"
  log "$1（${OLD:0:7} → ${NEW:0:7}）"
}

services() { local w; for w in "${WORLDS[@]}"; do set -- $w; echo "$1"; done; }

# 動いているサーバーの DB でも崩れない写しを取る（SQLite の backup）。
copy_db() {
  "$PY" -c 'import sqlite3, sys
src = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
dst = sqlite3.connect(sys.argv[2])
src.backup(dst)
dst.close()
src.close()' "$1" "$2"
}

# ポートの主なページが全部 200 になるまで待つ（最大およそ60秒）。
pages_ok() {
  local port="$1" page code i
  for ((i = 0; i < 30; i++)); do
    curl -s -o /dev/null "http://127.0.0.1:$port/" && break
    sleep 2
  done
  for page in "${PAGES[@]}"; do
    code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$port$page")"
    if [[ "$code" != 200 ]]; then
      log "ポート $port の $page が $code"
      return 1
    fi
  done
}

# 1. 新しい版を写しの DB で試す。動いている世界には触らない。
trial() {
  local tmp src py w service port db prefix copy url pid
  tmp="$(mktemp -d)"
  src="$tmp/src"
  step "試しの作業ツリーを作る" git -C "$APP_DIR" worktree add --detach "$src" "$NEW" || { rm -rf "$tmp"; return 1; }
  py="$PY"
  if ! git -C "$APP_DIR" diff --quiet "$OLD" "$NEW" -- pyproject.toml; then
    # 使う部品が変わったときだけ、試し用の venv を作る。
    step "試し用の venv を作る" python3 -m venv "$tmp/venv" &&
      step "試し用の venv に入れる" "$tmp/venv/bin/pip" install -q -e "$src" || { trial_cleanup "$tmp"; return 1; }
    py="$tmp/venv/bin/python"
  fi
  for w in "${WORLDS[@]}"; do
    set -- $w
    service="$1" port="$2" db="$3"
    copy="$tmp/$service.sqlite3"
    url="sqlite:///$copy"
    step "$service の DB を写す" copy_db "$db" "$copy" &&
      (cd "$src" && export DATABASE_URL="$url" &&
        step "$service の写しで migrate" "$py" manage.py migrate --noinput &&
        step "$service の写しで check_defs" "$py" manage.py check_defs &&
        for ((i = 1; i <= TRIAL_TURNS; i++)); do
          step "$service の写しで $i ターン目" "$py" manage.py run_turn --force || exit 1
        done) || { trial_cleanup "$tmp"; return 1; }
    # 写しの世界を別のポートで立て、主なページを見る。
    (cd "$src" && DATABASE_URL="$url" exec "$py" manage.py runserver --noreload "127.0.0.1:$((port + 10000))" >>"$LOG" 2>&1) &
    pid=$!
    pages_ok "$((port + 10000))"
    local ok=$?
    kill "$pid" 2>/dev/null
    wait "$pid" 2>/dev/null
    if ((ok != 0)); then
      log "失敗: $service の写しのページ"
      trial_cleanup "$tmp"
      return 1
    fi
  done
  trial_cleanup "$tmp"
  log "写しの試しが通った（各 $TRIAL_TURNS ターン）"
}

trial_cleanup() {
  git -C "$APP_DIR" worktree remove --force "$1/src" >>"$LOG" 2>&1
  rm -rf "$1"
}

# 2・3. 止めて控え、新しい版にして、再起動する。
STAMP="$(date '+%Y%m%d-%H%M%S')"
deploy() {
  local w service port db prefix
  step "サービスを止める" systemctl --user stop $(services) || return 1
  for w in "${WORLDS[@]}"; do
    set -- $w
    db="$3" prefix="$4"
    step "$1 の DB を控える" cp -p "$db" "$BACKUP_DIR/$prefix.main-${NEW:0:7}-$STAMP" || return 1
  done
  step "新しい版にする" git -C "$APP_DIR" merge --ff-only "$NEW" || return 1
  step "pip install" "$PY" -m pip install -q -e "$APP_DIR" || return 1
  for w in "${WORLDS[@]}"; do
    set -- $w
    (cd "$APP_DIR" && export DATABASE_URL="sqlite:///$3" &&
      step "$1 の migrate" "$PY" manage.py migrate --noinput &&
      step "$1 の check_defs" "$PY" manage.py check_defs) || return 1
  done
  start_and_check
}

start_and_check() {
  local w
  step "サービスを動かす" systemctl --user start $(services) || return 1
  for w in "${WORLDS[@]}"; do
    set -- $w
    pages_ok "$2" || return 1
  done
}

# 失敗したら、DB の控えと前の版に戻す。
restore() {
  local w
  systemctl --user stop $(services) >>"$LOG" 2>&1
  for w in "${WORLDS[@]}"; do
    set -- $w
    if [[ -f "$BACKUP_DIR/$4.main-${NEW:0:7}-$STAMP" ]]; then
      step "$1 の DB を戻す" cp -p "$BACKUP_DIR/$4.main-${NEW:0:7}-$STAMP" "$3"
    fi
  done
  step "前の版に戻す" git -C "$APP_DIR" reset --hard -q "$OLD"
  step "前の版で pip install" "$PY" -m pip install -q -e "$APP_DIR"
  start_and_check
}

# 自動の控えは、世界ごとに新しいものから $KEEP_BACKUPS 個だけ残す。
prune_backups() {
  local w
  for w in "${WORLDS[@]}"; do
    set -- $w
    ls -1t "$BACKUP_DIR/$4".main-* 2>/dev/null | tail -n +"$((KEEP_BACKUPS + 1))" | xargs -r rm -f
  done
}

exec 9>"$STATE_DIR/lock"
flock -n 9 || exit 0
[[ -e "$STATE_DIR/paused" ]] && exit 0

cd "$APP_DIR" || exit 1
if [[ "${1:-}" == "--trial" ]]; then
  # 写しの試しだけを回す（反映しない）。例: autodeploy.sh --trial HEAD
  OLD="$(git rev-parse HEAD)"
  NEW="$(git rev-parse "${2:-HEAD}")"
  trial
  exit
fi
git fetch -q origin "$BRANCH" >>"$LOG" 2>&1 || { log "失敗: git fetch"; exit 1; }
OLD="$(git rev-parse HEAD)"
NEW="$(git rev-parse "origin/$BRANCH")"
[[ "$OLD" == "$NEW" ]] && exit 0
[[ -e "$STATE_DIR/failed-$NEW" ]] && exit 0

log "main ${NEW:0:7} を見つけた（今は ${OLD:0:7}）"
if ! git merge-base --is-ancestor "$OLD" "$NEW"; then
  touch "$STATE_DIR/failed-$NEW"
  finish "止めた: 今の版から main へ早送りできない"
  exit 1
fi
if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  touch "$STATE_DIR/failed-$NEW"
  finish "止めた: サーバーの作業ツリーに手の変更がある"
  exit 1
fi
if ! trial; then
  touch "$STATE_DIR/failed-$NEW"
  finish "止めた: 写しの試しで失敗（動いている世界はそのまま）"
  exit 1
fi
if ! deploy; then
  log "反映に失敗したので戻す"
  touch "$STATE_DIR/failed-$NEW"
  if restore; then
    finish "戻した: 反映に失敗し、DB の控えと前の版で動かし直した"
  else
    finish "要対応: 反映にも戻しにも失敗した。サービスが止まっているかもしれない"
  fi
  exit 1
fi
rm -f "$STATE_DIR"/failed-*
prune_backups
# タイマーが呼ぶ $STATE_DIR/autodeploy.sh を、反映した版のこのファイルに合わせる。
if [[ -f "$APP_DIR/tools/deploy/autodeploy.sh" ]] && ! cmp -s "$APP_DIR/tools/deploy/autodeploy.sh" "$STATE_DIR/autodeploy.sh"; then
  cp "$APP_DIR/tools/deploy/autodeploy.sh" "$STATE_DIR/autodeploy.sh.new" &&
    chmod +x "$STATE_DIR/autodeploy.sh.new" &&
    mv -f "$STATE_DIR/autodeploy.sh.new" "$STATE_DIR/autodeploy.sh" &&
    log "自動の反映のスクリプトを新しい版に合わせた"
fi
finish "反映した"
