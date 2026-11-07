# host-netwatch — self-healing Ethernet for the home server

**Why it exists.** On 2026-11-01 the server's Intel I226-V NIC (`igc`, `enp2s0`) stopped passing
traffic while the link still showed "up" on both ends. DHCP renewal failed, the lease expired at
05:32, `dhcpcd` fell back to `169.254.130.91`, and Ledger and stage-messenger.com (both behind the
`nab9-server` tunnel) were offline until 2026-11-07, when the UniFi switch port was toggled.
The kernel logged nothing: a silent stall.

**What it does.**
- `netwatch.timer` runs `netwatch check` every 2 minutes. The check is healthy when `enp2s0` has a
  real IPv4 address and either the gateway or `1.1.1.1` answers ping.
- After 3 failed checks in a row (about 6 minutes), it does what toggling the switch port did:
  `ip link set enp2s0 down/up`, which resets the igc hardware. If no lease comes back after about
  30 seconds, it also sends a `dhcpcd -n enp2s0` rebind.
- It never resets more than once every 10 minutes, and it never touches Docker, Caddy or `cloudflared`.
- `netwatch-eee.service` turns Energy-Efficient Ethernet off at every boot. EEE is the most common
  trigger for I226-V stalls.

**Install / update** (idempotent; turning EEE off the first time drops the network for about 5 s):

    ssh -t joseph@192.168.1.121 'cd ~/src/StageMessenger-Hosted && git pull -q && sudo bash deploy/host-netwatch/install.sh'

**Use**

| Command | |
|---|---|
| `netwatch status` | health, fail count, last reset, EEE state, timer |
| `journalctl -t netwatch` | every failed check and recovery |
| `cat /var/lib/netwatch/resets.log` | one line per reset |
| `sudo netwatch reset` | force a link reset now |
| `/etc/default/netwatch` | thresholds; read on every check, nothing to restart |

**Test** (anywhere, uses fakes for `ip`/`ping`/`dhcpcd`): `bash deploy/host-netwatch/test_netwatch.sh`

**If resets keep happening** (check `resets.log`), the next step is disabling PCIe power management
for the NIC (`pcie_aspm=off`, or `igc` with `pcie_port_pm=off`), which needs a reboot.
