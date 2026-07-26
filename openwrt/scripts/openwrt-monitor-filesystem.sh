#!/bin/sh

set -e

# Overridable so the collector logic can be exercised in tests.
OUTDIR="${OPENWRT_MONITOR_TEXTFILE_DIR:-/var/prometheus}"
OUTFILE="$OUTDIR/openwrt_filesystem.prom"
# The textfile collector reads every file in $OUTDIR, so a temp file left
# there by a crashed run is scraped as a second copy of every metric below.
# Stage outside $OUTDIR (same filesystem on OpenWrt: /var -> /tmp) and mv
# atomically into place.
TMPFILE="/tmp/.openwrt-monitor-openwrt_filesystem.$$"

mkdir -p "$OUTDIR"
rm -f "$OUTFILE".[0-9]*
trap 'rm -f "$TMPFILE"' EXIT

{
  printf '# HELP openwrt_filesystem_size_bytes Filesystem size in bytes.\n'
  printf '# TYPE openwrt_filesystem_size_bytes gauge\n'
  printf '# HELP openwrt_filesystem_used_bytes Filesystem used space in bytes.\n'
  printf '# TYPE openwrt_filesystem_used_bytes gauge\n'
  printf '# HELP openwrt_filesystem_avail_bytes Filesystem available space in bytes.\n'
  printf '# TYPE openwrt_filesystem_avail_bytes gauge\n'
  printf '# HELP openwrt_filesystem_used_percent Filesystem space used percentage.\n'
  printf '# TYPE openwrt_filesystem_used_percent gauge\n'
  printf '# HELP overlay_bytes_total Total overlay rootfs_data filesystem size in bytes. Compatibility alias for older dashboards.\n'
  printf '# TYPE overlay_bytes_total gauge\n'
  printf '# HELP overlay_bytes_used Used overlay rootfs_data filesystem bytes. Compatibility alias for older dashboards.\n'
  printf '# TYPE overlay_bytes_used gauge\n'

  for mount in /overlay /tmp; do
    [ -e "$mount" ] || continue

    df -kP "$mount" | awk -v mount="$mount" '
      NR == 2 {
        gsub(/%/, "", $5)
        printf "openwrt_filesystem_size_bytes{mount=\"%s\"} %.0f\n", mount, $2 * 1024
        printf "openwrt_filesystem_used_bytes{mount=\"%s\"} %.0f\n", mount, $3 * 1024
        printf "openwrt_filesystem_avail_bytes{mount=\"%s\"} %.0f\n", mount, $4 * 1024
        printf "openwrt_filesystem_used_percent{mount=\"%s\"} %s\n", mount, $5
        if (mount == "/overlay") {
          printf "overlay_bytes_total %.0f\n", $2 * 1024
          printf "overlay_bytes_used %.0f\n", $3 * 1024
        }
      }
    '
  done
} > "$TMPFILE"

mv "$TMPFILE" "$OUTFILE"
