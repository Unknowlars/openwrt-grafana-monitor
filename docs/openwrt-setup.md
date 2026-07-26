# OpenWrt Router Setup

This repo uses two kinds of router-side data:

- Official `prometheus-node-exporter-lua` collectors from OpenWrt packages
- Bundled custom collectors and helper scripts from this repo's `openwrt/` directory

The dashboards expect both. If you only install the official packages, Grafana will still show core system metrics, but panels such as WAN/public IP, packet loss, DHCP pool usage, ping-based device presence, WAN quality, filesystem/inode usage, link health, IPv6 WAN health, firewall counters, and service health will be empty.

## Requirements

- OpenWrt 24.10/opkg or OpenWrt 25.12/apk
- SSH access to the router
- Enough free flash for the exporter packages plus a few small helper scripts
- A monitoring host on the same LAN running this repo's Docker stack

## Recommended Setup

Copy the whole `openwrt/` directory, not just `setup.sh`:

```sh
scp -O -r openwrt root@192.168.0.1:/tmp/
ssh root@192.168.0.1 "sh /tmp/openwrt/setup.sh 192.168.0.100"
```

The `-O` flag forces legacy scp mode for OpenWrt/dropbear systems without an SFTP server.

The setup script does all of the following:

- Detects `opkg` or `apk` and installs the required exporter packages
- Installs optional collectors when available on your router build
- Installs the `textfile` collector used for custom script metrics
- Configures `prometheus-node-exporter-lua` to listen on `lan:9100`
- Copies bundled collectors into `/usr/lib/lua/prometheus-collectors/`
- Copies helper scripts into `/usr/bin/`
- Creates `/var/prometheus/` for textfile metrics
- Adds cron jobs for device status, WAN/public IP, packet loss, WAN quality, filesystem and inode usage, service health, DHCP pool, link health, softnet counters, IPv6 health, firewall counters, SQM, and WiFi radio state
- Configures remote syslog to the monitoring host over UDP port `514` by default

The default profile is `core`. Optional `traffic`, `wifi_mesh`, `dpi`, and
`full` profiles are documented in [Advanced Router Profiles](advanced-profiles.md).
Use a profile when running the setup command, for example:

```sh
OPENWRT_MONITOR_PROFILE=full sh /tmp/openwrt/setup.sh 192.168.0.100
```

Bundled files installed by the script:

- `/usr/lib/lua/prometheus-collectors/dnsmasq.lua`
- `/usr/lib/lua/prometheus-collectors/device_status.lua`
- `/usr/lib/lua/prometheus-collectors/packet_loss.lua`
- `/usr/lib/lua/prometheus-collectors/wan_info.lua`
- `/usr/lib/lua/prometheus-collectors/device_traffic.lua` when `traffic` or `full` is selected
- `/usr/lib/lua/prometheus-collectors/wifi_dethrash.lua` when `wifi_mesh` or `full` is selected
- `/usr/lib/lua/prometheus-collectors/dpi_netifyd.lua` when `dpi` or `full` is selected
- `/usr/lib/lua/prometheus-collectors/client_inventory.lua` and
  `/usr/lib/lua/prometheus-collectors/topology.lua` when `clients` or `full` is selected
- `/usr/lib/lua/openwrt_oui.lua` and `/usr/lib/lua/openwrt_oui_data.lua` when
  `clients` or `full` is selected. These are `require`-able modules, not
  collectors, and must **not** be placed in `prometheus-collectors/`: the
  exporter loads every file there as a collector and calls `scrape()` on it.
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
# OpenWrt 24.10
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

On OpenWrt 25.12 and newer, use the same package names with `apk`:

```sh
apk update
apk add \
  prometheus-node-exporter-lua \
  prometheus-node-exporter-lua-textfile \
  prometheus-node-exporter-lua-openwrt \
  prometheus-node-exporter-lua-uci_dhcp_host \
  prometheus-node-exporter-lua-nat_traffic \
  prometheus-node-exporter-lua-netstat
```

Do not use `apk upgrade` on OpenWrt; use sysupgrade/attended sysupgrade for firmware upgrades.

## Optional Packages

These are useful depending on your router and feature set:

- `prometheus-node-exporter-lua-wifi`: AP-level WiFi metrics
- `prometheus-node-exporter-lua-wifi_stations`: WiFi client metrics
- `prometheus-node-exporter-lua-hostapd_stations`: hostapd WiFi client metrics when available
- `prometheus-node-exporter-lua-hwmon`: hardware temperature sensors
- `prometheus-node-exporter-lua-thermal`: thermal zone sensors
- `prometheus-node-exporter-lua-mwan3`: multi-WAN status
- `prometheus-node-exporter-lua-snmp6`: IPv6 stack counters
- `prometheus-node-exporter-lua-nft-counters`: nftables counters on newer OpenWrt releases
- `lua-cjson`: JSON parsing for the traffic and DPI profiles
- `nftables-json`: JSON output support for router-local nftables set inspection
- `conntrack`: per-client conntrack CLI on OpenWrt 25.12/apk; OpenWrt 24.10/opkg
  may use `conntrack-tools`, with a fallback to `conntrack`
- `netifyd`: optional DPI engine used by the `dpi` profile
- `prometheus-node-exporter-lua-ethtool`: lower-level Ethernet/NIC stats
- `tc` (from `ip-full` on some builds): detailed SQM/qdisc counters used by `openwrt-monitor-sqm.sh`

## Setup Options

`openwrt/setup.sh` accepts these optional environment variables:

- `EXPORTER_LISTEN_INTERFACE`: interface for port 9100; default `lan`
- `SYSLOG_PORT`: remote syslog port; default `514`
- `SYSLOG_PROTO`: remote syslog protocol; default `udp`
- `PING_TARGET`: packet-loss and WAN internet probe target; default `1.1.1.1`
- `DNS_PROBE_HOST`: DNS resolution probe host; default `openwrt.org`
- `DNS_PROBE_TIMEOUT`: DNS probe ping fallback timeout in seconds; default `5`
- `OPENWRT_MONITOR_PROFILE`: `core`, `traffic`, `wifi_mesh`, `dpi`, `clients`, `netflow`, or `full`; also accepts a comma-separated list of the non-`full` names; default `core`
- `TRAFFIC_LAN_INTERFACE`: LAN bridge counted by the traffic profile; default `br-lan`

NetFlow profile only (see [netflow-akvorado.md](netflow-akvorado.md) before enabling):

- `NETFLOW_INTERFACES`: devices softflowd captures on, space- or comma-separated; default `br-lan`. The setup script reads each device's ifIndex and passes `ifindex:interface` to softflowd so Akvorado can resolve input/output interfaces
- `NETFLOW_PORT`: Akvorado inlet UDP port; default `2055`. Must match `NETFLOW_PORT` in `.env`
- `NETFLOW_SAMPLING_RATE`: softflowd `-s`; default `1` (every packet). The stock OpenWrt default is `100`; if you raise this, raise `default-sampling-rate` in `akvorado/akvorado.yaml` to match or byte counts read low by exactly that factor
- `NETFLOW_TIMEOUTS`: softflowd `-t`; default `maxlife=60`. softflowd's own defaults are tcp/general 1h and maxlife **one week**, and a flow is only exported when it expires — so without this an ongoing transfer shows nothing until it ends, then lands as one spike at expiry time. The stock init script maps exactly one `-t`
- `NETFLOW_MAX_FLOWS`: softflowd flow-table cap; default `8192`. Overflow force-expires flows and truncates byte counts silently
- `NETFLOW_DISABLE_HW_OFFLOAD`: `0` or `1`; default `0`. softflowd captures via libpcap, so traffic forwarded by the switch ASIC under hardware flow offload is invisible to it. `1` turns offload off for complete accounting at the cost of routing throughput

## Manual Setup

Use the script if possible. Manual setup is mostly useful when you want to inspect or customize the router-side files.

### 1. Install exporter packages

Run the commands from the Required Packages section above.

### 2. Configure the exporter to listen on LAN

By default, the OpenWrt package usually listens on loopback only. Change it so the monitoring host can scrape it:

```sh
uci set prometheus-node-exporter-lua.main.listen_interface='lan'
uci set prometheus-node-exporter-lua.main.listen_port='9100'
uci commit prometheus-node-exporter-lua
```

### 3. Copy the bundled collectors and scripts

From your local machine:

```sh
scp -O openwrt/collectors/*.lua root@192.168.0.1:/usr/lib/lua/prometheus-collectors/
scp -O openwrt/scripts/*.sh root@192.168.0.1:/usr/bin/
ssh root@192.168.0.1 "chmod +x /usr/bin/openwrt-monitor-*.sh"

# Shared modules go one directory up, under their installed names. Anything
# left in prometheus-collectors/ is loaded as a collector and would fail with
# "attempt to call field 'scrape' (a nil value)".
scp -O openwrt/lua/oui.lua root@192.168.0.1:/usr/lib/lua/openwrt_oui.lua
scp -O openwrt/lua/oui_data.lua root@192.168.0.1:/usr/lib/lua/openwrt_oui_data.lua
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

The monitoring stack listens on both UDP and TCP. The setup script defaults to UDP, which matches OpenWrt syslog and the Kubernetes examples:

```sh
uci set system.@system[0].log_ip=192.168.0.100
uci set system.@system[0].log_remote='1'
uci set system.@system[0].log_port=514
uci set system.@system[0].log_proto=udp
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
LAN_IP="$(uci get network.lan.ipaddr 2>/dev/null)"
wget -qO- "http://$LAN_IP:9100/metrics" | head -40
```

Verify the custom metrics exist:

```sh
wget -qO- "http://$LAN_IP:9100/metrics" | grep -E '^(router_device_up|dhcp_lease|packet_loss|wan_info|openwrt_service_up|openwrt_filesystem_used_percent|openwrt_wan_probe_latency_milliseconds|openwrt_dhcp_pool_size_total|openwrt_link_up|openwrt_softnet_dropped_total|openwrt_wan6_up|openwrt_filesystem_inode_used_percent|openwrt_firewall_chain_packets_total|openwrt_tc_available|openwrt_wifi_channel|openwrt_wifi_station_connected_seconds|dns_probe_success|gateway_packet_loss|overlay_bytes_total|wan_public_ip_changed|dhcpv6_lease_count|sqm_backlog_bytes)'
```

Verify the exporter is scraping the collectors you expect:

```sh
wget -qO- "http://$LAN_IP:9100/metrics" | grep '^node_scrape_collector_success'
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
- Optional profile collectors emit `openwrt_device_traffic_collector_available`, `openwrt_wifi_mesh_collector_available`, and `openwrt_dpi_collector_available`. A value of `0` means unavailable or not installed; it is not a healthy zero.
- `wifi` and `wifi_stations` should expose `wifi_*` metrics automatically once the packages are installed. If they do not, check `node_scrape_collector_success` first.
- Temperature panels prefer `hwmon` and `thermal`. Some routers expose one, some both, some neither.

## Files This Repo Adds To The Router

These repo-local files are part of the supported setup and should be treated as part of the router install surface:

- `openwrt/collectors/dnsmasq.lua`
- `openwrt/collectors/device_status.lua`
- `openwrt/collectors/packet_loss.lua`
- `openwrt/collectors/wan_info.lua`
- `openwrt/collectors/device_traffic.lua`
- `openwrt/collectors/wifi_dethrash.lua`
- `openwrt/collectors/dpi_netifyd.lua`
- `openwrt/collectors/client_inventory.lua`
- `openwrt/collectors/topology.lua`
- `openwrt/lua/oui.lua` and `openwrt/lua/oui_data.lua` (installed to
  `/usr/lib/lua/` as `openwrt_oui.lua` / `openwrt_oui_data.lua`)
- `openwrt/netflow/softflowd.config` (rendered to `/etc/config/softflowd`)
- `openwrt/scripts/openwrt-monitor-device-status.sh`
- `openwrt/scripts/openwrt-monitor-filesystem.sh`
- `openwrt/scripts/openwrt-monitor-packet-loss.sh`
- `openwrt/scripts/openwrt-monitor-service-health.sh`
- `openwrt/scripts/openwrt-monitor-wan-info.sh`
- `openwrt/scripts/openwrt-monitor-wan-quality.sh`
- `openwrt/scripts/openwrt-monitor-dhcp-pool.sh`
- `openwrt/scripts/openwrt-monitor-netflow-health.sh`
- `openwrt/scripts/openwrt-monitor-link-health.sh`
- `openwrt/scripts/openwrt-monitor-softnet.sh`
- `openwrt/scripts/openwrt-monitor-ipv6-health.sh`
- `openwrt/scripts/openwrt-monitor-inodes.sh`
- `openwrt/scripts/openwrt-monitor-firewall-counters.sh`
- `openwrt/scripts/openwrt-monitor-sqm.sh`
- `openwrt/scripts/openwrt-monitor-wifi-radio.sh`

If you skip these files, the dashboards will only be partially populated.
