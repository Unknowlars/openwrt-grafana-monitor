#!/bin/sh

set -e

OUTDIR="/var/prometheus"
OUTFILE="$OUTDIR/openwrt_softnet.prom"
TMPFILE="$OUTFILE.$$"

mkdir -p "$OUTDIR"

listen_drops=0
listen_overflows=0

if [ -f /proc/net/netstat ]; then
  set -- $(awk '
    $1 == "TcpExt:" && !seen_header {
      for (i = 2; i <= NF; i++) {
        idx[$i] = i
      }
      seen_header = 1
      next
    }
    $1 == "TcpExt:" && seen_header {
      ld = 0
      lo = 0
      if (idx["ListenDrops"] > 0) {
        ld = $(idx["ListenDrops"])
      }
      if (idx["ListenOverflows"] > 0) {
        lo = $(idx["ListenOverflows"])
      }
      print ld + 0, lo + 0
      exit
    }
  ' /proc/net/netstat)

  if [ "$#" -ge 2 ]; then
    listen_drops="$1"
    listen_overflows="$2"
  fi
fi

{
  printf '# HELP openwrt_softnet_processed_total Packets processed by softnet per CPU.\n'
  printf '# TYPE openwrt_softnet_processed_total counter\n'
  printf '# HELP openwrt_softnet_dropped_total Packets dropped by softnet per CPU.\n'
  printf '# TYPE openwrt_softnet_dropped_total counter\n'
  printf '# HELP openwrt_softnet_times_squeezed_total Number of times softnet budget was exhausted per CPU.\n'
  printf '# TYPE openwrt_softnet_times_squeezed_total counter\n'
  printf '# HELP openwrt_tcp_listen_drops_total Dropped TCP connections in LISTEN state.\n'
  printf '# TYPE openwrt_tcp_listen_drops_total counter\n'
  printf '# HELP openwrt_tcp_listen_overflows_total Overflowed TCP listen queue events.\n'
  printf '# TYPE openwrt_tcp_listen_overflows_total counter\n'

  cpu=0
  if [ -f /proc/net/softnet_stat ]; then
    while read -r line; do
      set -- $line
      [ -n "$1" ] || continue

      processed=$((0x$1))
      dropped=$((0x$2))
      squeezed=$((0x$3))

      printf 'openwrt_softnet_processed_total{cpu="%s"} %s\n' "$cpu" "$processed"
      printf 'openwrt_softnet_dropped_total{cpu="%s"} %s\n' "$cpu" "$dropped"
      printf 'openwrt_softnet_times_squeezed_total{cpu="%s"} %s\n' "$cpu" "$squeezed"

      cpu=$((cpu + 1))
    done < /proc/net/softnet_stat
  fi

  printf 'openwrt_tcp_listen_drops_total %s\n' "$listen_drops"
  printf 'openwrt_tcp_listen_overflows_total %s\n' "$listen_overflows"
} > "$TMPFILE"

mv "$TMPFILE" "$OUTFILE"
