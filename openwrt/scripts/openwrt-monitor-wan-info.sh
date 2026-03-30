#!/bin/sh

set -e

. /lib/functions/network.sh

tmp_file="/tmp/wanip.out.$$"
wan_network=""
wan_ip="unknown"
public_ip=""

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

printf 'wanip=%s publicip=%s\n' "$wan_ip" "$public_ip" > "$tmp_file"
mv "$tmp_file" /tmp/wanip.out
