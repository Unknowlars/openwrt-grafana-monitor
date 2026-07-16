# Troubleshooting

## Package Install Fails

Check which package manager your router uses:

```sh
command -v apk && apk --version
command -v opkg && opkg --version
```

OpenWrt 24.10 uses `opkg`; OpenWrt 25.12 and newer use `apk`.

Refresh indexes and retry:

```sh
# OpenWrt 24.10
opkg update

# OpenWrt 25.12+
apk update
```

Do not run `apk upgrade` on OpenWrt. Use sysupgrade or attended sysupgrade for firmware upgrades.

If an optional collector fails, the setup script continues. The required path is the base exporter plus `openwrt`, `nat_traffic`, `netstat`, and `textfile`.

## Metrics Not Appearing in Grafana

### 1. Check the router endpoint

From the router:

```sh
LAN_IP="$(uci get network.lan.ipaddr)"
wget -qO- "http://$LAN_IP:9100/metrics" | head
/etc/init.d/prometheus-node-exporter-lua status
```

From the monitoring host:

```sh
curl http://192.168.0.1:9100/metrics | head
```

If it works locally but not from the monitoring host, check the exporter listen interface:

```sh
uci show prometheus-node-exporter-lua
```

The setup script sets:

```sh
prometheus-node-exporter-lua.main.listen_interface='lan'
```

Restart after changes:

```sh
/etc/init.d/prometheus-node-exporter-lua restart
```

### 2. Check custom textfile metrics

The Devices and WAN info panels depend on this repo's textfile metrics:

```sh
/usr/bin/openwrt-grafana-monitor-metrics
ls -l /var/prometheus
cat /var/prometheus/openwrt-grafana-monitor.prom
LAN_IP="$(uci get network.lan.ipaddr)"
wget -qO- "http://$LAN_IP:9100/metrics" | grep -E 'node_textfile|dhcp_lease|router_device_up|wan_info|packet_loss|dns_probe_success|dns_probe_duration_seconds|overlay_bytes|gateway_packet_loss|wan_public_ip_changed|dhcpv6_lease_count'
```

If `node_textfile_mtime_seconds` is missing, install the textfile collector package:

```sh
# OpenWrt 24.10
opkg install prometheus-node-exporter-lua-textfile

# OpenWrt 25.12+
apk add prometheus-node-exporter-lua-textfile
```

### 3. Check optional collectors

Panels for WiFi clients, temperature, nftables counters, mwan3, and IPv6 counters depend on optional packages. Missing metrics usually means the package is unavailable on your feed, the feature is not installed, or the device does not expose that data.

```sh
LAN_IP="$(uci get network.lan.ipaddr)"
wget -qO- "http://$LAN_IP:9100/metrics" | grep -E 'hostapd_station_(signal_dbm|receive_bytes_total|transmit_bytes_total|connected_seconds_total|inactive_seconds)|node_thermal_zone_temp|node_hwmon_temp_celsius|nft_counter|mwan3_interface_(up|status|score|uptime|lost)|snmp6_Ip6'
```

If the package is missing, install the collector that matches the panel:

```sh
# OpenWrt 24.10
opkg install prometheus-node-exporter-lua-hostapd_stations prometheus-node-exporter-lua-thermal prometheus-node-exporter-lua-hwmon prometheus-node-exporter-lua-nft-counters prometheus-node-exporter-lua-mwan3 prometheus-node-exporter-lua-snmp6

# OpenWrt 25.12+
apk add prometheus-node-exporter-lua-hostapd_stations prometheus-node-exporter-lua-thermal prometheus-node-exporter-lua-hwmon prometheus-node-exporter-lua-nft-counters prometheus-node-exporter-lua-mwan3 prometheus-node-exporter-lua-snmp6
```

### 4. Check Alloy is scraping

Open the Alloy UI at http://localhost:12345 and inspect `prometheus.scrape.openwrt`.

Or check logs:

```sh
docker logs alloy --tail 50 | grep -i "openwrt\|error\|scrape"
```

### 5. Check Prometheus received data

```sh
curl 'http://localhost:9090/api/v1/query?query=node_load1{job="openwrt"}' | python3 -m json.tool
curl 'http://localhost:9090/api/v1/query?query=router_device_up{job="openwrt"}' | python3 -m json.tool
```

### 6. Verify environment variables reached Alloy

```sh
docker exec alloy env | grep ROUTER
```

## Logs Not Appearing in Grafana

### 1. Check router syslog config

```sh
uci show system | grep log_
logger "test message from openwrt"
```

Expected:

```text
system.@system[0].log_ip='192.168.0.100'
system.@system[0].log_port='514'
system.@system[0].log_proto='udp'
```

### 2. Check Alloy receives syslog

```sh
docker logs alloy --tail 50 | grep -i "syslog\|514"
```

### 3. Check port 514

```sh
sudo tcpdump -i any udp port 514 -n
ss -ulnp | grep 514
```

If another process uses port 514, change `SYSLOG_PORT` in `.env` and rerun router setup with the same port:

```sh
SYSLOG_PORT=1514 sh /tmp/setup.sh 192.168.0.100
```

### 4. Check Loki received logs

```sh
curl 'http://localhost:3100/loki/api/v1/query?query={job="openwrt-syslog"}' | python3 -m json.tool
```

## Cron Lines Show as Errors

OpenWrt's BusyBox cron can emit command-start records with syslog severity `error` even when the command ran normally:

```text
USER root pid 15654 cmd /usr/bin/openwrt-grafana-monitor-metrics >/dev/null 2>&1
```

That line means cron started the command. It is not proof that the script failed. A real failure normally has additional output such as `not found`, permission errors, shell errors, or package install errors.

Verify this repo's collector manually on the router:

```sh
/usr/bin/openwrt-grafana-monitor-metrics
echo $?
head /var/prometheus/openwrt-grafana-monitor.prom
```

Exit code `0` means the collector completed successfully. The Logs dashboard filters these cron command-start records out of the error and warning panels, but they remain visible in "All System Logs".

If you migrated from an older monitoring setup and see many cron command-start lines every minute, check for legacy jobs:

```sh
crontab -l
```

The current repo only needs this cron entry for its custom textfile metrics:

```cron
* * * * * /usr/bin/openwrt-grafana-monitor-metrics >/dev/null 2>&1
```

See [Migrating From Older Router Scripts](openwrt-setup.md#migrating-from-older-router-scripts) before removing old jobs.

Setup prompts before removing known old monitoring cron entries during interactive runs. To force cleanup without prompting, rerun it with:

```sh
CLEANUP_LEGACY_CRON=1 sh /tmp/setup.sh 192.168.0.100
```

## Grafana Shows "No Data"

- Set the time range to "Last 1 hour".
- Wait at least one scrape interval.
- Confirm dashboard variables: `router`, `wan_interface`, `wifi24_interface`, `wifi5_interface`, `vpn_interface`.
- Test Prometheus Explore with `node_load1{job="openwrt"}`.
- Test Loki Explore with `{job="openwrt-syslog"}`.

## WAN or WiFi Panels Show No Data

The default dashboard variables are common defaults, not guaranteed names:

- WAN: `wan`
- 2.4 GHz WiFi: `phy0-ap0`
- 5 GHz WiFi: `phy1-ap0`
- VPN: `tailscale0`

Find your interface names:

```sh
ssh root@192.168.0.1 "ip route | grep default; cat /proc/net/dev"
```

Then change the Grafana dashboard variables at the top of the dashboard.

For WAN-health panels, check the custom DNS and packet-loss probes:

```sh
/usr/bin/openwrt-grafana-monitor-metrics
cat /var/prometheus/openwrt-grafana-monitor.prom | grep -E 'packet_loss|gateway_packet_loss|dns_probe_success|dns_probe_duration_seconds'
LAN_IP="$(uci get network.lan.ipaddr)"
wget -qO- "http://$LAN_IP:9100/metrics" | grep -E 'packet_loss|gateway_packet_loss|dns_probe_success|dns_probe_duration_seconds'
```

If `dns_probe_success` is `0`, verify router DNS resolution directly:

```sh
nslookup openwrt.org
```

If you changed `DNS_PROBE_HOST`, test that hostname instead.

## Optional SQM Panels Show No Data

The SQM/cake collector is disabled by default. Confirm it was enabled with real interface names:

```sh
grep -E 'ENABLE_SQM_METRICS|SQM_INTERFACES' /etc/openwrt-grafana-monitor.conf
crontab -l | grep openwrt-grafana-monitor-sqm
tc -s qdisc show dev eth0
tc -s qdisc show dev ifb4eth0
```

Then run and check the collector:

```sh
/usr/bin/openwrt-grafana-monitor-sqm
cat /var/prometheus/openwrt-grafana-monitor-sqm.prom
LAN_IP="$(uci get network.lan.ipaddr)"
wget -qO- "http://$LAN_IP:9100/metrics" | grep -E 'sqm_(backlog_bytes|dropped_packets_total|overlimits_total)'
```

## Port 514 Permission Denied

Docker normally handles privileged host ports for containers. If Alloy cannot bind:

```sh
docker port alloy
ss -tulnp | grep ':514'
```

Use a high port such as `1514` if needed:

```env
SYSLOG_PORT=1514
```

Then update the router:

```sh
SYSLOG_PORT=1514 sh /tmp/setup.sh 192.168.0.100
```

## Dashboards Not Loading

```sh
docker logs otel-lgtm 2>&1 | grep -i "dashboard\|provision"
docker compose restart otel-lgtm
```

Regenerate dashboards after editing `build_dashboards.py`:

```sh
python3 build_dashboards.py
```

## otel-lgtm Container Keeps Restarting

```sh
docker logs otel-lgtm --tail 50
```

Common causes:

- Port conflict on 3000, 9090, 3100, or 3200.
- Insufficient memory.
- Volume permission issue.

## Alloy Cannot Connect to otel-lgtm

Wait 60-90 seconds after startup, then check:

```sh
docker exec alloy wget -qO- http://otel-lgtm:9090/-/ready
```
