#!/usr/bin/env bash
# Scenario test for netwatch with fake ip/ping/dhcpcd/logger/sleep. Runs anywhere:  bash test_netwatch.sh
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
mkdir -p "$T/bin" "$T/run" "$T/state" "$T/sim"
export PATH="$T/bin:$PATH" NETWATCH_RUN="$T/run" NETWATCH_STATE="$T/state" NETWATCH_CONF="$T/none" SIM="$T/sim"
passed=0
ok()  { passed=$((passed+1)); echo "ok    $*"; }
bad() { echo "FAIL  $*"; exit 1; }

# sim knobs: addr (IPv4 on enp2s0), gw_up/net_up (ping answers), heal_on_reset (link bounce fixes it)
set_sim() { echo "$2" > "$SIM/$1"; }
cat > "$T/bin/ip" <<'S'
#!/usr/bin/env bash
case "$*" in
  "-4 -o addr show dev enp2s0 scope global") a=$(cat "$SIM/addr"); [ -n "$a" ] && echo "2: enp2s0    inet $a brd x scope global enp2s0" ;;
  "-4 route show default dev enp2s0") case "$(cat "$SIM/addr")" in 192.*) echo "default via 192.168.1.2 dev enp2s0" ;; esac ;;
  "link set enp2s0 down") echo down >> "$SIM/calls" ;;
  "link set enp2s0 up") echo up >> "$SIM/calls"
     if [ "$(cat "$SIM/heal_on_reset")" = 1 ]; then echo 192.168.1.121/24 > "$SIM/addr"; echo 1 > "$SIM/gw_up"; fi ;;
  *) echo "unexpected ip $*" >&2; exit 9 ;;
esac
S
cat > "$T/bin/ping" <<'S'
#!/usr/bin/env bash
t=${!#}; [ -z "$(cat "$SIM/addr")" ] && exit 1
case "$(cat "$SIM/addr")" in 169.254.*) exit 1 ;; esac
[ "$t" = 1.1.1.1 ] && [ "$(cat "$SIM/net_up")" = 1 ] && exit 0
[ "$t" = 192.168.1.2 ] && [ "$(cat "$SIM/gw_up")" = 1 ] && exit 0
exit 1
S
printf '#!/usr/bin/env bash\necho "dhcpcd $*" >> "$SIM/calls"\n' > "$T/bin/dhcpcd"
printf '#!/usr/bin/env bash\nshift 2; echo "$*" >> "$SIM/log"\n' > "$T/bin/logger"
printf '#!/usr/bin/env bash\nexit 0\n' > "$T/bin/sleep"
printf '#!/usr/bin/env bash\necho inactive\n' > "$T/bin/systemctl"
chmod +x "$T/bin/"*
NW="$HERE/netwatch"
fresh() { rm -f "$T/run/"* "$T/state/"* "$SIM/calls" "$SIM/log"; touch "$SIM/calls" "$SIM/log"; }
resets() { grep -c '^down$' "$SIM/calls"; }

# 1 healthy
fresh; set_sim addr 192.168.1.121/24; set_sim gw_up 1; set_sim net_up 1; set_sim heal_on_reset 1
"$NW" check >/dev/null && [ "$(resets)" = 0 ] && ok "healthy: no action" || bad "healthy"
"$NW" probe | grep -q 'gateway 192.168.1.2 answers' && ok "probe reports gateway" || bad "probe"

# 2 gateway silent but internet answers -> still healthy
set_sim gw_up 0; "$NW" check >/dev/null && ok "gateway ping blocked but 1.1.1.1 answers: healthy" || bad "probe fallback"

# 3 the Nov 1 failure: lease lost, link-local only, nothing answers; bounce heals it
fresh; set_sim addr 169.254.130.91/16; set_sim gw_up 0; set_sim net_up 0; set_sim heal_on_reset 1
"$NW" check >/dev/null; "$NW" check >/dev/null
[ "$(resets)" = 0 ] && ok "2 failures: no reset yet" || bad "early reset"
grep -q 'no IPv4 lease' "$SIM/log" && ok "link-local address detected as unhealthy" || bad "ll detect"
"$NW" check >/dev/null
[ "$(resets)" = 1 ] && ok "3rd failure: link reset" || bad "reset on 3rd ($(resets))"
grep -q 'RECOVERY OK' "$SIM/log" && [ "$(cat "$T/run/fails")" = 0 ] && ok "recovery verified, counter cleared" || bad "recovery ok"
! grep -q dhcpcd "$SIM/calls" && ok "no dhcpcd nudge needed when the lease returns" || bad "unneeded nudge"
[ -s "$T/state/resets.log" ] && ok "reset recorded in resets.log" || bad "resets.log"

# 4 stall that a bounce doesn't fix: rate-limit further resets
fresh; set_sim addr 192.168.1.121/24; set_sim gw_up 0; set_sim net_up 0; set_sim heal_on_reset 0
for _ in 1 2 3; do "$NW" check >/dev/null; done
[ "$(resets)" = 1 ] && ! grep -q dhcpcd "$SIM/calls" && ok "address kept but no traffic: reset, no pointless DHCP nudge" || bad "no-nudge"
grep -q 'did not restore' "$SIM/log" && ok "failed recovery logged" || bad "fail log"
for _ in 1 2 3 4 5; do "$NW" check >/dev/null; done
[ "$(resets)" = 1 ] && grep -q 'waiting before another reset' "$SIM/log" && ok "no second reset within MIN_RESET_GAP" || bad "flap guard ($(resets))"
echo $(( $(date +%s) - 601 )) > "$T/state/last_reset"
"$NW" check >/dev/null
[ "$(resets)" = 2 ] && ok "retries after the gap" || bad "retry after gap"

# 4b lease lost and the bounce doesn't bring it back: dhcpcd gets nudged
fresh; set_sim addr 169.254.130.91/16; set_sim gw_up 0; set_sim net_up 0; set_sim heal_on_reset 0
for _ in 1 2 3; do "$NW" check >/dev/null; done
[ "$(resets)" = 1 ] && grep -q 'dhcpcd -n enp2s0' "$SIM/calls" && ok "no lease after reset: dhcpcd rebind nudged" || bad "nudge"

# 5 recovers on its own between checks: counter clears, no reset
fresh; set_sim addr 192.168.1.121/24; set_sim gw_up 0; set_sim net_up 0
"$NW" check >/dev/null; "$NW" check >/dev/null; set_sim gw_up 1; "$NW" check >/dev/null
[ "$(resets)" = 0 ] && [ "$(cat "$T/run/fails")" = 0 ] && grep -q 'healthy again after 2' "$SIM/log" && ok "transient blip: counter clears, no reset" || bad "transient"

# 6 status + manual reset + bad arg
"$NW" status | grep -q 'HEALTHY' && ok "status output" || bad "status"
"$NW" bogus >/dev/null; [ $? = 2 ] && ok "unknown command rejected" || bad "usage"

echo; echo "ALL $passed NETWATCH CHECKS PASSED"
