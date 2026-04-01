# Troubleshooting

## Metrics not appearing in Grafana

### 1. Check the router's metrics endpoint

From the monitoring host:

```sh
curl http://192.168.0.1:9100/metrics
```

If this fails:
- Is prometheus-node-exporter-lua running? `ssh root@192.168.0.1 "/etc/init.d/prometheus-node-exporter-lua status"`
- Is port 9100 blocked by the router firewall? Try from the router itself: `wget -qO- http://127.0.0.1:9100/metrics`
- Is the exporter still bound to loopback only? Check `uci get prometheus-node-exporter-lua.main.listen_interface` and make sure it is `lan`

### 1a. Check which collectors are actually succeeding

From the router or the monitoring host:

```sh
curl http://192.168.0.1:9100/metrics | grep '^node_scrape_collector_success'
```

You should see success entries for collectors such as `openwrt`, `wifi`, `wifi_stations`, `nat_traffic`, `netstat`, `uci_dhcp_host`, plus the bundled custom collectors `dnsmasq`, `device_status`, `packet_loss`, and `wan_info`.

If you enabled the newer helper scripts, you should also see the `textfile` collector succeed.

If a package is installed but a collector does not appear here, the exporter is not loading it successfully.

### 2. Check Alloy is scraping

Open the Alloy UI at http://localhost:12345 → Graph → look for `prometheus.scrape.openwrt`.

Or check Alloy logs:

```sh
docker logs alloy --tail 50 | grep -i "openwrt\|error\|scrape"
```

### 3. Check Prometheus received data

```sh
curl 'http://localhost:9090/api/v1/query?query=node_load1{job="openwrt"}' | python3 -m json.tool
```

If empty, Alloy isn't writing to Prometheus. Check the `prometheus.remote_write` target in Alloy UI.

### 3a. Check the custom dashboard metrics directly

The repo's dashboards also expect these router-side custom metrics:

```sh
curl http://192.168.0.1:9100/metrics | grep -E '^(router_device_up|dhcp_lease|packet_loss|wan_info|openwrt_service_up|openwrt_filesystem_used_percent|openwrt_wan_probe_latency_milliseconds|openwrt_dhcp_pool_size_total|openwrt_link_up|openwrt_softnet_dropped_total|openwrt_wan6_up|openwrt_filesystem_inode_used_percent|openwrt_firewall_chain_packets_total|openwrt_tc_available|openwrt_wifi_channel)'
```

If these are missing, you probably copied only `openwrt/setup.sh` instead of the whole `openwrt/` directory, or the helper cron jobs are not running.

### 4. Verify the ROUTER_IP env var reached Alloy

```sh
docker exec alloy env | grep ROUTER
```

---

## Logs not appearing in Grafana

### 1. Check if logd is sending syslog

On the router:

```sh
uci show system | grep log_
# Should show: system.@system[0].log_ip='192.168.0.100'
```

Force a log message and watch if it arrives:

```sh
# On the router:
logger "test message from openwrt"
```

### 2. Check Alloy is receiving syslog

```sh
docker logs alloy --tail 50 | grep -i "syslog\|514"
```

### 3. Check port 514 is accessible

```sh
# From the monitoring host (listening):
sudo tcpdump -i any port 514 -n

# From the router (sending):
logger "test"
```

If nothing arrives, check if something else is using port 514 on the host (`ss -lntup | grep 514`).

If running Linux with systemd-journald, port 514 may be in use by rsyslog or systemd-journal-remote.

Fix: Change `SYSLOG_PORT` in `.env` to e.g. `1514` and update the router's `log_port` UCI setting.

### 4. Check Loki received logs

```sh
curl 'http://localhost:3100/loki/api/v1/query?query={job="openwrt-syslog"}' | python3 -m json.tool
```

---

## Grafana shows "No data"

- Check the time range — set it to "Last 1 hour" and wait a scrape interval (30s)
- Verify datasource URLs in Grafana → Connections → Data Sources (should be `http://localhost:9090` etc.)
- Run a test query in Explore: `node_load1` in Prometheus, `{job="openwrt-syslog"}` in Loki

---

## Port 514 permission denied

On Linux, ports below 1024 require root or `CAP_NET_BIND_SERVICE`. Docker handles this for containers,
but if you see permission errors:

```sh
# Check if the container started on 514:
docker port alloy
```

Alternative: use port 1514 in `.env` and on the router.

## WiFi metrics missing

If the dashboards show no `wifi_*` metrics even though the packages are installed:

```sh
curl http://192.168.0.1:9100/metrics | grep '^node_scrape_collector_success{collector="wifi"'
curl http://192.168.0.1:9100/metrics | grep '^node_scrape_collector_success{collector="wifi_stations"'
curl http://192.168.0.1:9100/metrics | grep '^wifi_'
```

Common causes:

- The packages are not actually installed on the router build you are using
- The radio interface names differ from `phy0-ap0` and `phy1-ap0`
- The collector is installed but failing internally, so it never emits metrics

The first thing to trust is `node_scrape_collector_success`, not the package list alone.

## Custom helper metrics missing

The setup script installs helper scripts that populate:

- `/tmp/device-status.out`
- `/tmp/packetloss.out`
- `/tmp/wanip.out`
- `/var/prometheus/openwrt_filesystem.prom`
- `/var/prometheus/openwrt_services.prom`
- `/var/prometheus/openwrt_wan_quality.prom`

Check that the files exist and contain fresh data:

```sh
ls -l /tmp/device-status.out /tmp/packetloss.out /tmp/wanip.out /var/prometheus/*.prom
cat /tmp/packetloss.out
cat /tmp/wanip.out
cat /var/prometheus/openwrt_services.prom
```

Then check the cron file:

```sh
grep 'openwrt-monitor-' /etc/crontabs/root
```

And force a manual refresh:

```sh
/usr/bin/openwrt-monitor-device-status.sh
/usr/bin/openwrt-monitor-service-health.sh
/usr/bin/openwrt-monitor-packet-loss.sh
/usr/bin/openwrt-monitor-wan-info.sh
/usr/bin/openwrt-monitor-wan-quality.sh
/usr/bin/openwrt-monitor-filesystem.sh
```

If the files are present but Prometheus still does not show the metrics, verify that `prometheus-node-exporter-lua-textfile` is installed and that the exporter reports `node_scrape_collector_success{collector="textfile"} 1`.

---

## Dashboards not loading

Check provisioning loaded correctly:

```sh
docker logs otel-lgtm 2>&1 | grep -i "dashboard\|provision"
```

Grafana reads the provisioning directory on startup. If you added dashboards after starting,
restart the container:

```sh
docker compose restart otel-lgtm
```

---

## otel-lgtm container keeps restarting

Check logs:

```sh
docker logs otel-lgtm --tail 50
```

Common causes:
- Port conflict (something else on 3000, 9090, etc.) — check with `ss -tlnp`
- Insufficient memory — ensure at least 2 GB RAM available
- Volume permission issue — try `docker compose down -v && docker compose up -d`

---

## WAN throughput panel shows wrong interface

The default dashboard uses `wan`. Your router may expose a different logical or physical WAN interface.

Find your WAN interface:

```sh
ssh root@192.168.0.1 "ip route | grep default"
```

Then in Grafana, edit the WAN panels and replace `wan` with your interface name if your router exposes a different label.

---

## Alloy can't connect to otel-lgtm

If Alloy starts before otel-lgtm is ready, it will retry. The `depends_on` with healthcheck in
`docker-compose.yml` handles this, but the otel-lgtm healthcheck takes up to 60 seconds.

Just wait 60-90 seconds after `docker compose up -d` for everything to stabilize.

Check connectivity between containers:

```sh
docker exec alloy wget -qO- http://otel-lgtm:9090/-/ready
```
