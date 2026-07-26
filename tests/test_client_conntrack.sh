#!/bin/sh
# Fixture test for the fail-closed per-client conntrack helper. It deliberately
# exercises a single conntrack row containing two LAN clients: each client is
# counted once, matching `conntrack -L | grep <ip> | wc -l` semantics without
# emitting a remote-IP or port label.
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/bin" "$WORK/out" "$WORK/libubox"
cp "$ROOT/tests/fixtures/gethosthints.json" "$WORK/gethosthints.json"
cat > "$WORK/conntrack_rows" <<'ROWS'
tcp      6 431999 ESTABLISHED src=192.168.0.42 dst=203.0.113.10 sport=50000 dport=443 src=203.0.113.10 dst=192.168.0.42 sport=443 dport=50000 [ASSURED] mark=0 use=1
udp      17 29 src=192.168.0.42 dst=1.1.1.1 sport=51000 dport=53 src=1.1.1.1 dst=192.168.0.42 sport=53 dport=51000 mark=0 use=1
tcp      6 431999 ESTABLISHED src=192.168.0.77 dst=192.168.0.42 sport=1234 dport=80 src=192.168.0.42 dst=192.168.0.77 sport=80 dport=1234 [ASSURED] mark=0 use=1
ROWS

cat > "$WORK/bin/ubus" <<EOF
#!/bin/sh
case "\$2 \$3" in
  'luci-rpc getHostHints') cat "$WORK/gethosthints.json" ;;
  'network.wireless status') cat "$ROOT/tests/fixtures/wireless_status.json" ;;
  *) exit 1 ;;
esac
EOF
chmod +x "$WORK/bin/ubus"

cat > "$WORK/bin/conntrack" <<EOF
#!/bin/sh
cat "$WORK/conntrack_rows"
EOF
chmod +x "$WORK/bin/conntrack"

cat > "$WORK/libubox/jshn.sh" <<'EOF'
json_init() { :; }
json_load() { JSHN_JSON=$1; JSHN_PATH='.'; JSHN_STACK=''; }
json_cleanup() { :; }
json_select() {
  case "$1" in
    ..)
      JSHN_PATH=${JSHN_STACK##*|}
      JSHN_STACK=${JSHN_STACK%|*}
      ;;
    *)
      # Match real jshn: fail closed when the key/index is absent and do not
      # leave the cursor on a missing path. This exercises a
      # missing interface `config` object.
      new_stack="$JSHN_STACK|$JSHN_PATH"
      case "$1" in
        *[!0-9]*) new_path="$JSHN_PATH[\"$1\"]" ;;
        *) new_path="$JSHN_PATH[$(( $1 - 1 ))]" ;;
      esac
      if ! printf '%s' "$JSHN_JSON" | jq -e "$new_path | type != \"null\"" >/dev/null 2>&1; then
        return 1
      fi
      JSHN_STACK=$new_stack
      JSHN_PATH=$new_path
      ;;
  esac
}
json_get_keys() { eval "$1=\$(printf '%s' \"\$JSHN_JSON\" | jq -r \"\$JSHN_PATH | if type == \\\"array\\\" then range(0; length) + 1 else keys[] end\")"; }
json_get_var() { case "$2" in *[!0-9]*) path="$JSHN_PATH[\"$2\"]" ;; *) path="$JSHN_PATH[$(( $2 - 1 ))]" ;; esac; eval "$1=\$(printf '%s' \"\$JSHN_JSON\" | jq -r \"\$path // empty\")"; }
EOF

PATH="$WORK/bin:$PATH" OPENWRT_MONITOR_TEXTFILE_DIR="$WORK/out" \
  OPENWRT_MONITOR_JSHN_PATH="$WORK/libubox/jshn.sh" \
  OPENWRT_MONITOR_LOGREAD_BIN=missing-logread \
  sh "$ROOT/openwrt/scripts/openwrt-monitor-client-conntrack.sh"

OUT="$WORK/out/openwrt_client_conntrack.prom"
python3 "$ROOT/tests/check_exposition.py" "$OUT"
grep -q '^openwrt_client_conntrack_collector_available 1$' "$OUT" || { echo 'FAIL: conntrack collector unavailable'; exit 1; }
grep -q '^openwrt_wifi_assoc_events_collector_available 0$' "$OUT" || { echo 'FAIL: no logread must be explicit unavailable'; exit 1; }
grep -q 'openwrt_client_conntrack_entries{mac="a4:83:e7:aa:bb:cc"} 3' "$OUT" || { echo 'FAIL: TV conntrack count'; exit 1; }
grep -q 'openwrt_client_conntrack_entries{mac="2a:11:22:33:44:55"} 1' "$OUT" || { echo 'FAIL: phone conntrack count'; exit 1; }
grep -q 'openwrt_client_conntrack_entries{mac="78:8c:b5:93:fb:a9"} 0' "$OUT" || { echo 'FAIL: known idle client must emit zero'; exit 1; }
grep -q '^openwrt_client_conntrack_truncated 0$' "$OUT" || { echo 'FAIL: small host set should not be truncated'; exit 1; }

PATH="$WORK/bin:$PATH" OPENWRT_MONITOR_TEXTFILE_DIR="$WORK/out" \
  OPENWRT_MONITOR_JSHN_PATH="$WORK/libubox/jshn.sh" \
  OPENWRT_MONITOR_LOGREAD_BIN=missing-logread \
  OPENWRT_MONITOR_CONNTRACK_BIN=missing-conntrack \
  OPENWRT_MONITOR_CONNTRACK_PROC="$WORK/conntrack_rows" \
  sh "$ROOT/openwrt/scripts/openwrt-monitor-client-conntrack.sh"
grep -q '^openwrt_client_conntrack_collector_available 1$' "$OUT" || { echo 'FAIL: readable proc conntrack fallback unavailable'; exit 1; }
grep -q 'openwrt_client_conntrack_entries{mac="a4:83:e7:aa:bb:cc"} 3' "$OUT" || { echo 'FAIL: proc fallback TV conntrack count'; exit 1; }
grep -q '^openwrt_wifi_assoc_events_collector_available 0$' "$OUT" || { echo 'FAIL: proc fallback no logread must be explicit unavailable'; exit 1; }

cat > "$WORK/dhcp.leases" <<'EOF'
1000 A4:83:E7:AA:BB:CC 192.168.0.42 living-room-tv *
1000 2a:11:22:33:44:55 192.168.0.77 phone-random *
EOF
PATH="$WORK/bin:$PATH" OPENWRT_MONITOR_TEXTFILE_DIR="$WORK/out" \
  OPENWRT_MONITOR_JSHN_PATH="$WORK/missing-jshn.sh" \
  OPENWRT_MONITOR_LOGREAD_BIN=missing-logread \
  OPENWRT_MONITOR_DHCP_LEASES="$WORK/dhcp.leases" \
  OPENWRT_MONITOR_ARP_FILE="$WORK/missing-arp" \
  sh "$ROOT/openwrt/scripts/openwrt-monitor-client-conntrack.sh"
grep -q '^openwrt_client_conntrack_collector_available 1$' "$OUT" || { echo 'FAIL: DHCP lease fallback unavailable'; exit 1; }
grep -q 'openwrt_client_conntrack_entries{mac="a4:83:e7:aa:bb:cc"} 3' "$OUT" || { echo 'FAIL: DHCP lease fallback TV conntrack count'; exit 1; }
grep -q '^openwrt_wifi_assoc_events_collector_available 0$' "$OUT" || { echo 'FAIL: missing jshn must leave association events unavailable'; exit 1; }

# Hostapd detail is preserved in Loki; this verifies the Prometheus companion
# is only the bounded AP/SSID/event aggregate and does not double-count the
# same log-ring rows on the next cron run.
cat > "$WORK/bin/logread" <<'EOF'
#!/bin/sh
printf '%s\n' 'Thu Jul 22 12:00:00 2026 daemon.info hostapd: wlan1: AP-STA-CONNECTED aa:bb:cc:dd:ee:ff'
printf '%s\n' 'Thu Jul 22 12:00:01 2026 daemon.info hostapd: wlan2: AP-STA-DISCONNECTED 11:22:33:44:55:66'
printf '%s\n' 'Thu Jul 22 12:00:02 2026 daemon.info hostapd: wlan3: AP-STA-CONNECTED aa:bb:cc:dd:ee:02'
EOF
chmod +x "$WORK/bin/logread"
PATH="$WORK/bin:$PATH" OPENWRT_MONITOR_TEXTFILE_DIR="$WORK/out" \
  OPENWRT_MONITOR_ASSOC_EVENTS_STATE="$WORK/assoc-events" \
  OPENWRT_MONITOR_JSHN_PATH="$WORK/libubox/jshn.sh" \
  sh "$ROOT/openwrt/scripts/openwrt-monitor-client-conntrack.sh"
grep -q '^openwrt_wifi_assoc_events_collector_available 1$' "$OUT" || { echo 'FAIL: association collector unavailable'; exit 1; }
grep -q 'openwrt_wifi_assoc_events_total{ap=".*",ssid="Home-5G",event="connected"} 1' "$OUT" || { echo 'FAIL: connected aggregate missing'; exit 1; }
grep -q 'openwrt_wifi_assoc_events_total{ap=".*",ssid="Guest-5G",event="disconnected"} 1' "$OUT" || { echo 'FAIL: disconnected aggregate missing'; exit 1; }
! grep -q 'openwrt_wifi_assoc_events_total{[^}]*mac=' "$OUT" || { echo 'FAIL: roaming counter must not have mac'; exit 1; }

# wlan3 in the shared tests/fixtures/wireless_status.json fixture is named
# `Lab Net"5G` (a space and a double quote). This expected value must stay
# identical to EXPECTED_SANITIZED_SSID in tests/test_client_inventory.lua --
# that pairing is what keeps the shell and Lua sanitizing converged. The
# the Lua collectors emitted the raw SSID here, so one physical SSID appeared
# under two label values and joins between the two metric families returned
# nothing.
grep -q 'openwrt_wifi_assoc_events_total{ap=".*",ssid="Lab_Net_5G",event="connected"} 1' "$OUT" \
  || { echo 'FAIL: ssid with a space and a quote was not sanitized to Lab_Net_5G'; exit 1; }
! grep -q 'ssid="[^"]* ' "$OUT" || { echo 'FAIL: raw space reached an ssid label value'; exit 1; }
PATH="$WORK/bin:$PATH" OPENWRT_MONITOR_TEXTFILE_DIR="$WORK/out" \
  OPENWRT_MONITOR_ASSOC_EVENTS_STATE="$WORK/assoc-events" \
  OPENWRT_MONITOR_JSHN_PATH="$WORK/libubox/jshn.sh" \
  sh "$ROOT/openwrt/scripts/openwrt-monitor-client-conntrack.sh"
grep -q 'openwrt_wifi_assoc_events_total{ap=".*",ssid="Home-5G",event="connected"} 1' "$OUT" || { echo 'FAIL: log-ring event was counted twice'; exit 1; }

# Cap conntrack series to CLIENT_CONNTRACK_MAX, keeping the busiest clients
# rather than an arbitrary awk hash-order subset of getHostHints.
CLIENT_CONNTRACK_MAX=3
host_count=$((CLIENT_CONNTRACK_MAX + 10))
{
  printf '{\n'
  i=1
  while [ "$i" -le "$host_count" ]; do
    mac=$(printf '02:00:00:00:00:%02x' "$i")
    ip="192.168.10.$i"
    comma=,
    [ "$i" -eq "$host_count" ] && comma=
    printf '  "%s": {"name":"host-%02d","ipaddrs":["%s"],"ip6addrs":[]}%s\n' "$mac" "$i" "$ip" "$comma"
    i=$((i + 1))
  done
  printf '}\n'
} > "$WORK/gethosthints.json"
: > "$WORK/conntrack_rows"
i=1
while [ "$i" -le "$host_count" ]; do
  case "$i" in
    1) count=5 ;;
    2) count=4 ;;
    3) count=3 ;;
    4) count=2 ;;
    *) count=0 ;;
  esac
  j=1
  while [ "$j" -le "$count" ]; do
    printf 'tcp 6 100 ESTABLISHED src=192.168.10.%d dst=203.0.113.%d sport=%d dport=443 src=203.0.113.%d dst=192.168.10.%d sport=443 dport=%d\n' \
      "$i" "$j" "$((50000 + j))" "$j" "$i" "$((50000 + j))" >> "$WORK/conntrack_rows"
    j=$((j + 1))
  done
  i=$((i + 1))
done
PATH="$WORK/bin:$PATH" OPENWRT_MONITOR_TEXTFILE_DIR="$WORK/out" \
  OPENWRT_MONITOR_ASSOC_EVENTS_STATE="$WORK/assoc-events" \
  OPENWRT_MONITOR_JSHN_PATH="$WORK/libubox/jshn.sh" \
  CLIENT_CONNTRACK_MAX="$CLIENT_CONNTRACK_MAX" \
  sh "$ROOT/openwrt/scripts/openwrt-monitor-client-conntrack.sh"
python3 "$ROOT/tests/check_exposition.py" "$OUT"
series_count=$(grep -c '^openwrt_client_conntrack_entries{' "$OUT")
[ "$series_count" = "$CLIENT_CONNTRACK_MAX" ] || { echo "FAIL: expected $CLIENT_CONNTRACK_MAX capped conntrack series, got $series_count"; exit 1; }
grep -q '^openwrt_client_conntrack_truncated 1$' "$OUT" || { echo 'FAIL: truncated host set did not report truncation'; exit 1; }
grep -q 'openwrt_client_conntrack_entries{mac="02:00:00:00:00:01"} 5' "$OUT" || { echo 'FAIL: busiest client was dropped'; exit 1; }
grep -q 'openwrt_client_conntrack_entries{mac="02:00:00:00:00:02"} 4' "$OUT" || { echo 'FAIL: second-busiest client was dropped'; exit 1; }
grep -q 'openwrt_client_conntrack_entries{mac="02:00:00:00:00:03"} 3' "$OUT" || { echo 'FAIL: third-busiest client was dropped'; exit 1; }
! grep -q 'openwrt_client_conntrack_entries{mac="02:00:00:00:00:04"}' "$OUT" || { echo 'FAIL: lower-count client survived cap ahead of a busier client'; exit 1; }
! grep -q 'openwrt_client_conntrack_entries{mac="02:00:00:00:00:0d"}' "$OUT" || { echo 'FAIL: idle tail survived cap'; exit 1; }

# An interface entry without `config` must not inherit the previous
# interface's SSID. Without the per-iteration reset, wlan-stale would map to
# Home-5G and both roam events would count under that SSID.
cat > "$WORK/wireless_missing_config.json" <<'EOF'
{
  "radio0": {
    "config": {"band": "5g"},
    "interfaces": [
      {"ifname": "wlan1", "config": {"ssid": "Home-5G", "network": ["lan"]}},
      {"ifname": "wlan-stale", "section": "orphan"}
    ]
  }
}
EOF
cat > "$WORK/bin/ubus" <<EOF
#!/bin/sh
case "\$2 \$3" in
  'luci-rpc getHostHints') cat "$WORK/gethosthints.json" ;;
  'network.wireless status') cat "$WORK/wireless_missing_config.json" ;;
  *) exit 1 ;;
esac
EOF
chmod +x "$WORK/bin/ubus"
cat > "$WORK/bin/logread" <<'EOF'
#!/bin/sh
printf '%s\n' 'Thu Jul 22 12:00:00 2026 daemon.info hostapd: wlan1: AP-STA-CONNECTED aa:bb:cc:dd:ee:ff'
printf '%s\n' 'Thu Jul 22 12:00:01 2026 daemon.info hostapd: wlan-stale: AP-STA-CONNECTED 11:22:33:44:55:66'
EOF
chmod +x "$WORK/bin/logread"
cp "$ROOT/tests/fixtures/gethosthints.json" "$WORK/gethosthints.json"
PATH="$WORK/bin:$PATH" OPENWRT_MONITOR_TEXTFILE_DIR="$WORK/out" \
  OPENWRT_MONITOR_ASSOC_EVENTS_STATE="$WORK/assoc-events-missing-config" \
  OPENWRT_MONITOR_JSHN_PATH="$WORK/libubox/jshn.sh" \
  sh "$ROOT/openwrt/scripts/openwrt-monitor-client-conntrack.sh"
grep -q 'openwrt_wifi_assoc_events_total{ap=".*",ssid="Home-5G",event="connected"} 1' "$OUT" \
  || { echo 'FAIL: expected only the configured ifname roam under Home-5G'; exit 1; }
! grep -q 'openwrt_wifi_assoc_events_total{ap=".*",ssid="Home-5G",event="connected"} 2' "$OUT" \
  || { echo 'FAIL: no-config ifname inherited previous SSID (stale ssid leak)'; exit 1; }
! grep -q 'ssid="orphan"' "$OUT" \
  || { echo 'FAIL: unexpected ssid label from non-config fields'; exit 1; }

# A failed conntrack command must replace prior data with availability only.
cp "$ROOT/tests/fixtures/gethosthints.json" "$WORK/gethosthints.json"
cat > "$WORK/bin/ubus" <<EOF
#!/bin/sh
case "\$2 \$3" in
  'luci-rpc getHostHints') cat "$WORK/gethosthints.json" ;;
  'network.wireless status') cat "$ROOT/tests/fixtures/wireless_status.json" ;;
  *) exit 1 ;;
esac
EOF
chmod +x "$WORK/bin/ubus"
cat > "$WORK/bin/conntrack" <<'EOF'
#!/bin/sh
exit 1
EOF
chmod +x "$WORK/bin/conntrack"
PATH="$WORK/bin:$PATH" OPENWRT_MONITOR_TEXTFILE_DIR="$WORK/out" \
  OPENWRT_MONITOR_JSHN_PATH="$WORK/libubox/jshn.sh" \
  OPENWRT_MONITOR_ASSOC_EVENTS_STATE="$WORK/assoc-events-failed-conntrack" \
  OPENWRT_MONITOR_CONNTRACK_PROC="$WORK/missing-nf-conntrack" \
  OPENWRT_MONITOR_CONNTRACK_LEGACY_PROC="$WORK/missing-ip-conntrack" \
  sh "$ROOT/openwrt/scripts/openwrt-monitor-client-conntrack.sh"
grep -q '^openwrt_client_conntrack_collector_available 0$' "$OUT" || { echo 'FAIL: conntrack failure did not fail closed'; exit 1; }
! grep -q '^openwrt_client_conntrack_entries{' "$OUT" || { echo 'FAIL: failed run retained conntrack values'; exit 1; }
grep -q '^openwrt_wifi_assoc_events_collector_available 1$' "$OUT" || { echo 'FAIL: conntrack failure suppressed association events'; exit 1; }

echo 'PASS: tests/test_client_conntrack.sh'
