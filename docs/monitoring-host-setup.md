# Monitoring Host Setup

The monitoring host is any Linux machine on the same network as your router. It runs:
- **grafana/otel-lgtm** — all-in-one Grafana + Prometheus + Loki + Tempo
- **Grafana Alloy** — metrics scraper and syslog receiver

The dashboards in this repo expect the router to be set up with both:

- official `prometheus-node-exporter-lua` packages
- the bundled custom collectors and helper scripts installed by `openwrt/setup.sh`

That includes custom textfile metrics for WAN quality, filesystem usage, and service health in addition to the Lua-based custom collectors.

## Requirements

- Docker 24+ and Docker Compose v2
- 2 GB RAM minimum (4 GB recommended)
- Ports 3000, 514, 9090, 3100, 4317, 4318 available
- Reachable from the router on the LAN

## Setup

### 1. Clone the repo

```sh
git clone https://github.com/your-username/openwrt-grafana-monitor
cd openwrt-grafana-monitor
```

### 2. Configure

```sh
cp .env.example .env
```

Edit `.env`:

```env
ROUTER_IP=192.168.0.1          # Your router's IP
ROUTER_NAME=openwrt             # Label used in Grafana
ROUTER_METRICS_PORT=9100        # prometheus-node-exporter-lua port
MONITORING_HOST_IP=192.168.0.100 # This machine's LAN IP
GRAFANA_ADMIN_PASSWORD=changeme  # Change this!
SYSLOG_PORT=514                 # Host port for Alloy syslog
```

### 3. Start

```sh
docker compose up -d
```

Check everything started:

```sh
docker compose ps
docker compose logs --tail 20
```

### 4. Open Grafana

Go to **http://localhost:3000**

Login: `admin` / value from `GRAFANA_ADMIN_PASSWORD` in `.env`

The four classic OpenWrt dashboards load automatically from `grafana/provisioning/dashboards/`.

The optional v2beta1 operations dashboard is generated separately into `grafana-dashboard-exports/openwrt-operations-v2.json` for manual import.

For a complete dashboard, make sure you ran the router-side setup by copying the whole `openwrt/` directory and executing `openwrt/setup.sh`, not just by installing the base exporter packages.

The dashboards include variables for `router`, `wan_interface`, `wifi24_interface`, `wifi5_interface`, and `vpn_interface`; adjust those in Grafana if your router uses different labels.

---

## Port reference

| Port | Service | Purpose |
|------|---------|---------|
| 3000 | Grafana | Web UI |
| 514/UDP | Alloy | Default router syslog receiver |
| 514/TCP | Alloy | Syslog receiver fallback |
| 9090 | Prometheus | Metrics database (also used by Alloy remote_write) |
| 3100 | Loki | Logs database |
| 3200 | Tempo | Traces database |
| 3500 | Pyroscope | Profiling (unused for OpenWrt) |
| 4317 | OTel Collector | OTLP gRPC |
| 4318 | OTel Collector | OTLP HTTP |
| 1234 | Alloy UI | Alloy debug/config UI |

---

## Data persistence

All data (metrics, logs, dashboards) is stored in the `lgtm-data` Docker volume:

```sh
docker volume inspect lgtm-data
```

To reset everything (wipe all data):

```sh
docker compose down -v
```

To back up:

```sh
docker run --rm -v lgtm-data:/data -v $(pwd):/backup alpine \
  tar czf /backup/lgtm-data-$(date +%Y%m%d).tar.gz /data
```

---

## Adding more routers

Edit `alloy/config.alloy` and add targets:

```alloy
prometheus.scrape "openwrt" {
  targets = [
    { __address__ = "192.168.0.1:9100", router = "home-router" },
    { __address__ = "10.0.0.1:9100",   router = "office-router" },
  ]
  ...
}
```

For syslog from multiple routers, add additional listeners on different ports,
or rely on the `log_hostname` label (set per router via `uci set system.@system[0].log_hostname`).

---

## Alloy UI

The Alloy debug interface is available at **http://localhost:1234**

Useful for:
- Checking if targets are being scraped
- Viewing pipeline component health
- Debugging config issues

---

## Updating

```sh
docker compose pull
docker compose up -d
```
