#!/bin/sh
# Regression test for the multiqueue tc collector.
#
# A multiqueue WAN device reports every child fq_codel qdisc with handle "0:",
# distinguished only by its parent. Keying the series on the handle alone made
# all children collide, so Prometheus kept the first (0 bytes) and dropped the
# queue carrying the real counters — the dashboard showed an idle WAN.

set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

mkdir -p "$WORK/bin" "$WORK/out"

cat > "$WORK/bin/tc" <<EOF
#!/bin/sh
cat "$ROOT/tests/fixtures/tc-qdisc-mq-wan.txt"
EOF
chmod +x "$WORK/bin/tc"

PATH="$WORK/bin:$PATH" OPENWRT_MONITOR_TEXTFILE_DIR="$WORK/out" \
  sh "$ROOT/openwrt/scripts/openwrt-monitor-sqm.sh"

OUT="$WORK/out/openwrt_sqm.prom"
[ -f "$OUT" ] || { echo "FAIL: no output written"; exit 1; }

python3 "$ROOT/tests/check_exposition.py" "$OUT" || exit 1

# The busy queue's counters must survive, not be masked by an idle sibling.
grep -q 'openwrt_tc_qdisc_sent_bytes_total{device="wan",qdisc="fq_codel",id="0",parent=":2"} 703553273' "$OUT" || {
  echo "FAIL: busy child qdisc counters missing"; grep openwrt_tc_qdisc_sent_bytes "$OUT"; exit 1; }

# The per-interface aliases must be emitted exactly once, from the root qdisc.
for alias in sqm_dropped_packets_total sqm_overlimits_total sqm_backlog_bytes; do
  count=$(grep -c "^$alias{" "$OUT" || true)
  [ "$count" = "1" ] || { echo "FAIL: $alias emitted $count times, expected 1"; exit 1; }
done

# Temp files must never be left in the scraped directory.
leaked=$(find "$WORK/out" -type f ! -name '*.prom' | wc -l)
[ "$leaked" = "0" ] || { echo "FAIL: $leaked temp file(s) leaked into scrape dir"; exit 1; }

echo "PASS: tests/test_sqm_collector.sh"
