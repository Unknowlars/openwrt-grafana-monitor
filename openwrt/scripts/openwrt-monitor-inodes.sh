#!/bin/sh

set -e

# Overridable so the collector logic can be exercised in tests.
OUTDIR="${OPENWRT_MONITOR_TEXTFILE_DIR:-/var/prometheus}"
OUTFILE="$OUTDIR/openwrt_inodes.prom"
INODE_MOUNTS="${OPENWRT_MONITOR_INODE_MOUNTS:-/overlay /tmp}"
DF_BIN="${OPENWRT_MONITOR_DF_BIN:-df}"
STAT_BIN="${OPENWRT_MONITOR_STAT_BIN:-stat}"
# The textfile collector reads every file in $OUTDIR, so a temp file left
# there by a crashed run is scraped as a second copy of every metric below.
# Stage outside $OUTDIR (same filesystem on OpenWrt: /var -> /tmp) and mv
# atomically into place.
TMPFILE="/tmp/.openwrt-monitor-openwrt_inodes.$$"
INODESFILE="/tmp/.openwrt-monitor-openwrt_inodes-rows.$$"

mkdir -p "$OUTDIR"
rm -f "$OUTFILE".[0-9]*
trap 'rm -f "$TMPFILE" "$INODESFILE"' EXIT

emit_mount_df() {
  mount="$1"
  "$DF_BIN" -iP "$mount" 2>/dev/null | awk -v mount="$mount" '
    NR == 2 {
      gsub(/%/, "", $5)
      printf "openwrt_filesystem_inodes_total{mount=\"%s\"} %.0f\n", mount, $2
      printf "openwrt_filesystem_inodes_used{mount=\"%s\"} %.0f\n", mount, $3
      printf "openwrt_filesystem_inodes_free{mount=\"%s\"} %.0f\n", mount, $4
      printf "openwrt_filesystem_inode_used_percent{mount=\"%s\"} %s\n", mount, $5
      found = 1
    }
    END { exit found ? 0 : 1 }
  '
}

emit_mount_stat() {
  mount="$1"
  command -v "$STAT_BIN" >/dev/null 2>&1 || return 1
  set -- $("$STAT_BIN" -f -c '%c %d' "$mount" 2>/dev/null) || return 1
  total="${1:-}"
  free="${2:-}"
  case "$total:$free" in
    *[!0-9:]*|:|*:|"") return 1 ;;
  esac
  [ "$total" -gt 0 ] 2>/dev/null || return 1
  used=$((total - free))
  [ "$used" -ge 0 ] 2>/dev/null || return 1
  percent=$(awk -v used="$used" -v total="$total" 'BEGIN { printf "%.2f", (used / total) * 100 }')
  printf 'openwrt_filesystem_inodes_total{mount="%s"} %s\n' "$mount" "$total"
  printf 'openwrt_filesystem_inodes_used{mount="%s"} %s\n' "$mount" "$used"
  printf 'openwrt_filesystem_inodes_free{mount="%s"} %s\n' "$mount" "$free"
  printf 'openwrt_filesystem_inode_used_percent{mount="%s"} %s\n' "$mount" "$percent"
}

emit_inodes() {
  emitted=0
  for mount in $INODE_MOUNTS; do
    [ -e "$mount" ] || continue
    if emit_mount_df "$mount" || emit_mount_stat "$mount"; then
      emitted=1
    fi
  done
  [ "$emitted" = 1 ]
}

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

  if emit_inodes > "$INODESFILE"; then
    printf 'openwrt_filesystem_inode_collector_available 1\n'
    cat "$INODESFILE"
  else
    printf 'openwrt_filesystem_inode_collector_available 0\n'
  fi
} > "$TMPFILE"

mv "$TMPFILE" "$OUTFILE"
