#!/bin/sh
# Fixture test for the nlbwmon textfile collector. The jshn shim lets the
# OpenWrt-specific parser contract run under the local POSIX shell using jq.

set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

mkdir -p "$WORK/bin" "$WORK/out" "$WORK/libubox"

cat > "$WORK/bin/nlbw" <<EOF
#!/bin/sh
cat "$ROOT/tests/fixtures/nlbw.json"
EOF
chmod +x "$WORK/bin/nlbw"

cat > "$WORK/libubox/jshn.sh" <<'EOF'
json_init() { :; }
json_load() { JSHN_JSON=$1; JSHN_PATH=''; JSHN_STACK=''; }
json_select() {
  case "$1" in
    ..)
      JSHN_PATH=${JSHN_STACK##*|}
      JSHN_STACK=${JSHN_STACK%|*}
      ;;
    *)
      JSHN_STACK="$JSHN_STACK|$JSHN_PATH"
      case "$1" in
        *[!0-9]*) JSHN_PATH="$JSHN_PATH.$1" ;;
        *) JSHN_PATH="$JSHN_PATH[$(( $1 - 1 ))]" ;;
      esac
      ;;
  esac
}
json_get_var() { eval "$1=\$(printf '%s' \"\$JSHN_JSON\" | jq -r \"$JSHN_PATH[$(( $2 - 1 ))] // empty\")"; }
json_get_keys() { eval "$1=\$(printf '%s' \"\$JSHN_JSON\" | jq -r \"$JSHN_PATH | keys[] + 1\")"; }
EOF

PATH="$WORK/bin:$PATH" OPENWRT_MONITOR_TEXTFILE_DIR="$WORK/out" \
  OPENWRT_MONITOR_JSHN_PATH="$WORK/libubox/jshn.sh" \
  sh "$ROOT/openwrt/scripts/openwrt-monitor-client-traffic.sh"

OUT="$WORK/out/openwrt_client_traffic.prom"
[ -f "$OUT" ] || { echo "FAIL: no output written"; exit 1; }
python3 "$ROOT/tests/check_exposition.py" "$OUT" || exit 1

grep -q 'openwrt_client_traffic_collector_available 1' "$OUT" || { echo "FAIL: collector unavailable"; exit 1; }
grep -q 'openwrt_client_bytes_total{mac="aa:bb:cc:dd:ee:ff",direction="in",service="https"} 300' "$OUT" || { echo "FAIL: IPv4/IPv6 HTTPS RX was not aggregated"; exit 1; }
grep -q 'openwrt_client_connections_total{mac="aa:bb:cc:dd:ee:ff",service="https"} 5' "$OUT" || { echo "FAIL: HTTPS connections were not aggregated"; exit 1; }
grep -q 'openwrt_client_bytes_total{mac="11:22:33:44:55:66",direction="out",service="other"} 9' "$OUT" || { echo "FAIL: unknown protocol was not bucketed as other"; exit 1; }
# A layer7 name outside the trimmed whitelist (e.g. nlbwmon's own built-in
# BitTorrent classification, never in our ~10-bucket protocol file) must
# bucket to "other", not abort the whole scrape. This failure must never zero
# all traffic for a router.
grep -q 'openwrt_client_bytes_total{mac="77:88:99:aa:bb:cc",direction="in",service="other"} 500' "$OUT" || { echo "FAIL: unmapped protocol name did not bucket as other"; exit 1; }
grep -q 'openwrt_client_traffic_collector_available 1' "$OUT" || { echo "FAIL: unmapped protocol name incorrectly failed the whole scrape closed"; exit 1; }

# A legitimately empty result set (fresh install, accounting-period rollover,
# just after `nlbw -c commit`) must still write output. The awk must not
# input file, the script aborted under `set -e` before the mv, and the previous
# period's .prom stayed in place still reporting available 1 -- stale data
# indistinguishable from healthy data. The honest result is available 1 with no
# per-client series: an empty period is not a collector failure.
printf '%s\n' '{"columns":["family","proto","port","mac","ip","conns","rx_bytes","rx_pkts","tx_bytes","tx_pkts","layer7"],"data":[]}' > "$WORK/empty.json"
cat > "$WORK/bin/nlbw" <<EOF
#!/bin/sh
cat "$WORK/empty.json"
EOF
chmod +x "$WORK/bin/nlbw"
# Seed the previous accounting period's output so we can prove it is replaced.
printf 'openwrt_client_bytes_total{mac="de:ad:be:ef:00:01",direction="in",service="https"} 12345\n' > "$OUT"
PATH="$WORK/bin:$PATH" OPENWRT_MONITOR_TEXTFILE_DIR="$WORK/out" \
  OPENWRT_MONITOR_JSHN_PATH="$WORK/libubox/jshn.sh" \
  sh "$ROOT/openwrt/scripts/openwrt-monitor-client-traffic.sh" \
  || { echo "FAIL: empty result set aborted instead of writing output"; exit 1; }
[ -f "$OUT" ] || { echo "FAIL: empty result set wrote no output"; exit 1; }
python3 "$ROOT/tests/check_exposition.py" "$OUT" || exit 1
grep -q '^openwrt_client_traffic_collector_available 1$' "$OUT" || { echo "FAIL: empty period did not report available 1"; exit 1; }
! grep -q '^openwrt_client_bytes_total{' "$OUT" || { echo "FAIL: empty period emitted per-client series"; exit 1; }
! grep -q 'de:ad:be:ef:00:01' "$OUT" || { echo "FAIL: previous period's stale series survived an empty run"; exit 1; }

# The collector must refuse a schema change rather than exposing positional
# values under the wrong names, and must replace any old traffic with only 0.
printf '%s\n' '{"columns":["wrong"],"data":[]}' > "$WORK/bad.json"
cat > "$WORK/bin/nlbw" <<EOF
#!/bin/sh
cat "$WORK/bad.json"
EOF
chmod +x "$WORK/bin/nlbw"
PATH="$WORK/bin:$PATH" OPENWRT_MONITOR_TEXTFILE_DIR="$WORK/out" \
  OPENWRT_MONITOR_JSHN_PATH="$WORK/libubox/jshn.sh" \
  sh "$ROOT/openwrt/scripts/openwrt-monitor-client-traffic.sh"
grep -q '^openwrt_client_traffic_collector_available 0$' "$OUT" || { echo "FAIL: schema mismatch did not fail closed"; exit 1; }
! grep -q '^openwrt_client_bytes_total{' "$OUT" || { echo "FAIL: failed run retained partial traffic"; exit 1; }

leaked=$(find "$WORK/out" -type f ! -name '*.prom' | wc -l)
[ "$leaked" = "0" ] || { echo "FAIL: $leaked temp file(s) leaked into scrape dir"; exit 1; }

echo "PASS: tests/test_client_traffic.sh"
