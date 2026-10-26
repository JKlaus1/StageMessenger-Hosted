#!/usr/bin/env bash
# Stage Messenger (hosted) — install / update on the home server. Idempotent.
#
# From the phone (one line):
#   ssh joseph@192.168.1.121 'bash ~/src/StageMessenger-Hosted/deploy/setup.sh'
# First run, before the clone exists:
#   ssh joseph@192.168.1.121 'git clone https://github.com/JKlaus1/StageMessenger-Hosted.git ~/src/StageMessenger-Hosted; bash ~/src/StageMessenger-Hosted/deploy/setup.sh'
#
# Touches ONLY: ~/src/StageMessenger-Hosted, ~/services/stage-messenger/, /etc/stage-messenger/env,
# and the stage-messenger container. Never touches Caddy, cloudflared or anything Ledger.
# Never prints secrets.

main() {
  set -u
  local REPO_URL=https://github.com/JKlaus1/StageMessenger-Hosted.git
  local SRC="$HOME/src/StageMessenger-Hosted"
  local SVC="$HOME/services/stage-messenger"
  local ENVDIR=/etc/stage-messenger
  local ENVF="$ENVDIR/env"
  local fails=0

  pass() { echo "PASS  $*"; }
  fail() { echo "FAIL  $*"; fails=$((fails+1)); }
  die()  { echo "FAIL  $*"; echo; echo "RESULT: FAIL — nothing further was changed."; exit 1; }

  [ -d /data/data/com.termux ] && die "this runs on the server, not the phone (use: ssh joseph@192.168.1.121 'bash ...')"
  [ "$(id -u)" = 1000 ] || die "expected to run as joseph (uid 1000), got uid $(id -u)"
  command -v docker >/dev/null || die "docker not found"
  docker compose version >/dev/null 2>&1 || die "docker compose plugin not found"
  docker network inspect caddy_default >/dev/null 2>&1 || die "docker network caddy_default not found (is Caddy running?)"
  pass "host checks (uid 1000, docker, compose, caddy_default)"

  # 1. Code
  if [ -d "$SRC/.git" ]; then
    git -C "$SRC" pull --ff-only -q || die "git pull failed in $SRC"
  else
    mkdir -p "$HOME/src"
    git clone -q "$REPO_URL" "$SRC" || die "git clone failed"
  fi
  pass "code at $(git -C "$SRC" rev-parse --short HEAD) ($(git -C "$SRC" log -1 --format=%s | cut -c1-60))"

  # 2. Secret env file (needs the one-time sudo step below)
  if [ ! -d "$ENVDIR" ] || [ ! -w "$ENVDIR" ]; then
    echo "      One-time step — run this in an interactive SSH session, then re-run this script:"
    echo "        ssh -t joseph@192.168.1.121 'sudo install -d -o joseph -g joseph -m 700 $ENVDIR'"
    die "$ENVDIR missing or not writable by joseph"
  fi
  if [ ! -s "$ENVF" ]; then
    ( umask 077; printf 'SECRET_KEY=%s\n' "$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')" > "$ENVF" )
    pass "created $ENVF with a new SECRET_KEY (not shown)"
  else
    pass "$ENVF exists (kept)"
  fi
  chmod 600 "$ENVF"
  grep -q '^SECRET_KEY=.\{32,\}$' "$ENVF" || die "$ENVF has no usable SECRET_KEY line"

  # 3. Service directory + compose file
  mkdir -p "$SVC/data"
  if [ -f "$SVC/docker-compose.yml" ] && ! cmp -s "$SRC/deploy/docker-compose.yml" "$SVC/docker-compose.yml"; then
    cp "$SVC/docker-compose.yml" "$SVC/docker-compose.yml.bak.$(date +%F-%H%M)"
    echo "      compose file changed — previous copy backed up"
  fi
  cp "$SRC/deploy/docker-compose.yml" "$SVC/docker-compose.yml"
  pass "service dir $SVC (data/ is gitignored by ~/services/.gitignore */data/)"

  # 4. Build + (re)start
  echo "      building image (first run takes a minute)..."
  ( set -o pipefail; cd "$SVC" && docker compose up -d --build --quiet-pull 2>&1 | tail -n 3 ) \
    || die "docker compose up failed — see: cd $SVC && docker compose logs --tail 50"

  # 5. Health
  local st="" i
  for i in $(seq 1 30); do
    st=$(docker inspect -f '{{.State.Health.Status}}' stage-messenger 2>/dev/null)
    [ "$st" = healthy ] && break
    sleep 2
  done
  [ "$st" = healthy ] && pass "container healthy" || fail "container health: ${st:-not running} (docker logs stage-messenger --tail 50)"

  local h
  h=$(docker exec caddy wget -qO- -T 5 http://stage-messenger:8000/healthz 2>/dev/null)
  case "$h" in *'"ok":true'*) pass "Caddy can reach stage-messenger:8000 over caddy_default" ;;
                *) fail "Caddy could not reach stage-messenger:8000 (got: ${h:-nothing})" ;; esac

  # 6. Ledger untouched
  local code
  code=$(curl -s -o /dev/null -w '%{http_code}' -H 'Host: ledger.stage-messenger.com' http://127.0.0.1:8080/)
  [ "$code" = 200 ] && pass "Ledger still serves (HTTP 200 via Caddy)" || fail "Ledger via Caddy returned HTTP $code"
  curl -s http://172.18.0.1:8091/api/backup/health | grep -q '"ok":true' \
    && pass "ledger-backup health ok" || fail "ledger-backup health check"

  # 7. Admin password status
  if docker exec stage-messenger python -c 'import store,sys; sys.exit(0 if store.admin_password_set() else 1)' 2>/dev/null; then
    pass "admin password is set"
  else
    echo "INFO  admin password not set yet. Set it (interactive, not echoed):"
    echo "        ssh -t joseph@192.168.1.121 'cd ~/services/stage-messenger && docker compose exec stage-messenger python manage.py set-admin-password'"
  fi

  echo
  if [ "$fails" -eq 0 ]; then echo "RESULT: PASS"; else echo "RESULT: FAIL ($fails check(s))"; exit 1; fi
}

main "$@"; exit
