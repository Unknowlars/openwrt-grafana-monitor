#!/bin/sh

set -e

OUTDIR="/var/prometheus"
OUTFILE="$OUTDIR/openwrt_inodes.prom"
TMPFILE="$OUTFILE.$$"

mkdir -p "$OUTDIR"

{
  printf '# HELP openwrt_filesystem_inode_collector_available Whether inode collection is available on this router (1 yes, 0 no).\n'
  printf '# TYPE openwrt_filesystem_inode_collector_available gauge\n'
  printf '# HELP openwrt_filesystem_inodes_total Total inodes for filesystem mount.\n'
  printf '# TYPE openwrt_filesystem_inodes_total gauge\n'
  printf '# HELP openwrt_filesystem_inodes_used Used inodes for filesystem mount.\n'
  printf '# TYPE openwrt_filesystem_inodes_used gauge\n'
  printf '# HELP openwrt_filesystem_inodes_free Free inodes for filesystem mount.\n'
  printf '# TYPE openwrt_filesystem_inodes_free gauge\n'
  printf '# HELP openwrt_filesystem_inode_used_percent Used inode percentage for filesystem mount.\n'
  printf '# TYPE openwrt_filesystem_inode_used_percent gauge\n'

  if df -iP / >/dev/null 2>&1; then
    printf 'openwrt_filesystem_inode_collector_available 1\n'

    for mount in /overlay /tmp; do
      [ -e "$mount" ] || continue

      df -iP "$mount" 2>/dev/null | awk -v mount="$mount" '
        NR == 2 {
          gsub(/%/, "", $5)
          printf "openwrt_filesystem_inodes_total{mount=\"%s\"} %.0f\n", mount, $2
          printf "openwrt_filesystem_inodes_used{mount=\"%s\"} %.0f\n", mount, $3
          printf "openwrt_filesystem_inodes_free{mount=\"%s\"} %.0f\n", mount, $4
          printf "openwrt_filesystem_inode_used_percent{mount=\"%s\"} %s\n", mount, $5
        }
      '
    done
  else
    printf 'openwrt_filesystem_inode_collector_available 0\n'
  fi
} > "$TMPFILE"

mv "$TMPFILE" "$OUTFILE"
