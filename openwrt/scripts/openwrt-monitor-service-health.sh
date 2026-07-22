#!/bin/sh

set -e

# Overridable so the collector logic can be exercised in tests.
OUTDIR="${OPENWRT_MONITOR_TEXTFILE_DIR:-/var/prometheus}"
OUTFILE="$OUTDIR/openwrt_services.prom"
# The textfile collector reads every file in $OUTDIR, so a temp file left
# there by a crashed run is scraped as a second copy of every metric below.
# Stage outside $OUTDIR (same filesystem on OpenWrt: /var -> /tmp) and mv
# atomically into place.
TMPFILE="/tmp/.openwrt-monitor-openwrt_services.$$"

mkdir -p "$OUTDIR"
rm -f "$OUTFILE".[0-9]*
trap 'rm -f "$TMPFILE"' EXIT

{
  printf '# HELP openwrt_service_up Whether the service process is running.\n'
  printf '# TYPE openwrt_service_up gauge\n'
  printf '# HELP openwrt_service_enabled Whether the init script is enabled.\n'
  printf '# TYPE openwrt_service_enabled gauge\n'

  while IFS=':' read -r service process optional; do
    if [ "$optional" = "1" ] && [ ! -x "/etc/init.d/$service" ] && ! pidof "$process" >/dev/null 2>&1; then
      continue
    fi

    if pidof "$process" >/dev/null 2>&1; then
      up=1
    else
      up=0
    fi

    enabled=0
    if [ -x "/etc/init.d/$service" ] && /etc/init.d/$service enabled >/dev/null 2>&1; then
      enabled=1
    fi

    printf 'openwrt_service_up{service="%s"} %s\n' "$service" "$up"
    printf 'openwrt_service_enabled{service="%s"} %s\n' "$service" "$enabled"
  done <<'EOF'
cron:crond:0
dnsmasq:dnsmasq:0
dropbear:dropbear:0
hostapd:hostapd:1
log:logd:0
network:netifd:0
odhcpd:odhcpd:0
rpcd:rpcd:0
uhttpd:uhttpd:0
tailscale:tailscaled:1
netifyd:netifyd:1
EOF
} > "$TMPFILE"

mv "$TMPFILE" "$OUTFILE"
