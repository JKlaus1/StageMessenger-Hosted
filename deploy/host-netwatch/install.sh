#!/usr/bin/env bash
# Install / update netwatch + EEE-off on the home server. Idempotent; run with sudo.
#   ssh -t joseph@192.168.1.121 'cd ~/src/StageMessenger-Hosted && git pull -q && sudo bash deploy/host-netwatch/install.sh'
# Changes only: /usr/local/sbin/netwatch (+ /usr/local/bin symlink), /etc/default/netwatch (created once),
# netwatch.{service,timer}, netwatch-eee.service, and installs ethtool + ping if missing.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
IFACE=enp2s0
fails=0
pass() { echo "PASS  $*"; }
fail() { echo "FAIL  $*"; fails=$((fails+1)); }
die()  { echo "FAIL  $*"; echo; echo "RESULT: FAIL"; exit 1; }

[ -d /data/data/com.termux ] && die "run this on the server, not the phone"
[ "$(id -u)" = 0 ] || die "needs root: sudo bash $0"
ip link show "$IFACE" >/dev/null 2>&1 || die "interface $IFACE not found"
pass "on $(hostname), $IFACE present"

need=()
command -v ethtool >/dev/null || need+=(ethtool)
command -v ping    >/dev/null || need+=(iputils-ping)
if [ ${#need[@]} -gt 0 ]; then
  DEBIAN_FRONTEND=noninteractive apt-get install -y -q "${need[@]}" >/dev/null 2>&1 \
    && pass "installed ${need[*]}" || die "apt-get install ${need[*]} failed"
else
  pass "ethtool and ping present"
fi

install -m 0755 "$HERE/netwatch" /usr/local/sbin/netwatch
ln -sfn /usr/local/sbin/netwatch /usr/local/bin/netwatch     # so plain `netwatch status` works for non-root users
for u in netwatch.service netwatch.timer netwatch-eee.service; do
  install -m 0644 "$HERE/$u" "/etc/systemd/system/$u"
done
if [ ! -f /etc/default/netwatch ]; then
  gw=$(ip -4 route show default dev "$IFACE" | awk '{print $3; exit}')
  cat > /etc/default/netwatch <<CONF
# netwatch settings (created $(date +%F)). Edit, then nothing to restart: read on every check.
IFACE=$IFACE
GW_FALLBACK=${gw:-192.168.1.2}
PROBE=1.1.1.1
FAILS_BEFORE_RESET=3
MIN_RESET_GAP=600
CONF
  pass "created /etc/default/netwatch (gateway fallback ${gw:-192.168.1.2})"
else
  pass "/etc/default/netwatch kept"
fi
systemctl daemon-reload
systemctl enable --now netwatch.timer >/dev/null 2>&1 && pass "netwatch.timer enabled (every 2 min)" || fail "netwatch.timer"
systemctl enable netwatch-eee.service >/dev/null 2>&1 && pass "netwatch-eee.service enabled (runs at every boot)" || fail "netwatch-eee enable"

if msg=$(/usr/local/sbin/netwatch probe); then pass "health check: $msg"; else fail "health check: $msg"; fi

eee=$(ethtool --show-eee "$IFACE" 2>&1)
if echo "$eee" | grep -qiE 'EEE status: *(disabled|not supported)'; then
  pass "EEE already off: $(echo "$eee" | awk -F': *' '/EEE status/{print $2; exit}')"
  systemctl start netwatch-eee.service >/dev/null 2>&1 || true
elif echo "$eee" | grep -qiE 'not supported|operation not supported'; then
  pass "EEE not supported on $IFACE — nothing to do"
else
  echo "      turning EEE off now: $IFACE renegotiates, so the network drops for ~5 s ..."
  systemctl restart netwatch-eee.service
  sleep 8
  state=$(ethtool --show-eee "$IFACE" 2>&1 | awk -F': *' '/EEE status/{print $2; exit}')
  case "$state" in
    disabled*|"") pass "EEE now: ${state:-off}" ;;
    *) fail "EEE still: $state" ;;
  esac
  for _ in $(seq 1 12); do /usr/local/sbin/netwatch probe >/dev/null 2>&1 && break; sleep 5; done
  if msg=$(/usr/local/sbin/netwatch probe); then pass "network back: $msg"; else fail "network after EEE change: $msg"; fi
fi

echo
/usr/local/sbin/netwatch status
echo
if [ "$fails" -eq 0 ]; then echo "RESULT: PASS"; else echo "RESULT: FAIL ($fails)"; exit 1; fi
