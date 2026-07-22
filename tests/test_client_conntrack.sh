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

cat > "$WORK/bin/ubus" <<EOF
#!/bin/sh
case "\$2 \$3" in
  'luci-rpc getHostHints') cat "$ROOT/tests/fixtures/gethosthints.json" ;;
  'network.wireless status') cat "$ROOT/tests/fixtures/wireless_status.json" ;;
  *) exit 1 ;;
esac
EOF
chmod +x "$WORK/bin/ubus"

cat > "$WORK/bin/conntrack" <<'EOF'
#!/bin/sh
cat <<'ROWS'
tcp      6 431999 ESTABLISHED src=192.168.0.42 dst=203.0.113.10 sport=50000 dport=443 src=203.0.113.10 dst=192.168.0.42 sport=443 dport=50000 [ASSURED] mark=0 use=1
udp      17 29 src=192.168.0.42 dst=1.1.1.1 sport=51000 dport=53 src=1.1.1.1 dst=192.168.0.42 sport=53 dport=51000 mark=0 use=1
tcp      6 431999 ESTABLISHED src=192.168.0.77 dst=192.168.0.42 sport=1234 dport=80 src=192.168.0.42 dst=192.168.0.77 sport=80 dport=1234 [ASSURED] mark=0 use=1
ROWS
EOF
chmod +x "$WORK/bin/conntrack"

cat > "$WORK/libubox/jshn.sh" <<'EOF'
json_init() { :; }
json_load() { JSHN_JSON=$1; JSHN_PATH='.'; JSHN_STACK=''; }
json_cleanup() { :; }
json_select() {
  case "$1" in
    ..) JSHN_PATH=${JSHN_STACK##*|}; JSHN_STACK=${JSHN_STACK%|*} ;;
    *) JSHN_STACK="$JSHN_STACK|$JSHN_PATH"; case "$1" in *[!0-9]*) JSHN_PATH="$JSHN_PATH[\"$1\"]" ;; *) JSHN_PATH="$JSHN_PATH[$(( $1 - 1 ))]" ;; esac ;;
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

# Hostapd detail is preserved in Loki; this verifies the Prometheus companion
# is only the bounded AP/SSID/event aggregate and does not double-count the
# same log-ring rows on the next cron run.
cat > "$WORK/bin/logread" <<'EOF'
#!/bin/sh
printf '%s\n' 'Thu Jul 22 12:00:00 2026 daemon.info hostapd: wlan1: AP-STA-CONNECTED aa:bb:cc:dd:ee:ff'
printf '%s\n' 'Thu Jul 22 12:00:01 2026 daemon.info hostapd: wlan2: AP-STA-DISCONNECTED 11:22:33:44:55:66'
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
PATH="$WORK/bin:$PATH" OPENWRT_MONITOR_TEXTFILE_DIR="$WORK/out" \
  OPENWRT_MONITOR_ASSOC_EVENTS_STATE="$WORK/assoc-events" \
  OPENWRT_MONITOR_JSHN_PATH="$WORK/libubox/jshn.sh" \
  sh "$ROOT/openwrt/scripts/openwrt-monitor-client-conntrack.sh"
grep -q 'openwrt_wifi_assoc_events_total{ap=".*",ssid="Home-5G",event="connected"} 1' "$OUT" || { echo 'FAIL: log-ring event was counted twice'; exit 1; }

# A failed conntrack command must replace prior data with availability only.
cat > "$WORK/bin/conntrack" <<'EOF'
#!/bin/sh
exit 1
EOF
chmod +x "$WORK/bin/conntrack"
PATH="$WORK/bin:$PATH" OPENWRT_MONITOR_TEXTFILE_DIR="$WORK/out" \
  OPENWRT_MONITOR_JSHN_PATH="$WORK/libubox/jshn.sh" \
  sh "$ROOT/openwrt/scripts/openwrt-monitor-client-conntrack.sh"
grep -q '^openwrt_client_conntrack_collector_available 0$' "$OUT" || { echo 'FAIL: conntrack failure did not fail closed'; exit 1; }
! grep -q '^openwrt_client_conntrack_entries{' "$OUT" || { echo 'FAIL: failed run retained conntrack values'; exit 1; }

echo 'PASS: tests/test_client_conntrack.sh'
