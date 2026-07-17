#!/bin/sh
# =============================================================================
# OpenWrt Grafana Monitor — Router Setup Script
# =============================================================================
#
# Run this script ON your OpenWrt router via SSH.
# It expects the whole `openwrt/` directory so it can copy the bundled
# collector files and helper scripts alongside the setup script.
#
#   scp -O -r openwrt root@192.168.0.1:/tmp/
#   ssh root@192.168.0.1 "sh /tmp/openwrt/setup.sh <MONITORING_HOST_IP>"
#
# Arguments:
#   $1  IP address of the machine running the Docker monitoring stack
#       (required — the router will send syslog here)
#
# Optional environment variables:
#   EXPORTER_LISTEN_INTERFACE  Interface for :9100; default: lan
#   SYSLOG_PORT                Remote syslog port; default: 514
#   SYSLOG_PROTO               Remote syslog protocol; default: udp
#
# =============================================================================

set -eu

MONITORING_HOST="${1:-}"
EXPORTER_LISTEN_INTERFACE="${EXPORTER_LISTEN_INTERFACE:-lan}"
SYSLOG_PORT="${SYSLOG_PORT:-514}"
SYSLOG_PROTO="${SYSLOG_PROTO:-udp}"
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
COLLECTOR_SRC_DIR="$SCRIPT_DIR/collectors"
HELPER_SRC_DIR="$SCRIPT_DIR/scripts"
CRONTAB_FILE="/etc/crontabs/root"

log() {
  printf '%s\n' "$*"
}

die() {
  log "ERROR: $*"
  exit 1
}

pkg_update() {
  case "$PKG_MANAGER" in
    apk) apk update ;;
    opkg) opkg update ;;
  esac
}

pkg_install_required() {
  case "$PKG_MANAGER" in
    apk) apk add "$@" ;;
    opkg) opkg install "$@" ;;
  esac
}

pkg_install_optional() {
  case "$PKG_MANAGER" in
    apk) apk add "$@" ;;
    opkg) opkg install "$@" ;;
  esac
}

pkg_installed() {
  case "$PKG_MANAGER" in
    apk) apk info "$1" >/dev/null 2>&1 ;;
    opkg) opkg list-installed 2>/dev/null | grep -q "^$1 " ;;
  esac
}

fetch_url() {
  url="$1"
  if command -v wget >/dev/null 2>&1; then
    wget -qO- "$url"
  elif command -v curl >/dev/null 2>&1; then
    curl -fsS "$url"
  else
    return 1
  fi
}

ensure_cron_line() {
  line="$1"

  touch "$CRONTAB_FILE"
  if ! grep -Fqx "$line" "$CRONTAB_FILE"; then
    printf '%s\n' "$line" >> "$CRONTAB_FILE"
  fi
}

ensure_dir() {
  dir="$1"

  mkdir -p "$dir"
}

install_file() {
  src="$1"
  dest="$2"
  mode="$3"

  cp "$src" "$dest"
  chmod "$mode" "$dest"
}

# ── Validate ──────────────────────────────────────────────────────────────────

REQUIRED_PACKAGES="
prometheus-node-exporter-lua
prometheus-node-exporter-lua-textfile
prometheus-node-exporter-lua-uci_dhcp_host
prometheus-node-exporter-lua-openwrt
prometheus-node-exporter-lua-nat_traffic
prometheus-node-exporter-lua-netstat
"

OPTIONAL_PACKAGES="
prometheus-node-exporter-lua-wifi
prometheus-node-exporter-lua-wifi_stations
prometheus-node-exporter-lua-hostapd_stations
prometheus-node-exporter-lua-hwmon
prometheus-node-exporter-lua-thermal
prometheus-node-exporter-lua-nft-counters
prometheus-node-exporter-lua-snmp6
"

if [ -z "$MONITORING_HOST" ]; then
  log "Usage: $0 <MONITORING_HOST_IP>"
  log "  Example: $0 192.168.0.100"
  exit 1
fi

if [ ! -d "$COLLECTOR_SRC_DIR" ] || [ ! -d "$HELPER_SRC_DIR" ]; then
  die "setup.sh expects the whole openwrt/ directory. Copy it with: scp -O -r openwrt root@<router>:/tmp/"
fi

if command -v apk >/dev/null 2>&1; then
  PKG_MANAGER="apk"
elif command -v opkg >/dev/null 2>&1; then
  PKG_MANAGER="opkg"
else
  die "neither apk nor opkg was found. This script supports OpenWrt 24.10/opkg and OpenWrt 25.12/apk."
fi

case "$SYSLOG_PROTO" in
  udp|tcp) ;;
  *) die "SYSLOG_PROTO must be udp or tcp" ;;
esac

log "==> OpenWrt Grafana Monitor setup"
log "    Monitoring host: $MONITORING_HOST"
log "    Package manager: $PKG_MANAGER"
log "    Exporter interface: $EXPORTER_LISTEN_INTERFACE"
log "    Syslog: $MONITORING_HOST:$SYSLOG_PORT/$SYSLOG_PROTO"
log ""

# ── Install packages ──────────────────────────────────────────────────────────

log "==> Updating package list..."
pkg_update

log "==> Installing required Prometheus exporters..."
pkg_install_required $REQUIRED_PACKAGES

for package in $OPTIONAL_PACKAGES; do
  log "==> Installing optional package: $package"
  if ! pkg_install_optional "$package"; then
    log "    WARNING: optional package unavailable or failed to install: $package"
  fi
done

# Install mwan3 exporter only if mwan3 is installed
if pkg_installed mwan3; then
  log "==> mwan3 detected, installing mwan3 exporter..."
  if ! pkg_install_optional prometheus-node-exporter-lua-mwan3; then
    log "    WARNING: mwan3 exporter unavailable or failed to install"
  fi
fi

# ── Install bundled helper files ───────────────────────────────────────────────

log "==> Installing bundled collector files and helper scripts..."
ensure_dir /usr/lib/lua/prometheus-collectors
ensure_dir /usr/bin
ensure_dir /var/prometheus

install_file "$COLLECTOR_SRC_DIR/dnsmasq.lua" /usr/lib/lua/prometheus-collectors/dnsmasq.lua 0644
install_file "$COLLECTOR_SRC_DIR/device_status.lua" /usr/lib/lua/prometheus-collectors/device_status.lua 0644
install_file "$COLLECTOR_SRC_DIR/packet_loss.lua" /usr/lib/lua/prometheus-collectors/packet_loss.lua 0644
install_file "$COLLECTOR_SRC_DIR/wan_info.lua" /usr/lib/lua/prometheus-collectors/wan_info.lua 0644

install_file "$HELPER_SRC_DIR/openwrt-monitor-device-status.sh" /usr/bin/openwrt-monitor-device-status.sh 0755
install_file "$HELPER_SRC_DIR/openwrt-monitor-packet-loss.sh" /usr/bin/openwrt-monitor-packet-loss.sh 0755
install_file "$HELPER_SRC_DIR/openwrt-monitor-wan-info.sh" /usr/bin/openwrt-monitor-wan-info.sh 0755
install_file "$HELPER_SRC_DIR/openwrt-monitor-filesystem.sh" /usr/bin/openwrt-monitor-filesystem.sh 0755
install_file "$HELPER_SRC_DIR/openwrt-monitor-service-health.sh" /usr/bin/openwrt-monitor-service-health.sh 0755
install_file "$HELPER_SRC_DIR/openwrt-monitor-wan-quality.sh" /usr/bin/openwrt-monitor-wan-quality.sh 0755
install_file "$HELPER_SRC_DIR/openwrt-monitor-dhcp-pool.sh" /usr/bin/openwrt-monitor-dhcp-pool.sh 0755
install_file "$HELPER_SRC_DIR/openwrt-monitor-link-health.sh" /usr/bin/openwrt-monitor-link-health.sh 0755
install_file "$HELPER_SRC_DIR/openwrt-monitor-softnet.sh" /usr/bin/openwrt-monitor-softnet.sh 0755
install_file "$HELPER_SRC_DIR/openwrt-monitor-ipv6-health.sh" /usr/bin/openwrt-monitor-ipv6-health.sh 0755
install_file "$HELPER_SRC_DIR/openwrt-monitor-inodes.sh" /usr/bin/openwrt-monitor-inodes.sh 0755
install_file "$HELPER_SRC_DIR/openwrt-monitor-firewall-counters.sh" /usr/bin/openwrt-monitor-firewall-counters.sh 0755
install_file "$HELPER_SRC_DIR/openwrt-monitor-sqm.sh" /usr/bin/openwrt-monitor-sqm.sh 0755
install_file "$HELPER_SRC_DIR/openwrt-monitor-wifi-radio.sh" /usr/bin/openwrt-monitor-wifi-radio.sh 0755

# ── Configure exporter listener ────────────────────────────────────────────────

log "==> Configuring exporter listener on $EXPORTER_LISTEN_INTERFACE..."
uci set prometheus-node-exporter-lua.main.listen_interface="$EXPORTER_LISTEN_INTERFACE"
uci set prometheus-node-exporter-lua.main.listen_port='9100'
uci commit prometheus-node-exporter-lua

# ── Configure scheduled helper scripts ─────────────────────────────────────────

log "==> Configuring helper cron jobs..."
ensure_cron_line '*/1 * * * * /usr/bin/openwrt-monitor-device-status.sh'
ensure_cron_line '*/1 * * * * /usr/bin/openwrt-monitor-service-health.sh'
ensure_cron_line '*/5 * * * * /usr/bin/openwrt-monitor-packet-loss.sh'
ensure_cron_line '*/5 * * * * /usr/bin/openwrt-monitor-wan-info.sh'
ensure_cron_line '*/5 * * * * /usr/bin/openwrt-monitor-wan-quality.sh'
ensure_cron_line '*/10 * * * * /usr/bin/openwrt-monitor-filesystem.sh'
ensure_cron_line '*/1 * * * * /usr/bin/openwrt-monitor-dhcp-pool.sh'
ensure_cron_line '*/1 * * * * /usr/bin/openwrt-monitor-link-health.sh'
ensure_cron_line '*/1 * * * * /usr/bin/openwrt-monitor-softnet.sh'
ensure_cron_line '*/5 * * * * /usr/bin/openwrt-monitor-ipv6-health.sh'
ensure_cron_line '*/10 * * * * /usr/bin/openwrt-monitor-inodes.sh'
ensure_cron_line '*/2 * * * * /usr/bin/openwrt-monitor-firewall-counters.sh'
ensure_cron_line '*/1 * * * * /usr/bin/openwrt-monitor-sqm.sh'
ensure_cron_line '*/2 * * * * /usr/bin/openwrt-monitor-wifi-radio.sh'

log "==> Running helper scripts once so custom metrics appear immediately..."
/usr/bin/openwrt-monitor-device-status.sh
/usr/bin/openwrt-monitor-service-health.sh
/usr/bin/openwrt-monitor-packet-loss.sh
/usr/bin/openwrt-monitor-wan-info.sh
/usr/bin/openwrt-monitor-wan-quality.sh
/usr/bin/openwrt-monitor-filesystem.sh
/usr/bin/openwrt-monitor-dhcp-pool.sh
/usr/bin/openwrt-monitor-link-health.sh
/usr/bin/openwrt-monitor-softnet.sh
/usr/bin/openwrt-monitor-ipv6-health.sh
/usr/bin/openwrt-monitor-inodes.sh
/usr/bin/openwrt-monitor-firewall-counters.sh
/usr/bin/openwrt-monitor-sqm.sh
/usr/bin/openwrt-monitor-wifi-radio.sh

# ── Start and enable the exporter ─────────────────────────────────────────────

log "==> Enabling and starting prometheus-node-exporter-lua..."
/etc/init.d/prometheus-node-exporter-lua enable
/etc/init.d/prometheus-node-exporter-lua restart

log "==> Enabling and restarting cron..."
/etc/init.d/cron enable
/etc/init.d/cron restart

# Give it a moment to start
sleep 2

LAN_IP="$(uci get network.lan.ipaddr 2>/dev/null || printf '%s' '<ROUTER_IP>')"
METRICS_URL="http://$LAN_IP:9100/metrics"

if fetch_url "$METRICS_URL" > /dev/null 2>&1; then
  log "    OK: metrics endpoint is up at $METRICS_URL"
else
  log "    WARNING: metrics endpoint not responding yet, check with:"
  log "    wget -qO- $METRICS_URL"
fi

# ── Configure remote syslog ───────────────────────────────────────────────────

log "==> Configuring remote syslog to $MONITORING_HOST:$SYSLOG_PORT/$SYSLOG_PROTO ..."
uci set system.@system[0].log_ip="$MONITORING_HOST"
uci set system.@system[0].log_remote='1'
uci set system.@system[0].log_port="$SYSLOG_PORT"
uci set system.@system[0].log_proto="$SYSLOG_PROTO"
uci set system.@system[0].log_hostname="$(uci get system.@system[0].hostname 2>/dev/null || echo openwrt)"
uci commit system

/etc/init.d/log restart
log "    OK: syslog configured"

# ── Summary ───────────────────────────────────────────────────────────────────

log ""
log "==> Setup complete!"
log ""
log "    Metrics:  $METRICS_URL"
log "    Syslog:   $MONITORING_HOST:$SYSLOG_PORT/$SYSLOG_PROTO"
log "    Helpers:  device status, packet loss, WAN/public IP, WAN quality, filesystem, service health"
log "              DHCP pool, link health, softnet, IPv6 WAN health, inodes, firewall counters, SQM, WiFi radio"
log ""
log "    Now start the Docker stack on $MONITORING_HOST:"
log "    docker compose up -d"
log ""
