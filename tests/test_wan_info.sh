#!/bin/sh
# Fixture test for the WAN info helper. Stubs uci/wget so staging and the
# textfile-dir cleanliness contract can be exercised offline (R9).
#
# After a run the textfile dir must contain exactly the .prom file and no
# staged .prom.<pid> leftovers. A seeded pre-fix leftover must be swept.

set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

mkdir -p "$WORK/bin" "$WORK/out"

cat > "$WORK/bin/uci" <<'EOF'
#!/bin/sh
printf 'test-router\n'
EOF

cat > "$WORK/bin/wget" <<'EOF'
#!/bin/sh
printf '203.0.113.10\n'
EOF

chmod +x "$WORK/bin/uci" "$WORK/bin/wget"

# Seed a leftover staged file from the pre-fix version. The collector must sweep
# it, otherwise routers already running the old code keep a permanent leftover.
printf 'wan_public_ip_changed 0\n' > "$WORK/out/openwrt_wan_info.prom.4242"

PATH="$WORK/bin:$PATH" OPENWRT_MONITOR_TEXTFILE_DIR="$WORK/out" \
  sh "$ROOT/openwrt/scripts/openwrt-monitor-wan-info.sh"

OUT="$WORK/out/openwrt_wan_info.prom"
[ -f "$OUT" ] || { echo "FAIL: no output written"; exit 1; }

python3 "$ROOT/tests/check_exposition.py" "$WORK/out"/*.prom* || exit 1

leaked=$(find "$WORK/out" -type f ! -name '*.prom' | wc -l)
[ "$leaked" = "0" ] || {
  echo "FAIL: $leaked temp/leftover file(s) present in scrape dir:"
  find "$WORK/out" -type f ! -name '*.prom'
  exit 1
}

grep -q '^wan_public_ip_changed [01]$' "$OUT" \
  || { echo "FAIL: wan_public_ip_changed missing or malformed"; exit 1; }

# Helper also stages /tmp/wanip.out for wan_info.lua; confirm shape without
# asserting host-global uniqueness of that path.
[ -f /tmp/wanip.out ] || { echo "FAIL: /tmp/wanip.out not written"; exit 1; }
grep -q 'publicip=203.0.113.10' /tmp/wanip.out \
  || { echo "FAIL: public IP missing from /tmp/wanip.out"; exit 1; }
grep -q 'hostname=test-router' /tmp/wanip.out \
  || { echo "FAIL: hostname missing from /tmp/wanip.out"; exit 1; }

echo "PASS: tests/test_wan_info.sh"
