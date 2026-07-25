#!/bin/sh

set -e

# Optional on OpenWrt; when absent wan_ip stays unknown so offline tests and
# partial installs still emit the public-IP change gauge.
[ -r /lib/functions/network.sh ] && . /lib/functions/network.sh

tmp_file="/tmp/wanip.out.$$"
# Overridable so the collector logic can be exercised in tests.
outdir="${OPENWRT_MONITOR_TEXTFILE_DIR:-/var/prometheus}"
metric_file="$outdir/openwrt_wan_info.prom"
# Stage outside $outdir (same filesystem on OpenWrt: /var -> /tmp) and mv
# atomically into place, matching every sibling helper.
#
# Note (2026-07-25 live measurement on R4's sibling defect): the textfile
# collector globs *.prom only, so a staged .prom.<pid> is not double-scraped.
# The harm is leftover-file accumulation after a crash/reboot/kill with no trap
# and no sweep.
metric_tmp="/tmp/.openwrt-monitor-openwrt_wan_info.$$"
last_public_ip_file="/tmp/openwrt-grafana-monitor-last-public-ip"
wan_network=""
wan_ip="unknown"
public_ip=""
public_ip_changed=0
hostname="$(uci get system.@system[0].hostname 2>/dev/null || printf 'openwrt')"

mkdir -p "$outdir"
# Sweeps leftover .prom.<pid> files left in $outdir by the pre-fix staging bug.
# Removable once no deployed router predates this fix.
rm -f "$metric_file".[0-9]*
trap 'rm -f "$tmp_file" "$metric_tmp"' EXIT

network_find_wan wan_network 2>/dev/null || true
if [ -n "$wan_network" ]; then
  network_get_ipaddr wan_ip "$wan_network" 2>/dev/null || true
fi

for url in http://checkip.amazonaws.com http://api.ipify.org; do
  public_ip=$(wget -qO- "$url" 2>/dev/null | tr -d ' \r\n\t')
  if [ -n "$public_ip" ]; then
    break
  fi
done

[ -n "$wan_ip" ] || wan_ip="unknown"
[ -n "$public_ip" ] || public_ip="unknown"

if [ "$public_ip" != "unknown" ]; then
  last_public_ip="$(cat "$last_public_ip_file" 2>/dev/null || true)"
  if [ -n "$last_public_ip" ] && [ "$last_public_ip" != "$public_ip" ]; then
    public_ip_changed=1
  fi
  printf '%s' "$public_ip" > "$last_public_ip_file"
fi

printf 'wanip=%s publicip=%s hostname=%s\n' "$wan_ip" "$public_ip" "$hostname" > "$tmp_file"
mv "$tmp_file" /tmp/wanip.out

{
  printf '# HELP wan_public_ip_changed 1 if public IP changed since the last successful lookup, else 0. Compatibility alias for older dashboards.\n'
  printf '# TYPE wan_public_ip_changed gauge\n'
  printf 'wan_public_ip_changed %s\n' "$public_ip_changed"
} > "$metric_tmp"
mv "$metric_tmp" "$metric_file"
