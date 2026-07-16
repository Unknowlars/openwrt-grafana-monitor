# OpenWrt Router Setup

This guide covers the router-side pieces for OpenWrt 24.10 and 25.12.

## Requirements

- OpenWrt 24.10 or 25.12
- SSH access as `root`
- At least 8 MB flash free: `df -h /overlay`
- At least 32 MB RAM free: `free`
- Monitoring host reachable from the router LAN

## Quick Setup

From your local machine:

```sh
scp openwrt/setup.sh root@192.168.0.1:/tmp/
ssh root@192.168.0.1 "sh /tmp/setup.sh 192.168.0.100"
#                                        ^ monitoring host LAN IP
```

The script detects the package manager:

- OpenWrt 24.10: `opkg`
- OpenWrt 25.12+: `apk`

It installs exporter packages, enables the textfile collector, creates custom metrics, configures remote syslog, and starts the exporter.

## Optional Script Settings

Set these before running the script if the defaults do not fit your router:

```sh
EXPORTER_LISTEN_INTERFACE=lan \
SYSLOG_PORT=514 \
PING_TARGET=1.1.1.1 \
PUBLIC_IP_LOOKUP=0 \
PUBLIC_IP_CHECK_INTERVAL=900 \
sh /tmp/setup.sh 192.168.0.100
```

| Variable | Default | Purpose |
|---|---|---|
| `EXPORTER_LISTEN_INTERFACE` | `lan` | Interface where `:9100` listens |
| `SYSLOG_PORT` | `514` | Remote syslog destination port |
| `PING_TARGET` | `1.1.1.1` | Packet-loss probe target |
| `PUBLIC_IP_LOOKUP` | `0` | Set `1` to query public IP endpoint |
| `PUBLIC_IP_URL` | `https://api.ipify.org` | Public IP endpoint |
| `PUBLIC_IP_CHECK_INTERVAL` | `900` | Minimum seconds between public IP endpoint calls |

## Manual Package Install

Prefer the setup script because it also installs custom metrics. If you need to install manually:

```sh
# OpenWrt 24.10
opkg update
opkg install \
  prometheus-node-exporter-lua \
  prometheus-node-exporter-lua-openwrt \
  prometheus-node-exporter-lua-nat_traffic \
  prometheus-node-exporter-lua-netstat \
  prometheus-node-exporter-lua-textfile
```

```sh
# OpenWrt 25.12+
apk update
apk add \
  prometheus-node-exporter-lua \
  prometheus-node-exporter-lua-openwrt \
  prometheus-node-exporter-lua-nat_traffic \
  prometheus-node-exporter-lua-netstat \
  prometheus-node-exporter-lua-textfile
```

Do not run `apk upgrade` on OpenWrt. Upgrade firmware with sysupgrade or attended sysupgrade.

WiFi collectors are installed by the script as best-effort optional packages:

```sh
prometheus-node-exporter-lua-wifi
prometheus-node-exporter-lua-wifi_stations
prometheus-node-exporter-lua-hostapd_stations
```

`hostapd_stations` is preferred for per-client WiFi quality panels because it reads station data directly from hostapd. `wifi_stations` remains installed when available for compatibility, but it is driver-dependent and may return no data on some mt76 devices.

Temperature and nftables collectors are also installed by the script as best-effort optional packages:

```sh
prometheus-node-exporter-lua-thermal
prometheus-node-exporter-lua-hwmon
prometheus-node-exporter-lua-nft-counters
```

Package availability depends on your OpenWrt feed. Optional package failures are warnings only.

If you use mwan3:

```sh
# OpenWrt 24.10
opkg install prometheus-node-exporter-lua-mwan3

# OpenWrt 25.12+
apk add prometheus-node-exporter-lua-mwan3
```

## Exporter Configuration

The setup script configures the exporter to listen on LAN:

```sh
uci set prometheus-node-exporter-lua.main.listen_interface=lan
uci set prometheus-node-exporter-lua.main.listen_port=9100
uci commit prometheus-node-exporter-lua
/etc/init.d/prometheus-node-exporter-lua restart
```

Verify from the router:

```sh
wget -qO- http://127.0.0.1:9100/metrics | head -40
```

Verify from the monitoring host:

```sh
curl http://192.168.0.1:9100/metrics | head -40
```

## Custom Textfile Metrics

The setup script installs:

- `/usr/bin/openwrt-grafana-monitor-metrics`
- `/etc/openwrt-grafana-monitor.conf`
- `/var/prometheus/openwrt-grafana-monitor.prom`
- A cron entry that refreshes metrics every minute

These metrics back dashboard panels that the official exporter does not provide directly:

- `dhcp_lease{mac,ip,hostname}`
- `router_device_up{device,mac,ip,status}`
- `wan_info{wanip,publicip,hostname}`
- `packet_loss{target}`
- `overlay_bytes_total`
- `overlay_bytes_used`
- `gateway_packet_loss{gateway}`
- `wan_public_ip_changed`
- `dhcpv6_lease_count`

Check them on the router:

```sh
/usr/bin/openwrt-grafana-monitor-metrics
cat /var/prometheus/openwrt-grafana-monitor.prom
wget -qO- http://127.0.0.1:9100/metrics | grep -E 'dhcp_lease|router_device_up|wan_info|packet_loss|overlay_bytes|gateway_packet_loss|wan_public_ip_changed|dhcpv6_lease_count|node_textfile'
```

Check optional collector metrics:

```sh
wget -qO- http://127.0.0.1:9100/metrics | grep -E 'hostapd_station|node_thermal_zone_temp|node_hwmon_temp_celsius|nft_counter'
```

## Optional nftables Counters

The setup script installs `prometheus-node-exporter-lua-nft-counters` as best effort, but it does not edit firewall rules. Enable only a small number of named counters manually so Prometheus label cardinality stays bounded.

Example for an existing WAN reject rule:

```sh
uci show firewall | grep -i "Reject-WAN"
uci set firewall.@rule[0].counter='1'
uci commit firewall
/etc/init.d/firewall restart
nft --json list counters
```

Use your actual rule index or edit `/etc/config/firewall` directly. Avoid counters parameterized by source IP, destination IP, or port.

## Remote Syslog

Replace `192.168.0.100` with the monitoring host IP:

```sh
uci set system.@system[0].log_ip=192.168.0.100
uci set system.@system[0].log_port=514
uci set system.@system[0].log_proto=udp
uci commit system
/etc/init.d/log restart
```

Verify from the router:

```sh
uci show system | grep log_
logger "test message from openwrt"
```

## Optional Firewall Logging

To see firewall DROP events in the Logs dashboard:

```sh
uci set firewall.@defaults[0].drop_invalid=1
uci commit firewall
/etc/init.d/firewall restart
```

Or add `option log 1` to specific firewall rules.

## Interface Names

The dashboard defaults are:

- WAN: `wan`
- 2.4 GHz WiFi: `phy0-ap0`
- 5 GHz WiFi: `phy1-ap0`
- VPN: `tailscale0`

Check your router:

```sh
ip route | grep default
cat /proc/net/dev
```

Use the Grafana dashboard variables to change interface names without editing JSON.
