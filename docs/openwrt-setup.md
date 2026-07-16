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
scp -O openwrt/setup.sh root@192.168.0.1:/tmp/
ssh root@192.168.0.1 "sh /tmp/setup.sh 192.168.0.100"
#                                        ^ monitoring host LAN IP
```

`-O` forces legacy scp mode. OpenWrt's default SSH server often does not provide an SFTP server, and modern OpenSSH `scp` uses SFTP by default.

The script detects the package manager:

- OpenWrt 24.10: `opkg`
- OpenWrt 25.12+: `apk`

It installs exporter packages, enables the textfile collector, creates custom metrics, configures remote syslog, and starts the exporter.

## Migrating From Older Router Scripts

If this router already ran an older OpenWrt monitoring setup, keep only one collector path for each metric. The current repo installs one cron job:

```cron
* * * * * /usr/bin/openwrt-grafana-monitor-metrics >/dev/null 2>&1
```

Older setups may also have jobs such as:

```cron
*/1 * * * * /usr/bin/1-minute-script.sh
*/5 * * * * /usr/bin/5-minute-script.sh
* * * * * /usr/bin/15-second-script.sh
* * * * * sleep 15; /usr/bin/15-second-script.sh
* * * * * sleep 30; /usr/bin/15-second-script.sh
* * * * * sleep 45; /usr/bin/15-second-script.sh
*/1 * * * * /usr/bin/device-status-ping.sh
*/1 * * * * /usr/bin/new_device.sh
*/1 * * * * /usr/bin/packet-loss.sh
*/1 * * * * /usr/bin/openwrt-monitor-device-status.sh
*/5 * * * * /usr/bin/openwrt-monitor-packet-loss.sh
*/5 * * * * /usr/bin/openwrt-monitor-wan-info.sh
*/1 * * * * /usr/bin/openwrt-monitor-service-health.sh
*/5 * * * * /usr/bin/openwrt-monitor-wan-quality.sh
*/10 * * * * /usr/bin/openwrt-monitor-filesystem.sh
*/1 * * * * /usr/bin/openwrt-monitor-dhcp-pool.sh
*/1 * * * * /usr/bin/openwrt-monitor-link-health.sh
*/1 * * * * /usr/bin/openwrt-monitor-softnet.sh
*/5 * * * * /usr/bin/openwrt-monitor-ipv6-health.sh
*/10 * * * * /usr/bin/openwrt-monitor-inodes.sh
*/2 * * * * /usr/bin/openwrt-monitor-firewall-counters.sh
*/1 * * * * /usr/bin/openwrt-monitor-sqm.sh
*/2 * * * * /usr/bin/openwrt-monitor-wifi-radio.sh
```

Those scripts are not installed or managed by this repo. They can create duplicate metrics and noisy cron syslog lines if left enabled. Audit before removing anything:

```sh
crontab -l
ls -l /usr/bin/openwrt-monitor-* /usr/bin/*packet-loss* /usr/bin/*device* 2>/dev/null
```

By default, setup auto-detects known legacy jobs. In an interactive SSH session it prompts before removing them. In a non-interactive shell it keeps them and prints a warning.

To remove the known old monitoring cron entries without prompting:

```sh
CLEANUP_LEGACY_CRON=1 sh /tmp/setup.sh 192.168.0.100
```

To always keep legacy jobs without prompting:

```sh
CLEANUP_LEGACY_CRON=0 sh /tmp/setup.sh 192.168.0.100
```

This removes only the known old monitoring cron entries listed above and always preserves:

```cron
* * * * * /usr/bin/openwrt-grafana-monitor-metrics >/dev/null 2>&1
```

To disable legacy jobs manually instead, edit root's crontab and remove only the old monitoring lines you no longer need:

```sh
crontab -e
/etc/init.d/cron restart
```

Do not remove unrelated jobs such as speed tests, backups, or custom maintenance tasks unless you know they are obsolete.

## Optional Script Settings

Set these before running the script if the defaults do not fit your router:

```sh
EXPORTER_LISTEN_INTERFACE=lan \
SYSLOG_PORT=514 \
PING_TARGET=1.1.1.1 \
DNS_PROBE_HOST=openwrt.org \
DNS_PROBE_TIMEOUT=5 \
PUBLIC_IP_LOOKUP=0 \
PUBLIC_IP_CHECK_INTERVAL=900 \
ENABLE_SQM_METRICS=0 \
SQM_INTERFACES='' \
CLEANUP_LEGACY_CRON=auto \
sh /tmp/setup.sh 192.168.0.100
```

| Variable | Default | Purpose |
|---|---|---|
| `EXPORTER_LISTEN_INTERFACE` | `lan` | Interface where `:9100` listens |
| `SYSLOG_PORT` | `514` | Remote syslog destination port |
| `PING_TARGET` | `1.1.1.1` | Packet-loss probe target |
| `DNS_PROBE_HOST` | `openwrt.org` | DNS name resolved by the WAN-health DNS probe |
| `DNS_PROBE_TIMEOUT` | `5` | Ping fallback timeout in seconds when `nslookup` is unavailable |
| `PUBLIC_IP_LOOKUP` | `0` | Set `1` to query public IP endpoint |
| `PUBLIC_IP_URL` | `https://api.ipify.org` | Public IP endpoint |
| `PUBLIC_IP_CHECK_INTERVAL` | `900` | Minimum seconds between public IP endpoint calls |
| `ENABLE_SQM_METRICS` | `0` | Set `1` to install the optional SQM/cake textfile collector |
| `SQM_INTERFACES` | empty | Space-separated SQM interfaces, for example `eth0 ifb4eth0` |
| `CLEANUP_LEGACY_CRON` | `auto` | `auto` prompts on interactive runs, `1` removes known old monitor cron jobs, `0` keeps them |

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

`hostapd_stations` is preferred for per-client WiFi quality panels because it reads station data directly from hostapd. Some OpenWrt 25.x/driver combinations expose hostapd collector metadata but no station samples; the dashboards fall back to `wifi_station_*` metrics from `wifi_stations` for signal, link rates, packet rates, inactive time, and AP client counts. AP-level quality panels use `wifi_network_*` metrics from the `wifi` collector.

Temperature and nftables collectors are also installed by the script as best-effort optional packages:

```sh
prometheus-node-exporter-lua-thermal
prometheus-node-exporter-lua-hwmon
prometheus-node-exporter-lua-nft-counters
prometheus-node-exporter-lua-snmp6
```

Package availability depends on your OpenWrt feed. Optional package failures are warnings only.

If you use mwan3:

```sh
# OpenWrt 24.10
opkg install prometheus-node-exporter-lua-mwan3

# OpenWrt 25.12+
apk add prometheus-node-exporter-lua-mwan3
```

The Network dashboard includes a "Multi-WAN (mwan3)" row. It stays empty when mwan3 or the exporter package is absent.

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
LAN_IP="$(uci get network.lan.ipaddr)"
wget -qO- "http://$LAN_IP:9100/metrics" | head -40
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
- `dns_probe_success{host}`
- `dns_probe_duration_seconds{host}`
- `overlay_bytes_total`
- `overlay_bytes_used`
- `gateway_packet_loss{gateway}`
- `wan_public_ip_changed`
- `dhcpv6_lease_count`
- `openwrt_wifi_station_connected_seconds{station,vif}`

Check them on the router:

```sh
/usr/bin/openwrt-grafana-monitor-metrics
cat /var/prometheus/openwrt-grafana-monitor.prom
LAN_IP="$(uci get network.lan.ipaddr)"
wget -qO- "http://$LAN_IP:9100/metrics" | grep -E 'dhcp_lease|router_device_up|wan_info|packet_loss|dns_probe_success|dns_probe_duration_seconds|overlay_bytes|gateway_packet_loss|wan_public_ip_changed|dhcpv6_lease_count|openwrt_wifi_station_connected_seconds|node_textfile'
```

Check optional collector metrics:

```sh
LAN_IP="$(uci get network.lan.ipaddr)"
wget -qO- "http://$LAN_IP:9100/metrics" | grep -E 'hostapd_station_(signal_dbm|receive_bytes_total|transmit_bytes_total|connected_seconds_total|inactive_seconds)|wifi_network_(quality|bitrate|noise_dbm|signal_dbm)|wifi_station_(signal_dbm|inactive_milliseconds|expected_throughput_kilobits_per_second|transmit_kilobits_per_second|receive_kilobits_per_second|transmit_packets_total|receive_packets_total|receive_bytes_total|transmit_bytes_total)|wifi_stations|node_thermal_zone_temp|node_hwmon_temp_celsius|node_scrape_collector_(success|duration_seconds)|node_textfile_mtime_seconds|nft_counter|mwan3_interface_(up|status|score|uptime|lost|age|online|offline|enabled|running|turn)|snmp6_Ip6'
```

## Optional SQM/Cake Metrics

SQM/cake metrics are disabled by default because the relevant interfaces vary by router and SQM setup. Enable them only when SQM is configured and you know the egress and IFB ingress interface names:

```sh
ENABLE_SQM_METRICS=1 SQM_INTERFACES='eth0 ifb4eth0' sh /tmp/setup.sh 192.168.0.100
```

This installs:

- `/usr/bin/openwrt-grafana-monitor-sqm`
- `/var/prometheus/openwrt-grafana-monitor-sqm.prom`
- One cron entry at one-minute cadence

Metrics:

- `sqm_backlog_bytes{iface,direction}`
- `sqm_dropped_packets_total{iface,direction}`
- `sqm_overlimits_total{iface,direction}`

Direction is `egress` for configured non-IFB interfaces and `ingress` for `ifb*` interfaces. If `ENABLE_SQM_METRICS=1` and `SQM_INTERFACES` is empty, setup prints a warning and the collector emits no interface samples.

Verify qdisc data before enabling:

```sh
tc -s qdisc show dev eth0
tc -s qdisc show dev ifb4eth0
```

Verify exported metrics:

```sh
/usr/bin/openwrt-grafana-monitor-sqm
cat /var/prometheus/openwrt-grafana-monitor-sqm.prom
LAN_IP="$(uci get network.lan.ipaddr)"
wget -qO- "http://$LAN_IP:9100/metrics" | grep -E 'sqm_(backlog_bytes|dropped_packets_total|overlimits_total)'
```

## Optional Add-On Collectors

These niche collectors are not installed by default. Install them manually only when the router uses the matching feature:

- `prometheus-node-exporter-lua-unbound`: useful only when Unbound is the resolver instead of dnsmasq.
- `prometheus-node-exporter-lua-modemmanager`: useful for LTE/WWAN routers managed by ModemManager.
- `prometheus-node-exporter-lua-ethtool`: useful for link speed, duplex, and driver details.

OpenWrt 24.10:

```sh
opkg update
opkg install prometheus-node-exporter-lua-unbound prometheus-node-exporter-lua-modemmanager prometheus-node-exporter-lua-ethtool
```

OpenWrt 25.12+:

```sh
apk update
apk add prometheus-node-exporter-lua-unbound prometheus-node-exporter-lua-modemmanager prometheus-node-exporter-lua-ethtool
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
