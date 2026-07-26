#!/bin/sh
# Static regression test: setup.sh tolerates an unavailable nlbwmon
# package, so the later clients-profile install block must not unconditionally
# copy into /usr/share/nlbwmon or run /etc/init.d/nlbwmon.

set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
SETUP="$ROOT/openwrt/setup.sh"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

BLOCK="$WORK/clients-block.sh"
awk '
  /^if profile_enabled clients; then$/ {
    clients_block += 1
    if (clients_block == 2) {
      inblock = 1
      depth = 1
      print
      next
    }
  }
  inblock {
    if ($0 ~ /^  if /) depth += 1
    if ($0 ~ /^  fi$/) depth -= 1
    print
    if (depth == 0) exit
  }
' "$SETUP" > "$BLOCK"

[ -s "$BLOCK" ] || {
  echo "FAIL: could not extract the clients profile install block"
  exit 1
}

grep -q '^  if \[ -x /etc/init.d/nlbwmon \]; then$' "$BLOCK" || {
  echo "FAIL: clients block does not gate nlbwmon setup on the init script"
  exit 1
}
grep -q '^    ensure_dir /usr/share/nlbwmon$' "$BLOCK" || {
  echo "FAIL: nlbwmon protocols directory is not created inside the gate"
  exit 1
}
grep -q '^    install_file "\$SCRIPT_DIR/nlbwmon/protocols" /usr/share/nlbwmon/protocols 0644$' "$BLOCK" || {
  echo "FAIL: nlbwmon protocols file install is missing from the gated block"
  exit 1
}
grep -q '^    /etc/init.d/nlbwmon enable$' "$BLOCK" || {
  echo "FAIL: nlbwmon enable is missing from the gated block"
  exit 1
}
grep -q '^    /etc/init.d/nlbwmon restart$' "$BLOCK" || {
  echo "FAIL: nlbwmon restart is missing from the gated block"
  exit 1
}
grep -q 'skipping protocols file and service restart' "$BLOCK" || {
  echo "FAIL: missing unavailable-nlbwmon warning"
  exit 1
}

awk '
  /^  if \[ -x \/etc\/init\.d\/nlbwmon \]; then$/ { gated = 1; next }
  gated && /^  else$/ { next }
  gated && /^  fi$/ { gated = 0; next }
  !gated && /nlbwmon\/protocols|\/etc\/init\.d\/nlbwmon/ {
    print "FAIL: unguarded nlbwmon operation: " $0
    bad = 1
  }
  END { exit bad ? 1 : 0 }
' "$BLOCK"

grep -q '^LAN_IP="\${LAN_IP%%/\*}"$' "$SETUP" || {
  echo "FAIL: setup.sh does not strip CIDR suffixes from network.lan.ipaddr before printing/checking METRICS_URL"
  exit 1
}

grep -q 'pkg_install_optional conntrack-tools.*pkg_install_optional conntrack' "$SETUP" || {
  echo "FAIL: setup.sh does not try both conntrack-tools and conntrack package names"
  exit 1
}

grep -q 'pkg_install_optional coreutils-stat' "$SETUP" || {
  echo "FAIL: setup.sh does not try coreutils-stat for inode fallback"
  exit 1
}

echo "PASS: tests/test_setup_nlbwmon_optional.sh"
