#!/bin/sh
# Fixture test for inode collection on BusyBox builds where df lacks -i.

set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

mkdir -p "$WORK/bin" "$WORK/out" "$WORK/mounts/overlay" "$WORK/mounts/tmp"

cat > "$WORK/bin/df" <<'EOF'
#!/bin/sh
exit 1
EOF

cat > "$WORK/bin/stat" <<'EOF'
#!/bin/sh
mount="${4:-}"
case "$mount" in
  */overlay) printf '100 40\n' ;;
  */tmp) printf '200 50\n' ;;
  *) exit 1 ;;
esac
EOF

chmod +x "$WORK/bin/df" "$WORK/bin/stat"

PATH="$WORK/bin:$PATH" \
OPENWRT_MONITOR_TEXTFILE_DIR="$WORK/out" \
OPENWRT_MONITOR_INODE_MOUNTS="$WORK/mounts/overlay $WORK/mounts/tmp" \
sh "$ROOT/openwrt/scripts/openwrt-monitor-inodes.sh"

OUT="$WORK/out/openwrt_inodes.prom"
[ -f "$OUT" ] || { echo "FAIL: no inode output written"; exit 1; }

python3 "$ROOT/tests/check_exposition.py" "$OUT" || exit 1

grep -q "^openwrt_filesystem_inode_collector_available 1$" "$OUT" \
  || { echo "FAIL: stat fallback did not mark inode collector available"; exit 1; }
grep -q "openwrt_filesystem_inodes_total{mount=\"$WORK/mounts/overlay\"} 100" "$OUT" \
  || { echo "FAIL: overlay total inode count missing"; exit 1; }
grep -q "openwrt_filesystem_inodes_used{mount=\"$WORK/mounts/overlay\"} 60" "$OUT" \
  || { echo "FAIL: overlay used inode count missing"; exit 1; }
grep -q "openwrt_filesystem_inode_used_percent{mount=\"$WORK/mounts/tmp\"} 75.00" "$OUT" \
  || { echo "FAIL: tmp inode percentage missing"; exit 1; }

cat > "$WORK/bin/stat" <<'EOF'
#!/bin/sh
exit 1
EOF
chmod +x "$WORK/bin/stat"

PATH="$WORK/bin:$PATH" \
OPENWRT_MONITOR_TEXTFILE_DIR="$WORK/out" \
OPENWRT_MONITOR_INODE_MOUNTS="$WORK/mounts/overlay" \
sh "$ROOT/openwrt/scripts/openwrt-monitor-inodes.sh"

grep -q "^openwrt_filesystem_inode_collector_available 0$" "$OUT" \
  || { echo "FAIL: missing inode sources did not fail closed"; exit 1; }
! grep -q "^openwrt_filesystem_inodes_total{" "$OUT" \
  || { echo "FAIL: failed inode run retained inode values"; exit 1; }

echo "PASS: tests/test_inodes.sh"
