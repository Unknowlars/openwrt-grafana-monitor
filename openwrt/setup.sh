#!/bin/sh
# =============================================================================
# OpenWRT Grafana Monitor — Router Setup Script
# =============================================================================
#
# Run this script ON your OpenWRT router via SSH.
# It expects the whole `openwrt/` directory so it can copy the bundled
# collector files and helper scripts alongside the setup script.
#
#   scp -r openwrt root@192.168.0.1:/tmp/
#   ssh root@192.168.0.1 "sh /tmp/openwrt/setup.sh <MONITORING_HOST_IP>"
#
# Arguments:
#   $1  IP address of the machine running the Docker monitoring stack
#       (required — the router will send syslog here)
#
# =============================================================================

set -e

MONITORING_HOST="${1}"
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
COLLECTOR_SRC_DIR="$SCRIPT_DIR/collectors"
HELPER_SRC_DIR="$SCRIPT_DIR/scripts"
CRONTAB_FILE="/etc/crontabs/root"

install_if_available() {
  pkg="$1"

  if opkg list | grep -q "^$pkg -"; then
    echo "==> Installing optional package: $pkg"
    opkg install "$pkg"
  else
    echo "==> Optional package not available on this OpenWRT build: $pkg"
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

if [ -z "$MONITORING_HOST" ]; then
  echo "Usage: $0 <MONITORING_HOST_IP>"
  echo "  Example: $0 192.168.0.100"
  exit 1
fi

if [ ! -d "$COLLECTOR_SRC_DIR" ] || [ ! -d "$HELPER_SRC_DIR" ]; then
  echo "ERROR: setup.sh expects the whole openwrt/ directory."
  echo "Copy it with: scp -r openwrt root@<router>:/tmp/"
  exit 1
fi

echo "==> OpenWRT Grafana Monitor setup"
echo "    Monitoring host: $MONITORING_HOST"
echo ""

# ── Install packages ──────────────────────────────────────────────────────────

echo "==> Updating package list..."
opkg update

echo "==> Installing required Prometheus exporters..."
opkg install \
  prometheus-node-exporter-lua \
  prometheus-node-exporter-lua-textfile \
  prometheus-node-exporter-lua-uci_dhcp_host \
  prometheus-node-exporter-lua-openwrt \
  prometheus-node-exporter-lua-wifi \
  prometheus-node-exporter-lua-wifi_stations \
  prometheus-node-exporter-lua-nat_traffic \
  prometheus-node-exporter-lua-netstat

install_if_available prometheus-node-exporter-lua-hwmon
install_if_available prometheus-node-exporter-lua-thermal

# Install mwan3 exporter only if mwan3 is installed
if opkg list-installed | grep -q "^mwan3 "; then
  echo "==> mwan3 detected, installing mwan3 exporter..."
  opkg install prometheus-node-exporter-lua-mwan3
fi

# ── Install bundled helper files ───────────────────────────────────────────────

echo "==> Installing bundled collector files and helper scripts..."
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

echo "==> Configuring exporter listener on LAN..."
uci set prometheus-node-exporter-lua.main.listen_interface='lan'
uci set prometheus-node-exporter-lua.main.listen_port='9100'
uci commit prometheus-node-exporter-lua

# ── Configure scheduled helper scripts ─────────────────────────────────────────

echo "==> Configuring helper cron jobs..."
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

echo "==> Running helper scripts once so custom metrics appear immediately..."
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

echo "==> Enabling and starting prometheus-node-exporter-lua..."
/etc/init.d/prometheus-node-exporter-lua enable
/etc/init.d/prometheus-node-exporter-lua restart

echo "==> Enabling and restarting cron..."
/etc/init.d/cron enable
/etc/init.d/cron restart

# Give it a moment to start
sleep 2

# Verify it's running
if wget -qO- "http://127.0.0.1:9100/metrics" > /dev/null 2>&1; then
  echo "    OK: metrics endpoint is up at :9100/metrics"
else
  echo "    WARNING: metrics endpoint not responding yet, check with:"
  echo "    wget -qO- http://127.0.0.1:9100/metrics"
fi

# ── Configure remote syslog ───────────────────────────────────────────────────

echo "==> Configuring remote syslog → $MONITORING_HOST:514 ..."
uci set system.@system[0].log_ip="$MONITORING_HOST"
uci set system.@system[0].log_remote='1'
uci set system.@system[0].log_port=514
uci set system.@system[0].log_proto=tcp
uci set system.@system[0].log_hostname="$(uci get system.@system[0].hostname 2>/dev/null || echo openwrt)"
uci commit system

/etc/init.d/log restart
echo "    OK: syslog configured"

# ── Summary ───────────────────────────────────────────────────────────────────

echo ""
echo "==> Setup complete!"
echo ""
echo "    Metrics:  http://$(uci get network.lan.ipaddr 2>/dev/null || printf '%s' '<ROUTER_IP>'):9100/metrics"
echo "    Syslog:   → $MONITORING_HOST:514 (TCP)"
echo "    Helpers:  device status, packet loss, WAN/public IP, WAN quality, filesystem, service health"
echo "              DHCP pool, link health, softnet, IPv6 WAN health, inodes, firewall counters, SQM, WiFi radio"
echo ""
echo "    Now start the Docker stack on $MONITORING_HOST:"
echo "    docker compose up -d"
echo ""
