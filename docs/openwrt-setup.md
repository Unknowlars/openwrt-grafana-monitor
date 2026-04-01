# OpenWRT Router Setup

This repo uses two kinds of router-side data:

- Official `prometheus-node-exporter-lua` collectors from OpenWRT packages
- Bundled custom collectors and helper scripts from this repo's `openwrt/` directory

The dashboards expect both. If you only install the official packages, Grafana will still show core system metrics, but panels such as WAN/public IP, packet loss, DHCP pool usage, ping-based device presence, WAN quality, filesystem/inode usage, link health, IPv6 WAN health, firewall counters, and service health will be empty.

## Requirements

- OpenWRT 21.02 or newer
- SSH access to the router
- Enough free flash for the exporter packages plus a few small helper scripts
- A monitoring host on the same LAN running this repo's Docker stack

## Recommended Setup

Copy the whole `openwrt/` directory, not just `setup.sh`:

```sh
scp -r openwrt root@192.168.0.1:/tmp/
ssh root@192.168.0.1 "sh /tmp/openwrt/setup.sh 192.168.0.100"
```

The setup script does all of the following:

- Installs the required exporter packages
- Installs `hwmon` and `thermal` collectors when available on your router build
- Installs the `textfile` collector used for custom script metrics
- Configures `prometheus-node-exporter-lua` to listen on `lan:9100`
- Copies bundled collectors into `/usr/lib/lua/prometheus-collectors/`
- Copies helper scripts into `/usr/bin/`
- Creates `/var/prometheus/` for textfile metrics
- Adds cron jobs for device status, WAN/public IP, packet loss, WAN quality, filesystem and inode usage, service health, DHCP pool, link health, softnet counters, IPv6 health, firewall counters, SQM, and WiFi radio state
- Configures remote syslog to the monitoring host over TCP port `514`

Bundled files installed by the script:

- `/usr/lib/lua/prometheus-collectors/dnsmasq.lua`
- `/usr/lib/lua/prometheus-collectors/device_status.lua`
- `/usr/lib/lua/prometheus-collectors/packet_loss.lua`
- `/usr/lib/lua/prometheus-collectors/wan_info.lua`
- `/usr/bin/openwrt-monitor-device-status.sh`
- `/usr/bin/openwrt-monitor-filesystem.sh`
- `/usr/bin/openwrt-monitor-packet-loss.sh`
- `/usr/bin/openwrt-monitor-service-health.sh`
- `/usr/bin/openwrt-monitor-wan-info.sh`
- `/usr/bin/openwrt-monitor-wan-quality.sh`
- `/usr/bin/openwrt-monitor-dhcp-pool.sh`
- `/usr/bin/openwrt-monitor-link-health.sh`
- `/usr/bin/openwrt-monitor-softnet.sh`
- `/usr/bin/openwrt-monitor-ipv6-health.sh`
- `/usr/bin/openwrt-monitor-inodes.sh`
- `/usr/bin/openwrt-monitor-firewall-counters.sh`
- `/usr/bin/openwrt-monitor-sqm.sh`
- `/usr/bin/openwrt-monitor-wifi-radio.sh`

## Required Packages

These are the packages the dashboards assume are present:

```sh
opkg update
opkg install \
  prometheus-node-exporter-lua \
  prometheus-node-exporter-lua-textfile \
  prometheus-node-exporter-lua-openwrt \
  prometheus-node-exporter-lua-uci_dhcp_host \
  prometheus-node-exporter-lua-wifi \
  prometheus-node-exporter-lua-wifi_stations \
  prometheus-node-exporter-lua-nat_traffic \
  prometheus-node-exporter-lua-netstat
```

Recommended when available:

```sh
opkg install \
  prometheus-node-exporter-lua-hwmon \
  prometheus-node-exporter-lua-thermal
```

## Optional Packages

These are useful depending on your router and feature set:

- `prometheus-node-exporter-lua-mwan3`: multi-WAN status
- `prometheus-node-exporter-lua-snmp6`: IPv6 stack counters
- `prometheus-node-exporter-lua-nft-counters`: nftables counters on newer OpenWRT releases
- `prometheus-node-exporter-lua-hostapd_ubus_stations`: extra WiFi client capability metadata
- `prometheus-node-exporter-lua-ethtool`: lower-level Ethernet/NIC stats
- `tc` (from `ip-full` on some builds): detailed SQM/qdisc counters used by `openwrt-monitor-sqm.sh`

## Manual Setup

Use the script if possible. Manual setup is mostly useful when you want to inspect or customize the router-side files.

### 1. Install exporter packages

Run the commands from the Required Packages section above.

### 2. Configure the exporter to listen on LAN

By default, the OpenWRT package usually listens on loopback only. Change it so the monitoring host can scrape it:

```sh
uci set prometheus-node-exporter-lua.main.listen_interface='lan'
uci set prometheus-node-exporter-lua.main.listen_port='9100'
uci commit prometheus-node-exporter-lua
```

### 3. Copy the bundled collectors and scripts

From your local machine:

```sh
scp openwrt/collectors/*.lua root@192.168.0.1:/usr/lib/lua/prometheus-collectors/
scp openwrt/scripts/*.sh root@192.168.0.1:/usr/bin/
ssh root@192.168.0.1 "chmod +x /usr/bin/openwrt-monitor-*.sh"
```

### 4. Add the helper cron jobs

On the router:

```sh
cat >> /etc/crontabs/root <<'EOF'
*/1 * * * * /usr/bin/openwrt-monitor-device-status.sh
*/1 * * * * /usr/bin/openwrt-monitor-service-health.sh
*/5 * * * * /usr/bin/openwrt-monitor-packet-loss.sh
*/5 * * * * /usr/bin/openwrt-monitor-wan-info.sh
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
EOF

/etc/init.d/cron enable
/etc/init.d/cron restart
```

Run the helper scripts once immediately so the custom metrics appear without waiting for cron:

```sh
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
```

### 5. Configure remote syslog

The monitoring stack listens on both UDP and TCP, but TCP is recommended for reliability:

```sh
uci set system.@system[0].log_ip=192.168.0.100
uci set system.@system[0].log_remote='1'
uci set system.@system[0].log_port=514
uci set system.@system[0].log_proto=tcp
uci set system.@system[0].log_hostname="$(uci get system.@system[0].hostname 2>/dev/null || echo openwrt)"
uci commit system
```

### 6. Restart services

```sh
/etc/init.d/prometheus-node-exporter-lua enable
/etc/init.d/prometheus-node-exporter-lua restart
/etc/init.d/log restart
```

## Verification

### On the router

Check the raw metrics endpoint:

```sh
wget -qO- http://127.0.0.1:9100/metrics | head -40
```

Verify the custom metrics exist:

```sh
wget -qO- http://127.0.0.1:9100/metrics | grep -E '^(router_device_up|dhcp_lease|packet_loss|wan_info|openwrt_service_up|openwrt_filesystem_used_percent|openwrt_wan_probe_latency_milliseconds|openwrt_dhcp_pool_size_total|openwrt_link_up|openwrt_softnet_dropped_total|openwrt_wan6_up|openwrt_filesystem_inode_used_percent|openwrt_firewall_chain_packets_total|openwrt_tc_available|openwrt_wifi_channel)'
```

Verify the exporter is scraping the collectors you expect:

```sh
wget -qO- http://127.0.0.1:9100/metrics | grep '^node_scrape_collector_success'
```

Healthy examples include collectors such as:

- `openwrt`
- `wifi`
- `wifi_stations`
- `nat_traffic`
- `netstat`
- `uci_dhcp_host`
- `dnsmasq`
- `device_status`
- `packet_loss`
- `wan_info`
- `textfile`

### On the monitoring host

```sh
curl http://192.168.0.1:9100/metrics | head -20
curl 'http://localhost:9090/api/v1/query?query=node_load1{job="openwrt"}'
curl 'http://localhost:3100/loki/api/v1/query?query={job="openwrt-syslog"}'
```

## Important Notes

- `router_device_up` is based on ICMP ping against DHCP leases. Some devices block ping and may appear offline even though they are connected.
- `wan_info` depends on the helper script reaching an external public-IP service. If that request fails, the panel will still show the local WAN IP and set the public IP label to `unknown`.
- The WAN quality metrics are synthetic probes run from the router itself. They are meant for trend and troubleshooting, not for precise SLA measurement.
- The filesystem and service-health metrics are exported via the textfile collector from files in `/var/prometheus/*.prom`.
- The newer helper scripts are also textfile metrics. They are safe to run even when optional tools are missing; affected scripts emit availability metrics such as `openwrt_tc_available` and `openwrt_wifi_radio_collector_available`.
- `wifi` and `wifi_stations` should expose `wifi_*` metrics automatically once the packages are installed. If they do not, check `node_scrape_collector_success` first.
- Temperature panels prefer `hwmon` and `thermal`. Some routers expose one, some both, some neither.

## Files This Repo Adds To The Router

These repo-local files are part of the supported setup and should be treated as part of the router install surface:

- `openwrt/collectors/dnsmasq.lua`
- `openwrt/collectors/device_status.lua`
- `openwrt/collectors/packet_loss.lua`
- `openwrt/collectors/wan_info.lua`
- `openwrt/scripts/openwrt-monitor-device-status.sh`
- `openwrt/scripts/openwrt-monitor-filesystem.sh`
- `openwrt/scripts/openwrt-monitor-packet-loss.sh`
- `openwrt/scripts/openwrt-monitor-service-health.sh`
- `openwrt/scripts/openwrt-monitor-wan-info.sh`
- `openwrt/scripts/openwrt-monitor-wan-quality.sh`
- `openwrt/scripts/openwrt-monitor-dhcp-pool.sh`
- `openwrt/scripts/openwrt-monitor-link-health.sh`
- `openwrt/scripts/openwrt-monitor-softnet.sh`
- `openwrt/scripts/openwrt-monitor-ipv6-health.sh`
- `openwrt/scripts/openwrt-monitor-inodes.sh`
- `openwrt/scripts/openwrt-monitor-firewall-counters.sh`
- `openwrt/scripts/openwrt-monitor-sqm.sh`
- `openwrt/scripts/openwrt-monitor-wifi-radio.sh`

If you skip these files, the dashboards will only be partially populated.
