#!/bin/sh

set -e

OUTDIR="/var/prometheus"
OUTFILE="$OUTDIR/openwrt_firewall.prom"
TMPFILE="$OUTFILE.$$"

mkdir -p "$OUTDIR"

collect_iptables() {
  cmd="$1"
  family="$2"
  table="$3"

  "$cmd" -t "$table" -L -v -x -n 2>/dev/null | awk -v family="$family" -v table="$table" '
    /^Chain / {
      chain = $2
      next
    }
    /^[[:space:]]*[0-9]+[[:space:]]+[0-9]+/ {
      if (chain == "") {
        next
      }

      packets = $1 + 0
      bytes = $2 + 0
      target = $3

      chain_packets[chain] += packets
      chain_bytes[chain] += bytes

      if (target == "DROP" || target == "REJECT") {
        drop_packets[chain, target] += packets
        drop_bytes[chain, target] += bytes
      }
    }
    END {
      for (c in chain_packets) {
        printf "openwrt_firewall_chain_packets_total{family=\"%s\",table=\"%s\",chain=\"%s\"} %.0f\n", family, table, c, chain_packets[c]
        printf "openwrt_firewall_chain_bytes_total{family=\"%s\",table=\"%s\",chain=\"%s\"} %.0f\n", family, table, c, chain_bytes[c]
      }

      for (k in drop_packets) {
        split(k, parts, SUBSEP)
        c = parts[1]
        t = parts[2]
        printf "openwrt_firewall_drop_packets_total{family=\"%s\",table=\"%s\",chain=\"%s\",target=\"%s\"} %.0f\n", family, table, c, t, drop_packets[k]
        printf "openwrt_firewall_drop_bytes_total{family=\"%s\",table=\"%s\",chain=\"%s\",target=\"%s\"} %.0f\n", family, table, c, t, drop_bytes[k]
      }
    }
  '
}

{
  printf '# HELP openwrt_firewall_collector_available Whether firewall counter collection is available on this router.\n'
  printf '# TYPE openwrt_firewall_collector_available gauge\n'
  printf '# HELP openwrt_firewall_chain_packets_total Packet counters by firewall chain.\n'
  printf '# TYPE openwrt_firewall_chain_packets_total counter\n'
  printf '# HELP openwrt_firewall_chain_bytes_total Byte counters by firewall chain.\n'
  printf '# TYPE openwrt_firewall_chain_bytes_total counter\n'
  printf '# HELP openwrt_firewall_drop_packets_total DROP/REJECT packet counters by firewall chain.\n'
  printf '# TYPE openwrt_firewall_drop_packets_total counter\n'
  printf '# HELP openwrt_firewall_drop_bytes_total DROP/REJECT byte counters by firewall chain.\n'
  printf '# TYPE openwrt_firewall_drop_bytes_total counter\n'

  available=0

  if command -v iptables >/dev/null 2>&1; then
    available=1
    for table in filter nat mangle raw; do
      collect_iptables iptables ipv4 "$table"
    done
  fi

  if command -v ip6tables >/dev/null 2>&1; then
    available=1
    for table in filter nat mangle raw; do
      collect_iptables ip6tables ipv6 "$table"
    done
  fi

  printf 'openwrt_firewall_collector_available %s\n' "$available"
} > "$TMPFILE"

mv "$TMPFILE" "$OUTFILE"
