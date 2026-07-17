#!/bin/sh

set -e

lease_file="/tmp/dhcp.leases"
tmp_file="/tmp/device-status.out.$$"

if [ ! -f "$lease_file" ]; then
  : > "$tmp_file"
  mv "$tmp_file" /tmp/device-status.out
  exit 0
fi

: > "$tmp_file"

while read -r _ mac ip hostname _; do
  [ -n "$ip" ] || continue
  [ -n "$hostname" ] || hostname="unknown"

  if ping -c 1 -W 1 "$ip" >/dev/null 2>&1; then
    status="online"
    up="1"
  else
    status="offline"
    up="0"
  fi

  printf 'device=%s mac=%s ip=%s status=%s up=%s\n' \
    "$hostname" "$mac" "$ip" "$status" "$up" >> "$tmp_file"
done < "$lease_file"

mv "$tmp_file" /tmp/device-status.out
