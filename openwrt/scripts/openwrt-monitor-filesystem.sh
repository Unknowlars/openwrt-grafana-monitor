#!/bin/sh

set -e

OUTDIR="/var/prometheus"
OUTFILE="$OUTDIR/openwrt_filesystem.prom"
TMPFILE="$OUTFILE.$$"

mkdir -p "$OUTDIR"

{
  printf '# HELP openwrt_filesystem_size_bytes Filesystem size in bytes.\n'
  printf '# TYPE openwrt_filesystem_size_bytes gauge\n'
  printf '# HELP openwrt_filesystem_used_bytes Filesystem used space in bytes.\n'
  printf '# TYPE openwrt_filesystem_used_bytes gauge\n'
  printf '# HELP openwrt_filesystem_avail_bytes Filesystem available space in bytes.\n'
  printf '# TYPE openwrt_filesystem_avail_bytes gauge\n'
  printf '# HELP openwrt_filesystem_used_percent Filesystem space used percentage.\n'
  printf '# TYPE openwrt_filesystem_used_percent gauge\n'

  for mount in /overlay /tmp; do
    [ -e "$mount" ] || continue

    df -kP "$mount" | awk -v mount="$mount" '
      NR == 2 {
        gsub(/%/, "", $5)
        printf "openwrt_filesystem_size_bytes{mount=\"%s\"} %.0f\n", mount, $2 * 1024
        printf "openwrt_filesystem_used_bytes{mount=\"%s\"} %.0f\n", mount, $3 * 1024
        printf "openwrt_filesystem_avail_bytes{mount=\"%s\"} %.0f\n", mount, $4 * 1024
        printf "openwrt_filesystem_used_percent{mount=\"%s\"} %s\n", mount, $5
      }
    '
  done
} > "$TMPFILE"

mv "$TMPFILE" "$OUTFILE"
