# OpenWRT Full Monitoring Stack — Implementation Plan

> **Goal**: A self-contained, easy-to-replicate monitoring solution for OpenWRT routers
> using the Grafana LGTM stack (`grafana/otel-lgtm`) with full metrics, logging, and dashboards.

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Component Decisions & Rationale](#2-component-decisions--rationale)
3. [What We Can Monitor](#3-what-we-can-monitor)
4. [Repo Structure](#4-repo-structure)
5. [Phase 1 — OpenWRT Side Setup](#5-phase-1--openwrt-side-setup)
6. [Phase 2 — Monitoring Host Setup](#6-phase-2--monitoring-host-setup)
7. [Phase 3 — Dashboards & Alerts](#7-phase-3--dashboards--alerts)
8. [Phase 4 — Documentation & UX](#8-phase-4--documentation--ux)
9. [Future / Stretch Goals](#9-future--stretch-goals)
10. [Research Notes & Sources](#10-research-notes--sources)

---

## 1. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│  OpenWRT Router (192.168.0.1)                                   │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  prometheus-node-exporter-lua  :9100/metrics             │  │
│  │  ├── CPU, memory, load average                           │  │
│  │  ├── Network interfaces (RX/TX bytes, packets, errors)   │  │
│  │  ├── WiFi clients & signal quality                       │  │
│  │  ├── NAT/conntrack traffic                               │  │
│  │  ├── DHCP leases                                         │  │
│  │  ├── Temperature sensors                                 │  │
│  │  └── OpenWRT-specific system info                        │  │
│  │                                                          │  │
│  │  logd (syslog)  → remote syslog UDP/TCP :514            │  │
│  │  ├── System events                                       │  │
│  │  ├── Kernel messages                                     │  │
│  │  ├── DHCP assignments                                    │  │
│  │  ├── Firewall drops                                      │  │
│  │  └── Service restarts                                    │  │
│  └──────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                          │ HTTP scrape :9100
                          │ Syslog UDP :514
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│  Monitoring Host (Linux PC / server / Raspberry Pi / VM)        │
│                                                                 │
│  ┌──────────────────────────────────────┐                       │
│  │  Grafana Alloy (collector agent)     │                       │
│  │  ├── Scrapes :9100/metrics (Prometheus receiver)            │  │
│  │  ├── Listens for syslog on :514 (Loki syslog receiver)     │  │
│  │  ├── Adds labels (router=openwrt, instance=192.168.0.1)    │  │
│  │  ├── Forwards metrics → Mimir/Prometheus                   │  │
│  │  └── Forwards logs → Loki                                  │  │
│  └──────────────────────┬───────────────┘                       │
│                         │                                       │
│  ┌──────────────────────▼───────────────┐                       │
│  │  grafana/otel-lgtm (Docker)          │                       │
│  │  ├── Prometheus  :9090  (metrics DB) │                       │
│  │  ├── Loki        :3100  (logs DB)    │                       │
│  │  ├── Tempo       :3200  (traces)     │                       │
│  │  ├── Pyroscope   :3500  (profiling)  │                       │
│  │  ├── OTel Collector :4317/:4318      │                       │
│  │  └── Grafana     :3000  (UI)         │                       │
│  └──────────────────────────────────────┘                       │
└─────────────────────────────────────────────────────────────────┘
                          │
                          ▼
                   http://localhost:3000
                   (Grafana dashboards)
```

### Signal Flow Summary

| Signal | Source | Collector | Destination |
|--------|--------|-----------|-------------|
| Metrics | prometheus-node-exporter-lua | Alloy `prometheus.scrape` | Prometheus in otel-lgtm |
| Logs | logd remote syslog | Alloy `loki.source.syslog` | Loki in otel-lgtm |
| Dashboards | — | — | Grafana in otel-lgtm |

---

## 2. Component Decisions & Rationale

### On the Router

#### Metrics: `prometheus-node-exporter-lua` (chosen)

The clear winner for OpenWRT metrics collection. Reasons:
- Actively maintained in the official OpenWRT package feed (updated 2025-11-22)
- Lua-based = tiny footprint, no extra runtime needed
- Modular: install only the collectors you need
- Exposes standard Prometheus text format at `:9100/metrics`
- Has OpenWRT-specific collectors (not available in standard node_exporter)

**Alternative considered**: `collectd` with `write_prometheus` plugin — more complex to configure,
heavier dependency chain (libprotobuf-c, libmicrohttpd). Skip unless you already use collectd.

**Rejected**: Running Grafana Alloy on the router — not officially supported on MIPS/ARM OpenWRT
architectures (tracked in grafana/alloy#2460). Likely never will be due to Go binary size.

#### Logging: `logd` remote syslog (built-in)

OpenWRT's built-in `logd` supports remote syslog natively. Zero installation needed.
Just configure a `log_ip` pointing at our Alloy syslog receiver.

**Alternative considered**: `openwrt-loki-exporter` (shell script that pipes `logread` to Loki via curl).
Works but fragile, polling-based, not a clean stream. The syslog approach is more reliable.

### On the Monitoring Host

#### Collector: Grafana Alloy

Single agent that handles both Prometheus scraping and syslog ingestion and forwards to
the LGTM stack. Replaces the need for separate Promtail + separate scrape job.

- `prometheus.scrape` → scrapes router `:9100/metrics` on interval
- `loki.source.syslog` → listens for RFC5424 syslog from router logd
- `prometheus.remote_write` → pushes to Prometheus in otel-lgtm
- `loki.write` → pushes to Loki in otel-lgtm

#### Backend: `grafana/otel-lgtm`

All-in-one Docker image containing: Grafana + Prometheus + Loki + Tempo + Pyroscope + OTel Collector.
Perfect for home/self-hosted use. Not HA, not production-scale — but ideal for personal monitoring.

---

## 3. What We Can Monitor

### Metrics (via prometheus-node-exporter-lua modules)

| Module | Package | Metrics |
|--------|---------|---------|
| Base system | `prometheus-node-exporter-lua` | CPU, memory, load, uptime, filesystem |
| OpenWRT specifics | `prometheus-node-exporter-lua-openwrt` | Firmware version, board info |
| WiFi stats | `prometheus-node-exporter-lua-wifi` | SSID, channel, signal, noise, bitrate |
| WiFi clients | `prometheus-node-exporter-lua-wifi_stations` | Connected clients, MAC, RSSI per client |
| Network interfaces | `prometheus-node-exporter-lua-netstat` | TCP/UDP connections, socket stats |
| NAT traffic | `prometheus-node-exporter-lua-nat_traffic` | NAT conntrack sessions, bytes |
| MWAN3 | `prometheus-node-exporter-lua-mwan3` | Multi-WAN failover status (if using mwan3) |

**Key dashboards we'll build around**:
- WAN interface RX/TX throughput (bytes/sec)
- LAN interface traffic
- WiFi client count over time
- Memory usage % (routers often run tight)
- CPU load average
- Uptime / reboots
- Active NAT sessions count
- DHCP lease count

### Logs (via logd remote syslog → Loki)

- System events (startup, shutdown, service restarts)
- DHCP lease assignments (`dnsmasq: DHCPACK` lines)
- Firewall drops (if logging enabled in `/etc/config/firewall`)
- Kernel messages (interface state changes, OOM, etc.)
- OpenVPN / WireGuard connection events (if running VPN)
- PPPoE/VLAN connect/disconnect events

**Loki queries we'll pre-build**:
- Count of firewall drops over time
- DHCP lease history per MAC/hostname
- System error events

---

## 4. Repo Structure

```
openwrt-grafana-monitor/
│
├── README.md                    # Quick start + overview
├── PLAN.md                      # This document
│
├── docker-compose.yml           # Main stack: otel-lgtm + Alloy
│
├── alloy/
│   └── config.alloy             # Alloy config (scrape + syslog + forward)
│
├── otel-lgtm/
│   └── otelcol-config.yaml      # Optional: custom OTel Collector config
│
├── grafana/
│   ├── provisioning/
│   │   ├── dashboards/
│   │   │   ├── dashboards.yaml  # Dashboard provider config
│   │   │   ├── openwrt-overview.json
│   │   │   ├── openwrt-wifi.json
│   │   │   ├── openwrt-logs.json
│   │   │   └── openwrt-network.json
│   │   └── datasources/
│   │       └── datasources.yaml # Pre-configured data sources
│
├── openwrt/
│   ├── setup.sh                 # Script to run ON the router (opkg installs)
│   └── configs/
│       ├── uhttpd-prometheus     # uhttpd config snippet for metrics endpoint
│       └── system-logging        # UCI config snippet for remote syslog
│
└── docs/
    ├── openwrt-setup.md         # Detailed router setup guide
    ├── monitoring-host-setup.md # Detailed host setup guide
    ├── dashboards.md            # Dashboard guide and screenshots
    ├── metrics-reference.md     # All metrics explained
    └── troubleshooting.md       # Common issues and fixes
```

---

## 5. Phase 1 — OpenWRT Side Setup

### 5.1 Install prometheus-node-exporter-lua

SSH into the router and run:

```sh
opkg update
opkg install \
  prometheus-node-exporter-lua \
  prometheus-node-exporter-lua-openwrt \
  prometheus-node-exporter-lua-wifi \
  prometheus-node-exporter-lua-wifi_stations \
  prometheus-node-exporter-lua-nat_traffic \
  prometheus-node-exporter-lua-netstat
```

The exporter starts automatically and listens on `:9100`. Verify:

```sh
curl http://192.168.0.1:9100/metrics | head -30
```

> **Note**: The exporter uses `uhttpd` (OpenWRT's built-in web server) or runs its own
> minimal HTTP listener. Check which port is active with `netstat -tlnp | grep 9100`.

### 5.2 Configure Remote Syslog (Logging)

Edit `/etc/config/system` or use UCI:

```sh
uci set system.@system[0].log_ip=<MONITORING_HOST_IP>
uci set system.@system[0].log_port=514
uci set system.@system[0].log_proto=udp
uci set system.@system[0].log_hostname=openwrt
uci commit system
/etc/init.d/log restart
```

Replace `<MONITORING_HOST_IP>` with the IP of the machine running Docker.

### 5.3 (Optional) Enable Firewall Logging

To get firewall drop logs in Loki:

```sh
uci set firewall.@defaults[0].drop_invalid=1
# Or add logging to specific rules in /etc/config/firewall:
# option log 1
uci commit firewall
/etc/init.d/firewall restart
```

### 5.4 (Optional) Enable MWAN3 Metrics

If using mwan3 for multi-WAN:

```sh
opkg install prometheus-node-exporter-lua-mwan3
```

### 5.5 Firewall — Open Port 9100 for Scraping

By default OpenWRT blocks WAN→LAN access but LAN access should work. If your monitoring host
is on the LAN, no firewall changes needed. If scraping from a different network:

```sh
uci add firewall rule
uci set firewall.@rule[-1].name='Allow-Prometheus-Scrape'
uci set firewall.@rule[-1].src='lan'
uci set firewall.@rule[-1].dest_port='9100'
uci set firewall.@rule[-1].proto='tcp'
uci set firewall.@rule[-1].target='ACCEPT'
uci commit firewall
/etc/init.d/firewall restart
```

---

## 6. Phase 2 — Monitoring Host Setup

### 6.1 Prerequisites

- Docker + Docker Compose installed
- Ports 3000, 4317, 4318, 9090, 3100, 514 available
- The monitoring host must be reachable from the router (same LAN or routed)

### 6.2 docker-compose.yml

```yaml
services:
  otel-lgtm:
    image: grafana/otel-lgtm:latest
    container_name: otel-lgtm
    ports:
      - "3000:3000"    # Grafana UI
      - "4317:4317"    # OTLP gRPC
      - "4318:4318"    # OTLP HTTP
      - "9090:9090"    # Prometheus
      - "3100:3100"    # Loki
      - "3200:3200"    # Tempo
    volumes:
      - lgtm-data:/data
      - ./grafana/provisioning:/etc/grafana/provisioning:ro
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=changeme
      - GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH=/etc/grafana/provisioning/dashboards/openwrt-overview.json
    restart: unless-stopped

  alloy:
    image: grafana/alloy:latest
    container_name: alloy
    ports:
      - "12345:12345"  # Alloy UI
      - "514:514/udp"  # Syslog receiver (UDP)
      - "514:514/tcp"  # Syslog receiver (TCP)
    volumes:
      - ./alloy/config.alloy:/etc/alloy/config.alloy:ro
    command: run /etc/alloy/config.alloy
    depends_on:
      - otel-lgtm
    restart: unless-stopped

volumes:
  lgtm-data:
```

### 6.3 Alloy Configuration (`alloy/config.alloy`)

```alloy
// ─── Scrape OpenWRT metrics ───────────────────────────────────────────────

prometheus.scrape "openwrt" {
  targets = [
    {__address__ = "192.168.0.1:9100", router = "openwrt"},
  ]
  scrape_interval = "30s"
  metrics_path    = "/metrics"

  forward_to = [prometheus.remote_write.lgtm.receiver]
}

prometheus.remote_write "lgtm" {
  endpoint {
    url = "http://otel-lgtm:9090/api/v1/write"
  }
}

// ─── Collect OpenWRT syslog ───────────────────────────────────────────────

loki.source.syslog "openwrt" {
  listen_address = "0.0.0.0:514"
  listener {
    protocol = "udp"
    labels   = { job = "openwrt-syslog", router = "openwrt" }
  }
  listener {
    protocol = "tcp"
    labels   = { job = "openwrt-syslog", router = "openwrt" }
  }

  forward_to = [loki.write.lgtm.receiver]
}

loki.write "lgtm" {
  endpoint {
    url = "http://otel-lgtm:3100/loki/api/v1/push"
  }
}
```

> **Multi-router setup**: Add additional targets to `prometheus.scrape` and additional
> `loki.source.syslog` listeners with different port numbers and labels per router.

### 6.4 Grafana Data Source Provisioning (`grafana/provisioning/datasources/datasources.yaml`)

```yaml
apiVersion: 1
datasources:
  - name: Prometheus
    type: prometheus
    url: http://localhost:9090
    isDefault: true
    access: proxy

  - name: Loki
    type: loki
    url: http://localhost:3100
    access: proxy

  - name: Tempo
    type: tempo
    url: http://localhost:3200
    access: proxy
```

> The otel-lgtm image pre-configures these data sources internally. This provisioning
> file ensures they're available even if container state is wiped.

### 6.5 Starting the Stack

```sh
git clone https://github.com/your-username/openwrt-grafana-monitor
cd openwrt-grafana-monitor

# Edit alloy/config.alloy to set your router's IP
# Edit docker-compose.yml if needed

docker compose up -d
```

Open Grafana at `http://localhost:3000` (admin / changeme).

---

## 7. Phase 3 — Dashboards & Alerts

### 7.1 Dashboard Plan

We'll build and commit 4 dashboard JSON files (exported from Grafana):

#### `openwrt-overview.json` — Main Overview
- Router uptime panel
- Memory used % gauge
- CPU load average (1m, 5m, 15m) time series
- WAN RX/TX throughput time series
- Active NAT sessions stat panel
- Connected WiFi clients count

#### `openwrt-network.json` — Network Deep Dive
- Per-interface RX/TX bytes/sec (all interfaces)
- Per-interface error/drop rates
- NAT conntrack table usage
- Network interface link state (up/down)

#### `openwrt-wifi.json` — WiFi Monitor
- Connected client count over time
- Per-client RSSI heatmap or table
- WiFi channel utilization
- TX/RX rates per client

#### `openwrt-logs.json` — Log Explorer
- Loki log panel (filterable by severity, service)
- Firewall drop count over time (derived from logs)
- DHCP event timeline
- Error/warning count stat panels

### 7.2 Alerting (Optional, Phase 3+)

Grafana built-in alerting (available in otel-lgtm) for:
- Router memory > 90% for 5 minutes
- WAN interface down (no metrics received)
- Unusual number of firewall drops
- Router unreachable (scrape failures)

Alert delivery via: Email, Slack webhook, ntfy.sh, Telegram, etc.

---

## 8. Phase 4 — Documentation & UX

### 8.1 README.md

Clean, friendly README with:
- What this is (one paragraph)
- Prerequisites (Docker, OpenWRT router)
- 3-step quick start
- Screenshot of Grafana dashboard
- Link to detailed docs

### 8.2 Detailed Docs (`docs/`)

- `openwrt-setup.md` — Step-by-step router setup with screenshots of LuCI
- `monitoring-host-setup.md` — Docker setup, environment variables, port reference
- `metrics-reference.md` — Every metric name, its meaning, typical values
- `dashboards.md` — Dashboard guide, how to customize
- `troubleshooting.md` — "Metrics not appearing", "Logs not arriving", etc.

### 8.3 Environment Variable Configuration

Use a `.env` file pattern so users don't have to edit YAML:

```env
ROUTER_IP=192.168.0.1
ROUTER_NAME=openwrt
SCRAPE_INTERVAL=30s
GRAFANA_PASSWORD=changeme
MONITORING_HOST_IP=192.168.0.100
```

---

## 9. Future / Stretch Goals

These are out of scope for the initial implementation but worth tracking:

### Multi-Router Support
- Alloy config template for N routers
- Dashboard variable for selecting router
- Label strategy: `router=home`, `router=office`, etc.

### Ping / Reachability Monitoring
- Use Alloy's `prometheus.exporter.blackbox` to ping WAN gateway, DNS, and external hosts
- Detect internet outages from the monitoring host perspective

### SNMP Fallback
- Some routers expose SNMP even without OpenWRT packages
- Alloy has a built-in `prometheus.exporter.snmp` component
- Could supplement prometheus-node-exporter-lua for certain metrics

### Custom Lua Collectors
- Write additional `.lua` files for metrics not covered by official modules
- Examples: OpenVPN tunnel stats, WireGuard peers, adblock statistics

### Bandwidth Usage Per Client (DHCP-based)
- Combine DHCP lease data (hostname → IP) with conntrack/nftables flow data
- Show per-device bandwidth usage

### Notifications
- Configure Grafana contact points for alerting
- Pre-built alert rules committed to the repo

### Kubernetes / Helm Chart
- For users running the monitoring stack in k8s

---

## 10. Research Notes & Sources

### Key Findings

1. **`prometheus-node-exporter-lua` is the right choice for OpenWRT metrics.**
   It's in the official feed, actively maintained, tiny, and has all the modules we need.
   See: https://github.com/openwrt/packages/tree/master/utils/prometheus-node-exporter-lua

2. **Grafana Alloy cannot run on OpenWRT** (MIPS/ARM unsupported).
   Issue: https://github.com/grafana/alloy/issues/2460
   Solution: Run Alloy on the monitoring host and scrape the router remotely.

3. **`logd` built-in remote syslog is the simplest logging solution.**
   No packages to install on the router. Just a UCI config change.
   Alloy's `loki.source.syslog` receives it directly.

4. **`grafana/otel-lgtm` is not for production but perfect for home use.**
   Single container, all components bundled, data persistence via `/data` volume.
   Grafana is pre-configured with Prometheus, Loki, Tempo data sources.

5. **`collectd` with `write_prometheus` is a viable alternative to prometheus-node-exporter-lua**
   but has heavier dependencies and more complex configuration. Not chosen for simplicity.

6. **Prometheus remote_write endpoint** in the otel-lgtm image is available at
   `http://host:9090/api/v1/write` — this is what Alloy writes metrics to.

7. **Loki push endpoint** is at `http://host:3100/loki/api/v1/push`.

### Reference Links

- [How I monitor my OpenWRT router with Grafana Cloud and Prometheus](https://grafana.com/blog/2021/02/09/how-i-monitor-my-openwrt-router-with-grafana-cloud-and-prometheus/)
- [Monitor OpenWRT nodes with Prometheus](https://www.cloudrocket.at/posts/monitor-openwrt-nodes-with-prometheus/)
- [prometheus-node-exporter-lua GitHub](https://github.com/openwrt/packages/tree/master/utils/prometheus-node-exporter-lua)
- [grafana/docker-otel-lgtm GitHub](https://github.com/grafana/docker-otel-lgtm)
- [Grafana Alloy loki.source.syslog docs](https://grafana.com/docs/alloy/latest/reference/components/loki/loki.source.syslog/)
- [Grafana Alloy prometheus.scrape docs](https://grafana.com/docs/alloy/latest/reference/components/prometheus/prometheus.scrape/)
- [OpenWRT remote syslog logging](https://feeding.cloud.geek.nz/posts/debugging-openwrt-routers-by-shipping/)
- [Monitoring OpenWRT with collectd, InfluxDB and Grafana](https://blog.christophersmart.com/2019/09/09/monitoring-openwrt-with-collectd-influxdb-and-grafana/)
- [Docker OTel LGTM documentation](https://grafana.com/docs/opentelemetry/docker-lgtm/)

---

## Implementation Order

The recommended build order for this repo:

1. `docker-compose.yml` + `alloy/config.alloy` — core stack, testable immediately
2. `openwrt/setup.sh` — router setup script
3. `grafana/provisioning/` — data sources + dashboard providers
4. Dashboard JSON files (build in Grafana UI, export, commit)
5. `.env` + env var wiring in docker-compose
6. `docs/` — detailed guides
7. `README.md` — final polish with screenshots
