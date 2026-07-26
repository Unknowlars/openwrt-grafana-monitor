#!/bin/sh

set -e

# Overridable so the collector logic can be exercised in tests.
OUTDIR="${OPENWRT_MONITOR_TEXTFILE_DIR:-/var/prometheus}"
OUTFILE="$OUTDIR/openwrt_sqm.prom"
# The textfile collector reads every file in $OUTDIR, so a temp file left
# there by a crashed run is scraped as a second copy of every metric below.
# Stage outside $OUTDIR (same filesystem on OpenWrt: /var -> /tmp) and mv
# atomically into place.
TMPFILE="/tmp/.openwrt-monitor-openwrt_sqm.$$"

mkdir -p "$OUTDIR"
rm -f "$OUTFILE".[0-9]*
trap 'rm -f "$TMPFILE"' EXIT

wan_dev="wan"

if [ -f /lib/functions/network.sh ]; then
  . /lib/functions/network.sh
  wan_network=""
  network_find_wan wan_network 2>/dev/null || true
  if [ -n "$wan_network" ]; then
    network_get_device wan_dev "$wan_network" 2>/dev/null || true
  fi
fi

[ -n "$wan_dev" ] || wan_dev="wan"

{
  printf '# HELP openwrt_tc_available Whether tc is available for qdisc statistics collection.\n'
  printf '# TYPE openwrt_tc_available gauge\n'
  printf '# HELP openwrt_tc_qdisc_sent_bytes_total Total bytes sent through qdisc.\n'
  printf '# TYPE openwrt_tc_qdisc_sent_bytes_total counter\n'
  printf '# HELP openwrt_tc_qdisc_sent_packets_total Total packets sent through qdisc.\n'
  printf '# TYPE openwrt_tc_qdisc_sent_packets_total counter\n'
  printf '# HELP openwrt_tc_qdisc_drops_total Total qdisc packet drops.\n'
  printf '# TYPE openwrt_tc_qdisc_drops_total counter\n'
  printf '# HELP openwrt_tc_qdisc_overlimits_total Total qdisc overlimit events.\n'
  printf '# TYPE openwrt_tc_qdisc_overlimits_total counter\n'
  printf '# HELP openwrt_tc_qdisc_requeues_total Total qdisc requeue events.\n'
  printf '# TYPE openwrt_tc_qdisc_requeues_total counter\n'
  printf '# HELP openwrt_tc_qdisc_backlog_bytes Current qdisc backlog in bytes.\n'
  printf '# TYPE openwrt_tc_qdisc_backlog_bytes gauge\n'
  printf '# HELP openwrt_tc_qdisc_backlog_packets Current qdisc backlog in packets.\n'
  printf '# TYPE openwrt_tc_qdisc_backlog_packets gauge\n'
  printf '# HELP openwrt_tc_wan_device_info Label-only metric for selected WAN device.\n'
  printf '# TYPE openwrt_tc_wan_device_info gauge\n'
  printf '# HELP sqm_backlog_bytes SQM/cake qdisc backlog bytes by interface. Compatibility alias for older dashboards.\n'
  printf '# TYPE sqm_backlog_bytes gauge\n'
  printf '# HELP sqm_dropped_packets_total SQM/cake qdisc dropped packets by interface. Compatibility alias for older dashboards.\n'
  printf '# TYPE sqm_dropped_packets_total counter\n'
  printf '# HELP sqm_overlimits_total SQM/cake qdisc overlimits by interface. Compatibility alias for older dashboards.\n'
  printf '# TYPE sqm_overlimits_total counter\n'

  if command -v tc >/dev/null 2>&1; then
    printf 'openwrt_tc_available 1\n'
    printf 'openwrt_tc_wan_device_info{device="%s"} 1\n' "$wan_dev"

    tc -s qdisc show dev "$wan_dev" 2>/dev/null | awk -v dev="$wan_dev" '
      # On a multiqueue device the kernel auto-creates one child qdisc per
      # hardware queue, all reported with handle "0:" and distinguished only by
      # their parent. Keying series on the handle alone collapsed every child
      # onto identical labels, so Prometheus kept the first (0) and dropped the
      # queue that carried the real byte counts as a duplicate sample.
      /^qdisc / {
        qtype = $2
        qid = $3
        sub(/:$/, "", qid)

        qparent = "root"
        for (i = 1; i <= NF; i++) {
          if ($i == "parent" && i + 1 <= NF) {
            qparent = $(i + 1)
          }
        }

        direction = (dev ~ /^ifb/) ? "ingress" : "egress"
        next
      }

      /^[[:space:]]*Sent / {
        line = $0
        gsub(/[(),]/, "", line)
        n = split(line, a, " ")

        sent_bytes = 0
        sent_packets = 0
        dropped = 0
        overlimits = 0
        requeues = 0

        for (i = 1; i <= n; i++) {
          if (a[i] == "Sent") {
            sent_bytes = a[i + 1] + 0
            sent_packets = a[i + 3] + 0
          }
          if (a[i] == "dropped") {
            dropped = a[i + 1] + 0
          }
          if (a[i] == "overlimits") {
            overlimits = a[i + 1] + 0
          }
          if (a[i] == "requeues") {
            requeues = a[i + 1] + 0
          }
        }

        printf "openwrt_tc_qdisc_sent_bytes_total{device=\"%s\",qdisc=\"%s\",id=\"%s\",parent=\"%s\"} %.0f\n", dev, qtype, qid, qparent, sent_bytes
        printf "openwrt_tc_qdisc_sent_packets_total{device=\"%s\",qdisc=\"%s\",id=\"%s\",parent=\"%s\"} %.0f\n", dev, qtype, qid, qparent, sent_packets
        printf "openwrt_tc_qdisc_drops_total{device=\"%s\",qdisc=\"%s\",id=\"%s\",parent=\"%s\"} %.0f\n", dev, qtype, qid, qparent, dropped
        printf "openwrt_tc_qdisc_overlimits_total{device=\"%s\",qdisc=\"%s\",id=\"%s\",parent=\"%s\"} %.0f\n", dev, qtype, qid, qparent, overlimits
        printf "openwrt_tc_qdisc_requeues_total{device=\"%s\",qdisc=\"%s\",id=\"%s\",parent=\"%s\"} %.0f\n", dev, qtype, qid, qparent, requeues

        # The sqm_* aliases are per-interface, not per-qdisc. Emitting them
        # inside this block produced one identical series per queue; accumulate
        # the root qdisc only and emit once in END.
        if (qparent == "root") {
          sqm_dropped = dropped
          sqm_overlimits = overlimits
          sqm_seen = 1
        }
        next
      }

      /^[[:space:]]*backlog / {
        line = $0
        n = split(line, a, " ")
        backlog_bytes = 0
        backlog_packets = 0

        for (i = 1; i <= n; i++) {
          if (a[i] == "backlog") {
            backlog_bytes = a[i + 1]
            sub(/b$/, "", backlog_bytes)
            backlog_bytes += 0

            backlog_packets = a[i + 2]
            sub(/p$/, "", backlog_packets)
            backlog_packets += 0
          }
        }

        printf "openwrt_tc_qdisc_backlog_bytes{device=\"%s\",qdisc=\"%s\",id=\"%s\",parent=\"%s\"} %.0f\n", dev, qtype, qid, qparent, backlog_bytes
        printf "openwrt_tc_qdisc_backlog_packets{device=\"%s\",qdisc=\"%s\",id=\"%s\",parent=\"%s\"} %.0f\n", dev, qtype, qid, qparent, backlog_packets

        if (qparent == "root") {
          sqm_backlog = backlog_bytes
          sqm_seen = 1
        }
      }

      END {
        if (sqm_seen) {
          printf "sqm_dropped_packets_total{iface=\"%s\",direction=\"%s\"} %.0f\n", dev, direction, sqm_dropped
          printf "sqm_overlimits_total{iface=\"%s\",direction=\"%s\"} %.0f\n", dev, direction, sqm_overlimits
          printf "sqm_backlog_bytes{iface=\"%s\",direction=\"%s\"} %.0f\n", dev, direction, sqm_backlog
        }
      }
    '
  else
    printf 'openwrt_tc_available 0\n'
  fi
} > "$TMPFILE"

mv "$TMPFILE" "$OUTFILE"
