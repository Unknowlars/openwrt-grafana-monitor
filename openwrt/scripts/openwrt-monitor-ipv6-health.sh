#!/bin/sh

set -e

OUTDIR="/var/prometheus"
OUTFILE="$OUTDIR/openwrt_ipv6_health.prom"
TMPFILE="$OUTFILE.$$"

mkdir -p "$OUTDIR"

wan6_up=0
default_route_up=0
prefix_valid=0
prefix_preferred=0
global_addr_count=0

if command -v ip >/dev/null 2>&1; then
  if ip -6 route show default 2>/dev/null | awk 'NR==1 {found=1} END {exit !found}'; then
    default_route_up=1
  fi

  global_addr_count="$(ip -6 addr show scope global 2>/dev/null | awk '/inet6 / {c++} END {print c+0}')"
fi

if command -v ifstatus >/dev/null 2>&1 && command -v jsonfilter >/dev/null 2>&1; then
  wan6_json="$(ifstatus wan6 2>/dev/null || true)"

  if [ -n "$wan6_json" ]; then
    up_raw="$(printf '%s' "$wan6_json" | jsonfilter -e '@.up' 2>/dev/null || true)"
    case "$up_raw" in
      true|1) wan6_up=1 ;;
      *) wan6_up=0 ;;
    esac

    prefix_valid_raw="$(printf '%s' "$wan6_json" | jsonfilter -e '@["ipv6-prefix"][0].valid' 2>/dev/null || true)"
    prefix_preferred_raw="$(printf '%s' "$wan6_json" | jsonfilter -e '@["ipv6-prefix"][0].preferred' 2>/dev/null || true)"

    case "$prefix_valid_raw" in
      ''|*[!0-9]*) prefix_valid=0 ;;
      *) prefix_valid="$prefix_valid_raw" ;;
    esac

    case "$prefix_preferred_raw" in
      ''|*[!0-9]*) prefix_preferred=0 ;;
      *) prefix_preferred="$prefix_preferred_raw" ;;
    esac
  fi
fi

{
  printf '# HELP openwrt_wan6_up Whether WAN IPv6 interface is up.\n'
  printf '# TYPE openwrt_wan6_up gauge\n'
  printf '# HELP openwrt_ipv6_default_route_up Whether a default IPv6 route exists.\n'
  printf '# TYPE openwrt_ipv6_default_route_up gauge\n'
  printf '# HELP openwrt_ipv6_prefix_valid_seconds Remaining valid lifetime for delegated IPv6 prefix.\n'
  printf '# TYPE openwrt_ipv6_prefix_valid_seconds gauge\n'
  printf '# HELP openwrt_ipv6_prefix_preferred_seconds Remaining preferred lifetime for delegated IPv6 prefix.\n'
  printf '# TYPE openwrt_ipv6_prefix_preferred_seconds gauge\n'
  printf '# HELP openwrt_ipv6_global_addresses Number of global IPv6 addresses present on the router.\n'
  printf '# TYPE openwrt_ipv6_global_addresses gauge\n'

  printf 'openwrt_wan6_up %s\n' "$wan6_up"
  printf 'openwrt_ipv6_default_route_up %s\n' "$default_route_up"
  printf 'openwrt_ipv6_prefix_valid_seconds %s\n' "$prefix_valid"
  printf 'openwrt_ipv6_prefix_preferred_seconds %s\n' "$prefix_preferred"
  printf 'openwrt_ipv6_global_addresses %s\n' "$global_addr_count"
} > "$TMPFILE"

mv "$TMPFILE" "$OUTFILE"
