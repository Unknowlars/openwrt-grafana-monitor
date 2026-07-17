#!/bin/sh

set -e

. /lib/functions/network.sh

tmp_file="/tmp/wanip.out.$$"
outdir="/var/prometheus"
metric_file="$outdir/openwrt_wan_info.prom"
metric_tmp="$metric_file.$$"
last_public_ip_file="/tmp/openwrt-grafana-monitor-last-public-ip"
wan_network=""
wan_ip="unknown"
public_ip=""
public_ip_changed=0
hostname="$(uci get system.@system[0].hostname 2>/dev/null || printf 'openwrt')"

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

mkdir -p "$outdir"
{
  printf '# HELP wan_public_ip_changed 1 if public IP changed since the last successful lookup, else 0. Compatibility alias for older dashboards.\n'
  printf '# TYPE wan_public_ip_changed gauge\n'
  printf 'wan_public_ip_changed %s\n' "$public_ip_changed"
} > "$metric_tmp"
mv "$metric_tmp" "$metric_file"
