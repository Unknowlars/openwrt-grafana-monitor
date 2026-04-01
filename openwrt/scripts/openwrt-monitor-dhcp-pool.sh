#!/bin/sh

set -e

OUTDIR="/var/prometheus"
OUTFILE="$OUTDIR/openwrt_dhcp_pool.prom"
TMPFILE="$OUTFILE.$$"
METRICFILE="$TMPFILE.metrics"
LEASE_FILE="/tmp/dhcp.leases"

mkdir -p "$OUTDIR"

now="$(date +%s)"
pool_total=0
lease_count=0
lease_active=0
lease_remaining_max=0
lease_remaining_min=0

: > "$METRICFILE"

for section in $(uci -q show dhcp | awk -F'[.=]' '/=dhcp$/{print $2}'); do
  ignore="$(uci -q get "dhcp.$section.ignore" 2>/dev/null || printf '0')"
  [ "$ignore" = "1" ] && continue

  iface="$(uci -q get "dhcp.$section.interface" 2>/dev/null || printf '%s' "$section")"
  limit="$(uci -q get "dhcp.$section.limit" 2>/dev/null || printf '0')"

  case "$limit" in
    ''|*[!0-9]*) limit=0 ;;
  esac

  pool_total=$((pool_total + limit))
  printf 'openwrt_dhcp_pool_size{interface="%s"} %s\n' "$iface" "$limit" >> "$METRICFILE"
done

if [ -f "$LEASE_FILE" ]; then
  while read -r expires _ _ _ _; do
    lease_count=$((lease_count + 1))

    case "$expires" in
      ''|*[!0-9]*) continue ;;
    esac

    if [ "$expires" -eq 0 ] || [ "$expires" -gt "$now" ]; then
      lease_active=$((lease_active + 1))

      if [ "$expires" -eq 0 ]; then
        continue
      fi

      remaining=$((expires - now))
      if [ "$remaining" -lt 0 ]; then
        remaining=0
      fi

      if [ "$lease_remaining_max" -lt "$remaining" ]; then
        lease_remaining_max="$remaining"
      fi

      if [ "$lease_remaining_min" -eq 0 ] || [ "$remaining" -lt "$lease_remaining_min" ]; then
        lease_remaining_min="$remaining"
      fi
    fi
  done < "$LEASE_FILE"
fi

if [ "$pool_total" -gt 0 ]; then
  pool_utilization="$(awk -v used="$lease_active" -v total="$pool_total" 'BEGIN { printf "%.2f", (used / total) * 100 }')"
else
  pool_utilization="0"
fi

static_hosts="$(uci -q show dhcp | awk -F= '$2=="host" {c++} END {print c+0}')"

{
  printf '# HELP openwrt_dhcp_pool_size Configured DHCP pool size per interface.\n'
  printf '# TYPE openwrt_dhcp_pool_size gauge\n'
  printf '# HELP openwrt_dhcp_pool_size_total Total configured DHCP pool size across interfaces.\n'
  printf '# TYPE openwrt_dhcp_pool_size_total gauge\n'
  printf '# HELP openwrt_dhcp_leases_used Number of active DHCP leases.\n'
  printf '# TYPE openwrt_dhcp_leases_used gauge\n'
  printf '# HELP openwrt_dhcp_leases_total Number of lease entries in the lease file.\n'
  printf '# TYPE openwrt_dhcp_leases_total gauge\n'
  printf '# HELP openwrt_dhcp_pool_utilization_percent Active lease utilization of the configured DHCP pool.\n'
  printf '# TYPE openwrt_dhcp_pool_utilization_percent gauge\n'
  printf '# HELP openwrt_dhcp_lease_remaining_seconds_max Maximum remaining DHCP lease lifetime in seconds.\n'
  printf '# TYPE openwrt_dhcp_lease_remaining_seconds_max gauge\n'
  printf '# HELP openwrt_dhcp_lease_remaining_seconds_min Minimum remaining DHCP lease lifetime in seconds.\n'
  printf '# TYPE openwrt_dhcp_lease_remaining_seconds_min gauge\n'
  printf '# HELP openwrt_dhcp_static_hosts_total Number of configured static DHCP hosts.\n'
  printf '# TYPE openwrt_dhcp_static_hosts_total gauge\n'
  cat "$METRICFILE"
  printf 'openwrt_dhcp_pool_size_total %s\n' "$pool_total"
  printf 'openwrt_dhcp_leases_used %s\n' "$lease_active"
  printf 'openwrt_dhcp_leases_total %s\n' "$lease_count"
  printf 'openwrt_dhcp_pool_utilization_percent %s\n' "$pool_utilization"
  printf 'openwrt_dhcp_lease_remaining_seconds_max %s\n' "$lease_remaining_max"
  printf 'openwrt_dhcp_lease_remaining_seconds_min %s\n' "$lease_remaining_min"
  printf 'openwrt_dhcp_static_hosts_total %s\n' "$static_hosts"
} > "$TMPFILE"

mv "$TMPFILE" "$OUTFILE"
rm -f "$METRICFILE"
