#!/bin/sh

set -e

target="${1:-1.1.1.1}"
count="${2:-10}"
tmp_file="/tmp/packetloss.out.$$"

loss=$(ping -c "$count" -W 1 "$target" 2>/dev/null | awk -F ', ' '/packet loss/ {gsub(/%/, "", $3); print $3}')

if [ -z "$loss" ]; then
  loss="100"
fi

printf '%s\n' "$loss" > "$tmp_file"
mv "$tmp_file" /tmp/packetloss.out
