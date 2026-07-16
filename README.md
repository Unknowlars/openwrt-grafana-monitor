# OpenWrt Grafana Monitor

Full observability stack for OpenWrt routers: metrics, logs, and dashboards in a single `docker compose up`.

**Stack**: [`grafana/otel-lgtm`](https://github.com/grafana/docker-otel-lgtm) (Grafana + Prometheus + Loki + Tempo) + Grafana Alloy.

## What You Get

| | |
|---|---|
| **CPU & memory** | Load average, memory usage, free memory |
| **System health** | CPU temperature, overlay flash usage, uptime, reboot count, file descriptors |
| **Network** | Per-interface RX/TX, WAN, LAN, WiFi AP, VPN, errors, drops, DNS probe, gateway packet loss |
| **Devices** | Online device count, DHCP lease table, WiFi client signal, NAT traffic top-10 |
| **NAT & firewall** | Active conntrack sessions, limit usage, optional named nftables counters, optional mwan3/IPv6/SQM rows |
| **Logs** | Syslog stream, DHCP messages, firewall drops, failed SSH logins, kernel messages |

4 pre-built dashboards: Overview, Network, Devices, Logs.

> Tested on ASUS RT-AX53U (MediaTek MT7621) with OpenWrt 24.10.3. Updated for OpenWrt 24.10/opkg and OpenWrt 25.12/apk compatibility.

## OpenWrt 24 vs 25

OpenWrt 24.10 uses `opkg`. OpenWrt 25.12 and newer use `apk`. The router setup script detects the available package manager and uses the right install commands automatically.

Do not use `apk upgrade` on OpenWrt. Use sysupgrade/attended sysupgrade for firmware upgrades.

## Prerequisites

- OpenWrt 24.10 or 25.12 router
- A Linux machine on the LAN for Docker
- Docker 24+ and Docker Compose v2

## Quick Start

### Step 1 - Router

Use the setup script from your workstation:

```sh
scp -O openwrt/setup.sh root@192.168.0.1:/tmp/
ssh root@192.168.0.1 "sh /tmp/setup.sh 192.168.0.100"
```

Replace `192.168.0.100` with the LAN IP of the monitoring host.
The `-O` flag forces legacy scp mode for OpenWrt/dropbear systems without an SFTP server.

The script:

- Installs `prometheus-node-exporter-lua` using `opkg` or `apk`.
- Enables official exporter collectors plus the textfile collector.
- Adds this repo's custom textfile metrics for DHCP leases, device status, WAN info, packet loss, DNS probe health, gateway health, overlay usage, DHCPv6 lease count, and public IP change events.
- Configures the exporter to listen on the LAN interface at `:9100`.
- Configures OpenWrt remote syslog to the monitoring host.

If you are migrating from an older router monitoring setup, review [Migrating From Older Router Scripts](docs/openwrt-setup.md#migrating-from-older-router-scripts). Setup auto-detects known old monitor cron jobs and prompts on interactive runs; use `CLEANUP_LEGACY_CRON=1` for unattended cleanup.

Manual package equivalents:

```sh
# OpenWrt 24.10
opkg update
opkg install prometheus-node-exporter-lua prometheus-node-exporter-lua-openwrt \
  prometheus-node-exporter-lua-nat_traffic prometheus-node-exporter-lua-netstat \
  prometheus-node-exporter-lua-textfile

# OpenWrt 25.12+
apk update
apk add prometheus-node-exporter-lua prometheus-node-exporter-lua-openwrt \
  prometheus-node-exporter-lua-nat_traffic prometheus-node-exporter-lua-netstat \
  prometheus-node-exporter-lua-textfile
```

The setup script also attempts optional collectors as best effort: WiFi AP/client metrics, hostapd station quality, thermal/hwmon temperature, nftables counters, and curated IPv6 counters via `snmp6`. If a package is unavailable on your OpenWrt feed, setup continues.

### Step 2 - Monitoring Host

```sh
git clone https://github.com/your-username/openwrt-grafana-monitor
cd openwrt-grafana-monitor
cp .env.example .env
# Edit .env: set ROUTER_IP and MONITORING_HOST_IP
docker compose up -d
```

### Step 3 - Open Grafana

Open **http://localhost:3000** and log in with `admin` / `changeme` unless you changed `GRAFANA_ADMIN_PASSWORD`.

## Configuration

All monitoring-host settings are in `.env`:

| Variable | Default | Description |
|---|---|---|
| `ROUTER_IP` | `192.168.0.1` | OpenWrt router IP |
| `ROUTER_NAME` | `openwrt` | Router label used in Prometheus, Loki, and Grafana |
| `ROUTER_METRICS_PORT` | `9100` | `prometheus-node-exporter-lua` port |
| `MONITORING_HOST_IP` | `192.168.0.100` | Host IP used by the router for syslog |
| `SCRAPE_INTERVAL` | `30s` | Metrics scrape interval |
| `GRAFANA_ADMIN_PASSWORD` | `changeme` | Grafana admin password |
| `SYSLOG_PORT` | `514` | Alloy syslog listener port |

Router setup accepts these optional environment variables:

```sh
EXPORTER_LISTEN_INTERFACE=lan
SYSLOG_PORT=514
PING_TARGET=1.1.1.1
DNS_PROBE_HOST=openwrt.org
DNS_PROBE_TIMEOUT=5
PUBLIC_IP_LOOKUP=0
PUBLIC_IP_URL=https://api.ipify.org
PUBLIC_IP_CHECK_INTERVAL=900
ENABLE_SQM_METRICS=0
SQM_INTERFACES=
CLEANUP_LEGACY_CRON=auto
```

## Architecture

```text
OpenWrt Router
├── prometheus-node-exporter-lua -> :9100/metrics
│   └── textfile collector -> /var/prometheus/openwrt-grafana-monitor.prom
└── logd remote syslog -> UDP :514
         |
         v
Monitoring Host (Docker)
├── Grafana Alloy
│   ├── scrapes :9100 -> Prometheus
│   └── receives syslog -> Loki
└── grafana/otel-lgtm
    ├── Prometheus :9090
    ├── Loki       :3100
    ├── Tempo      :3200
    └── Grafana    :3000
```

## Docs

- [OpenWrt setup guide](docs/openwrt-setup.md)
- [Monitoring host setup](docs/monitoring-host-setup.md)
- [Troubleshooting](docs/troubleshooting.md)

## Adapting to Your Router

Dashboards have Grafana variables for router name, WAN interface, WiFi interfaces, and VPN interface. Change them in the dashboard variable controls first. If you want different defaults, edit `build_dashboards.py` and run:

```sh
python3 build_dashboards.py
```
