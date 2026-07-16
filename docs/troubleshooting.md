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
wget -qO- http://127.0.0.1:9100/metrics | head
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
wget -qO- http://127.0.0.1:9100/metrics | grep -E 'node_textfile|dhcp_lease|router_device_up|wan_info|packet_loss|overlay_bytes|gateway_packet_loss|wan_public_ip_changed|dhcpv6_lease_count'
```

If `node_textfile_mtime_seconds` is missing, install the textfile collector package:

```sh
# OpenWrt 24.10
opkg install prometheus-node-exporter-lua-textfile

# OpenWrt 25.12+
apk add prometheus-node-exporter-lua-textfile
```

### 3. Check optional collectors

Panels for WiFi client signal, temperature, and nftables counters depend on optional packages. Missing metrics usually means the package is unavailable on your feed or the device does not expose that data.

```sh
wget -qO- http://127.0.0.1:9100/metrics | grep -E 'hostapd_station|node_thermal_zone_temp|node_hwmon_temp_celsius|nft_counter'
```

If the package is missing, install the collector that matches the panel:

```sh
# OpenWrt 24.10
opkg install prometheus-node-exporter-lua-hostapd_stations prometheus-node-exporter-lua-thermal prometheus-node-exporter-lua-hwmon prometheus-node-exporter-lua-nft-counters

# OpenWrt 25.12+
apk add prometheus-node-exporter-lua-hostapd_stations prometheus-node-exporter-lua-thermal prometheus-node-exporter-lua-hwmon prometheus-node-exporter-lua-nft-counters
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
