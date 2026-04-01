#!/bin/sh

set -e

OUTDIR="/var/prometheus"
OUTFILE="$OUTDIR/openwrt_link_health.prom"
TMPFILE="$OUTFILE.$$"

mkdir -p "$OUTDIR"

{
  printf '# HELP openwrt_link_up Whether the network link is up for the interface (1 up, 0 down).\n'
  printf '# TYPE openwrt_link_up gauge\n'
  printf '# HELP openwrt_link_speed_bits_per_second Interface link speed in bits per second, or 0 when unknown.\n'
  printf '# TYPE openwrt_link_speed_bits_per_second gauge\n'
  printf '# HELP openwrt_link_duplex Interface duplex mode (2 full, 1 half, 0 unknown).\n'
  printf '# TYPE openwrt_link_duplex gauge\n'
  printf '# HELP openwrt_link_carrier_changes_total Carrier state changes reported by the kernel.\n'
  printf '# TYPE openwrt_link_carrier_changes_total counter\n'
  printf '# HELP openwrt_link_info Static interface state labels.\n'
  printf '# TYPE openwrt_link_info gauge\n'

  for path in /sys/class/net/*; do
    [ -d "$path" ] || continue
    dev="$(basename "$path")"
    [ "$dev" = "lo" ] && continue

    carrier="0"
    operstate="unknown"
    speed_mbps="0"
    duplex_raw="unknown"
    duplex_val="0"

    if [ -f "$path/carrier" ]; then
      carrier="$(cat "$path/carrier" 2>/dev/null || printf '0')"
    fi

    if [ -f "$path/operstate" ]; then
      operstate="$(cat "$path/operstate" 2>/dev/null || printf 'unknown')"
    fi

    if [ -f "$path/speed" ]; then
      speed_mbps="$(cat "$path/speed" 2>/dev/null || printf '0')"
      case "$speed_mbps" in
        ''|*[!0-9]*) speed_mbps=0 ;;
      esac
    fi

    if [ -f "$path/duplex" ]; then
      duplex_raw="$(cat "$path/duplex" 2>/dev/null || printf 'unknown')"
    fi

    case "$duplex_raw" in
      full|Full) duplex_val=2 ;;
      half|Half) duplex_val=1 ;;
      *) duplex_val=0 ;;
    esac

    up=0
    if [ "$carrier" = "1" ] || [ "$operstate" = "up" ]; then
      up=1
    fi

    speed_bps=$((speed_mbps * 1000000))

    printf 'openwrt_link_up{device="%s"} %s\n' "$dev" "$up"
    printf 'openwrt_link_speed_bits_per_second{device="%s"} %s\n' "$dev" "$speed_bps"
    printf 'openwrt_link_duplex{device="%s"} %s\n' "$dev" "$duplex_val"
    printf 'openwrt_link_info{device="%s",operstate="%s",duplex="%s"} 1\n' "$dev" "$operstate" "$duplex_raw"

    if [ -f "$path/carrier_changes" ]; then
      carrier_changes="$(cat "$path/carrier_changes" 2>/dev/null || printf '0')"
      case "$carrier_changes" in
        ''|*[!0-9]*) carrier_changes=0 ;;
      esac
      printf 'openwrt_link_carrier_changes_total{device="%s"} %s\n' "$dev" "$carrier_changes"
    fi
  done
} > "$TMPFILE"

mv "$TMPFILE" "$OUTFILE"
