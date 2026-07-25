#!/bin/sh
# Fixture test for the WAN quality collector. Stubs ping/ip/nslookup so the
# staging and exposition contract can be exercised offline.
#
# The property under test is the one that matters for R4: after a run the
# textfile dir contains exactly the .prom file and no staged temp copy.
#
# Note the exposure is leftover-file accumulation, not double-scraped metrics.
# Measured on a live router (2026-07-25): the textfile collector globs *.prom
# only, so a staged openwrt_wan_quality.prom.<pid> is never read -- 144
# one-second polls spanning a confirmed collector run found zero duplicated
# series. The duplicate assertion below is kept anyway: it is cheap, it is the
# property we actually want to hold, and it does not depend on the exporter's
# glob pattern staying what it is today.

set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

mkdir -p "$WORK/bin" "$WORK/out"

cat > "$WORK/bin/ip" <<'EOF'
#!/bin/sh
echo "default via 192.168.0.1 dev eth0 proto static"
EOF

cat > "$WORK/bin/ping" <<'EOF'
#!/bin/sh
echo "64 bytes from 192.168.0.1: seq=0 ttl=64 time=1.234 ms"
echo "64 bytes from 192.168.0.1: seq=1 ttl=64 time=2.345 ms"
echo "2 packets transmitted, 2 packets received, 0% packet loss"
EOF

cat > "$WORK/bin/nslookup" <<'EOF'
#!/bin/sh
echo "Name:      openwrt.org"
EOF

chmod +x "$WORK/bin/ip" "$WORK/bin/ping" "$WORK/bin/nslookup"

# Seed a leftover staged file from the pre-fix version. The collector must sweep
# it, otherwise routers already running the old code keep a permanent duplicate.
printf 'openwrt_wan_probe_success{target="gateway",address="192.168.0.1"} 1\n' \
  > "$WORK/out/openwrt_wan_quality.prom.4242"

PATH="$WORK/bin:$PATH" OPENWRT_MONITOR_TEXTFILE_DIR="$WORK/out" \
  sh "$ROOT/openwrt/scripts/openwrt-monitor-wan-quality.sh" 2 1

OUT="$WORK/out/openwrt_wan_quality.prom"
[ -f "$OUT" ] || { echo "FAIL: no output written"; exit 1; }

# The property that actually matters, and it generalizes to R9: no duplicate
# series across the whole textfile dir.
python3 "$ROOT/tests/check_exposition.py" "$WORK/out"/*.prom* || exit 1

leaked=$(find "$WORK/out" -type f ! -name '*.prom' | wc -l)
[ "$leaked" = "0" ] || {
  echo "FAIL: $leaked temp/leftover file(s) present in scrape dir:"
  find "$WORK/out" -type f ! -name '*.prom'
  exit 1
}

grep -q '^openwrt_wan_probe_success{target="gateway",address="192.168.0.1"} 1$' "$OUT" \
  || { echo "FAIL: gateway probe missing"; exit 1; }
grep -q '^gateway_packet_loss{gateway="192.168.0.1"} 0' "$OUT" \
  || { echo "FAIL: gateway_packet_loss compatibility alias missing"; exit 1; }
grep -q '^dns_probe_success{host="openwrt.org"} 1$' "$OUT" \
  || { echo "FAIL: dns probe missing"; exit 1; }

echo "PASS: tests/test_wan_quality.sh"
