#!/bin/sh
# Fixture test for the NetFlow exporter health collector.
#
# The two counters that matter here are the ones that make flow data quietly
# wrong rather than obviously broken: forced flow expiry (flow table full, byte
# counts truncated) and libpcap drops (router could not keep up). Both are
# buried inside parenthesised sub-fields of softflowctl's output, so the awk
# field arithmetic is what this actually pins down.

set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

mkdir -p "$WORK/bin" "$WORK/out" "$WORK/run" "$WORK/sys/wan0" "$WORK/sys/br-lan"

printf '7\n' > "$WORK/sys/wan0/ifindex"
printf '11\n' > "$WORK/sys/br-lan/ifindex"

# Stand-in for the running softflowd: replies for wan0's control socket only.
cat > "$WORK/bin/softflowctl" <<EOF
#!/bin/sh
# Usage as called by the collector: softflowctl -c <socket> statistics
case "\$2" in
  */softflowd-wan0.ctl) cat "$ROOT/tests/fixtures/softflowctl-statistics.txt" ;;
  *) exit 1 ;;
esac
EOF
chmod +x "$WORK/bin/softflowctl"

touch "$WORK/run/softflowd-wan0.ctl"
# Deliberately no socket for br-lan: a configured-but-dead exporter.

OUT="$WORK/out/openwrt_netflow.prom"

run_collector() {
  PATH="$WORK/bin:$PATH" \
  OPENWRT_MONITOR_TEXTFILE_DIR="$WORK/out" \
  OPENWRT_MONITOR_SOFTFLOWD_RUN_DIR="$WORK/run" \
  OPENWRT_MONITOR_SYSFS_NET="$WORK/sys" \
  NETFLOW_INTERFACES="$1" \
  sh "$ROOT/openwrt/scripts/openwrt-monitor-netflow-health.sh"
}

# ── One live exporter, one dead one ──────────────────────────────────────────

run_collector "wan0 br-lan"

[ -f "$OUT" ] || { echo "FAIL: no netflow health output written"; exit 1; }
python3 "$ROOT/tests/check_exposition.py" "$OUT" || exit 1

grep -q '^openwrt_netflow_exporter_up{interface="wan0"} 1$' "$OUT" \
  || { echo "FAIL: live exporter not reported up"; exit 1; }
grep -q '^openwrt_netflow_exporter_up{interface="br-lan"} 0$' "$OUT" \
  || { echo "FAIL: dead exporter not reported down"; exit 1; }
grep -q '^openwrt_netflow_collector_available 1$' "$OUT" \
  || { echo "FAIL: collector not marked available with one exporter up"; exit 1; }

# ifIndex is what Akvorado matches against akvorado/exporters.yaml; a wrong or
# missing value silently discards every flow, so it must be exported for a
# configured interface even when its exporter is down.
grep -q '^openwrt_netflow_ifindex{interface="wan0"} 7$' "$OUT" \
  || { echo "FAIL: wan0 ifindex missing"; exit 1; }
grep -q '^openwrt_netflow_ifindex{interface="br-lan"} 11$' "$OUT" \
  || { echo "FAIL: br-lan ifindex missing for a down exporter"; exit 1; }

grep -q '^openwrt_netflow_active_flows{interface="wan0"} 412$' "$OUT" \
  || { echo "FAIL: active flow count missing"; exit 1; }
grep -q '^openwrt_netflow_packets_processed_total{interface="wan0"} 9182734$' "$OUT" \
  || { echo "FAIL: processed packet count missing"; exit 1; }
grep -q '^openwrt_netflow_flows_expired_total{interface="wan0"} 88213$' "$OUT" \
  || { echo "FAIL: expired flow count missing"; exit 1; }

# "Flows expired: 88213 (137 forced)" -- 137, not 88213, and without the paren.
grep -q '^openwrt_netflow_flows_force_expired_total{interface="wan0"} 137$' "$OUT" \
  || { echo "FAIL: forced-expiry count not parsed out of the parenthesised field"; exit 1; }

grep -q '^openwrt_netflow_flows_exported_total{interface="wan0"} 88104$' "$OUT" \
  || { echo "FAIL: exported flow count missing"; exit 1; }

# "Flows exported: 88104 (91220 records) in 7318 packets (9 failures)" -- the
# failure count is the second-to-last field, not the record or packet count.
grep -q '^openwrt_netflow_export_failures_total{interface="wan0"} 9$' "$OUT" \
  || { echo "FAIL: export failure count not parsed from the trailing field"; exit 1; }

grep -q '^openwrt_netflow_pcap_packets_received_total{interface="wan0"} 9187890$' "$OUT" \
  || { echo "FAIL: libpcap received count missing"; exit 1; }
grep -q '^openwrt_netflow_pcap_packets_dropped_total{interface="wan0"} 231$' "$OUT" \
  || { echo "FAIL: libpcap drop count missing"; exit 1; }

# A down exporter must not carry over the live one's statistics.
! grep -q '^openwrt_netflow_active_flows{interface="br-lan"}' "$OUT" \
  || { echo "FAIL: dead exporter emitted flow statistics"; exit 1; }

# ── Every exporter down ──────────────────────────────────────────────────────

run_collector "br-lan"
python3 "$ROOT/tests/check_exposition.py" "$OUT" || exit 1
grep -q '^openwrt_netflow_collector_available 0$' "$OUT" \
  || { echo "FAIL: all-exporters-down did not fail closed"; exit 1; }

# ── softflowctl absent entirely ──────────────────────────────────────────────

rm -f "$WORK/bin/softflowctl"
run_collector "wan0"
python3 "$ROOT/tests/check_exposition.py" "$OUT" || exit 1
grep -q '^openwrt_netflow_collector_available 0$' "$OUT" \
  || { echo "FAIL: missing softflowctl did not fail closed"; exit 1; }
grep -q '^openwrt_netflow_exporter_up{interface="wan0"} 0$' "$OUT" \
  || { echo "FAIL: missing softflowctl did not report the exporter down"; exit 1; }
! grep -q '^openwrt_netflow_packets_processed_total{' "$OUT" \
  || { echo "FAIL: stale statistics retained after softflowctl vanished"; exit 1; }

# ── Profile not enabled: no configured interfaces ────────────────────────────

run_collector ""
python3 "$ROOT/tests/check_exposition.py" "$OUT" || exit 1
grep -q '^openwrt_netflow_collector_available 0$' "$OUT" \
  || { echo "FAIL: unconfigured netflow did not fail closed"; exit 1; }
! grep -q '^openwrt_netflow_exporter_up{' "$OUT" \
  || { echo "FAIL: unconfigured netflow invented an exporter series"; exit 1; }

echo "PASS: tests/test_netflow_health.sh"
