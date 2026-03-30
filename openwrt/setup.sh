#!/bin/sh
# =============================================================================
# OpenWRT Grafana Monitor — Router Setup Script
# =============================================================================
#
# Run this script ON your OpenWRT router via SSH:
#
#   scp openwrt/setup.sh root@192.168.0.1:/tmp/
#   ssh root@192.168.0.1 "sh /tmp/setup.sh <MONITORING_HOST_IP>"
#
# Or with wget directly on the router:
#
#   wget -O /tmp/setup.sh https://raw.githubusercontent.com/.../openwrt/setup.sh
#   sh /tmp/setup.sh <MONITORING_HOST_IP>
#
# Arguments:
#   $1  IP address of the machine running the Docker monitoring stack
#       (required — the router will send syslog here)
#
# =============================================================================

set -e

MONITORING_HOST="${1}"

# ── Validate ──────────────────────────────────────────────────────────────────

if [ -z "$MONITORING_HOST" ]; then
  echo "Usage: $0 <MONITORING_HOST_IP>"
  echo "  Example: $0 192.168.0.100"
  exit 1
fi

echo "==> OpenWRT Grafana Monitor setup"
echo "    Monitoring host: $MONITORING_HOST"
echo ""

# ── Install packages ──────────────────────────────────────────────────────────

echo "==> Updating package list..."
opkg update

echo "==> Installing prometheus-node-exporter-lua..."
opkg install \
  prometheus-node-exporter-lua \
  prometheus-node-exporter-lua-openwrt \
  prometheus-node-exporter-lua-wifi \
  prometheus-node-exporter-lua-wifi_stations \
  prometheus-node-exporter-lua-nat_traffic \
  prometheus-node-exporter-lua-netstat

# Install mwan3 exporter only if mwan3 is installed
if opkg list-installed | grep -q "^mwan3 "; then
  echo "==> mwan3 detected, installing mwan3 exporter..."
  opkg install prometheus-node-exporter-lua-mwan3
fi

# ── Start and enable the exporter ─────────────────────────────────────────────

echo "==> Enabling and starting prometheus-node-exporter-lua..."
/etc/init.d/prometheus-node-exporter-lua enable
/etc/init.d/prometheus-node-exporter-lua start

# Give it a moment to start
sleep 2

# Verify it's running
if curl -sf "http://127.0.0.1:9100/metrics" > /dev/null 2>&1; then
  echo "    OK: metrics endpoint is up at :9100/metrics"
else
  echo "    WARNING: metrics endpoint not responding yet, check with:"
  echo "    curl http://127.0.0.1:9100/metrics"
fi

# ── Configure remote syslog ───────────────────────────────────────────────────

echo "==> Configuring remote syslog → $MONITORING_HOST:514 ..."
uci set system.@system[0].log_ip="$MONITORING_HOST"
uci set system.@system[0].log_port=514
uci set system.@system[0].log_proto=udp
uci set system.@system[0].log_hostname="$(uci get system.@system[0].hostname 2>/dev/null || echo openwrt)"
uci commit system

/etc/init.d/log restart
echo "    OK: syslog configured"

# ── Summary ───────────────────────────────────────────────────────────────────

echo ""
echo "==> Setup complete!"
echo ""
echo "    Metrics:  http://$(uci get network.lan.ipaddr 2>/dev/null || echo <ROUTER_IP>):9100/metrics"
echo "    Syslog:   → $MONITORING_HOST:514 (UDP)"
echo ""
echo "    Now start the Docker stack on $MONITORING_HOST:"
echo "    docker compose up -d"
echo ""
