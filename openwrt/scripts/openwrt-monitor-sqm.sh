#!/bin/sh

set -e

OUTDIR="/var/prometheus"
OUTFILE="$OUTDIR/openwrt_sqm.prom"
TMPFILE="$OUTFILE.$$"

mkdir -p "$OUTDIR"

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

  if command -v tc >/dev/null 2>&1; then
    printf 'openwrt_tc_available 1\n'
    printf 'openwrt_tc_wan_device_info{device="%s"} 1\n' "$wan_dev"

    tc -s qdisc show dev "$wan_dev" 2>/dev/null | awk -v dev="$wan_dev" '
      /^qdisc / {
        qtype = $2
        qid = $3
        sub(/:$/, "", qid)
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

        printf "openwrt_tc_qdisc_sent_bytes_total{device=\"%s\",qdisc=\"%s\",id=\"%s\"} %.0f\n", dev, qtype, qid, sent_bytes
        printf "openwrt_tc_qdisc_sent_packets_total{device=\"%s\",qdisc=\"%s\",id=\"%s\"} %.0f\n", dev, qtype, qid, sent_packets
        printf "openwrt_tc_qdisc_drops_total{device=\"%s\",qdisc=\"%s\",id=\"%s\"} %.0f\n", dev, qtype, qid, dropped
        printf "openwrt_tc_qdisc_overlimits_total{device=\"%s\",qdisc=\"%s\",id=\"%s\"} %.0f\n", dev, qtype, qid, overlimits
        printf "openwrt_tc_qdisc_requeues_total{device=\"%s\",qdisc=\"%s\",id=\"%s\"} %.0f\n", dev, qtype, qid, requeues
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

        printf "openwrt_tc_qdisc_backlog_bytes{device=\"%s\",qdisc=\"%s\",id=\"%s\"} %.0f\n", dev, qtype, qid, backlog_bytes
        printf "openwrt_tc_qdisc_backlog_packets{device=\"%s\",qdisc=\"%s\",id=\"%s\"} %.0f\n", dev, qtype, qid, backlog_packets
      }
    '
  else
    printf 'openwrt_tc_available 0\n'
  fi
} > "$TMPFILE"

mv "$TMPFILE" "$OUTFILE"
