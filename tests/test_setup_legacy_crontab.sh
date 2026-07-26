#!/bin/sh
# setup.sh must not abort on the legacy-crontab upgrade path.
#
# There is no general test harness for setup.sh (it mutates a live router), so
# this extracts the legacy-crontab removal block straight out of the real
# source and runs it against a temp crontab under the same `set -eu` the script
# uses. Extracting rather than copying keeps the test honest: if the block moves
# or changes shape the extraction fails loudly instead of silently testing a
# stale copy.
#
# The case that matters: `grep -v` exits 1 when it selects no lines, so a
# crontab containing *only* the two legacy entries used to kill the installer
# mid-run -- collectors copied and /var/prometheus wiped, but no exporter
# listener, no cron jobs, and no remote syslog, with no error explaining why.

set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

SETUP="$ROOT/openwrt/setup.sh"
BLOCK="$WORK/block.sh"

# Extract from the `if grep -qE ...legacy...` line through the cleanup `rm -f`.
awk '
  /^if grep -qE .openwrt-grafana-monitor-\(metrics\|sqm\). "\$CRONTAB_FILE"/ { inblock = 1 }
  inblock { print }
  inblock && /^rm -f "\$CRONTAB_FILE\.clean"$/ { exit }
' "$SETUP" > "$BLOCK"

[ -s "$BLOCK" ] || {
  echo "FAIL: could not extract the legacy-crontab block from openwrt/setup.sh"
  echo "      (the block moved or changed shape -- update this test)"
  exit 1
}
grep -q 'grep -vE' "$BLOCK" || { echo "FAIL: extracted block has no grep -vE"; exit 1; }
grep -q '^  mv "\$CRONTAB_FILE.clean" "\$CRONTAB_FILE"$' "$BLOCK" || {
  echo "FAIL: extracted block does not mv the staged file into place"
  exit 1
}
# The mv must be unconditional. Guarding it on [ -s ... ] would skip the removal
# in exactly the case this fix is about, leaving the legacy lines in place and
# reintroducing the duplicate-series problem.
! grep -q '\[ -s "\$CRONTAB_FILE.clean" \]' "$BLOCK" || {
  echo "FAIL: mv is guarded on a non-empty staged file"
  exit 1
}

run_case() {
  case_name="$1"
  crontab_body="$2"
  must_keep="${3:-}"

  CT="$WORK/crontab.$$"
  printf '%s' "$crontab_body" > "$CT"

  # `log` is a setup.sh helper; the extracted block calls it.
  CRONTAB_FILE="$CT" sh -eu -c '
    log() { printf "%s\n" "$*"; }
    CRONTAB_FILE="$1"
    . "$2"
    echo REACHED_END
  ' _ "$CT" "$BLOCK" > "$WORK/out.$$" 2>&1 || {
    echo "FAIL [$case_name]: block exited non-zero"
    sed 's/^/       /' "$WORK/out.$$"
    return 1
  }

  grep -q REACHED_END "$WORK/out.$$" || {
    echo "FAIL [$case_name]: block aborted before completing"
    sed 's/^/       /' "$WORK/out.$$"
    return 1
  }

  # No legacy entries may survive, and the stray staged file must be cleaned up.
  ! grep -qE 'openwrt-grafana-monitor-(metrics|sqm)' "$CT" || {
    echo "FAIL [$case_name]: legacy cron entries survived removal"
    return 1
  }
  [ ! -e "$CT.clean" ] || {
    echo "FAIL [$case_name]: stray $CT.clean left behind"
    return 1
  }

  if [ -n "$must_keep" ]; then
    grep -q "$must_keep" "$CT" || {
      echo "FAIL [$case_name]: unrelated entry '$must_keep' was lost"
      return 1
    }
  fi

  printf 'ok: %s\n' "$case_name"
  rm -f "$CT" "$WORK/out.$$"
}

# The legacy lines are the entire crontab, so `grep -v` selects
# nothing and exits 1. Result must be an empty crontab, not a dead installer.
run_case "legacy entries are the whole crontab" \
'* * * * * /usr/bin/openwrt-grafana-monitor-metrics
*/5 * * * * /usr/bin/openwrt-grafana-monitor-sqm
'

# Mixed: unrelated entries must be preserved.
run_case "legacy entries mixed with unrelated ones" \
'*/5 * * * * /usr/bin/openwrt-monitor-wan-quality
* * * * * /usr/bin/openwrt-grafana-monitor-metrics
0 3 * * * /usr/bin/something-else
' something-else

# No legacy entries at all: the block must be a no-op, not a truncation.
CT="$WORK/none"
printf '0 3 * * * /usr/bin/something-else\n' > "$CT"
CRONTAB_FILE="$CT" sh -eu -c '
  log() { printf "%s\n" "$*"; }
  CRONTAB_FILE="$1"
  . "$2"
  echo REACHED_END
' _ "$CT" "$BLOCK" > "$WORK/out.none" 2>&1 || {
  echo "FAIL [no legacy entries]: block exited non-zero"
  cat "$WORK/out.none"
  exit 1
}
grep -q REACHED_END "$WORK/out.none" || { echo "FAIL [no legacy entries]: aborted"; exit 1; }
grep -q 'something-else' "$CT" || { echo "FAIL [no legacy entries]: unrelated entry was lost"; exit 1; }
printf 'ok: %s\n' "no legacy entries is a no-op"

echo "PASS: tests/test_setup_legacy_crontab.sh"
