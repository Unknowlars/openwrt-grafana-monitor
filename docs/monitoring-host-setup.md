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

The classic OpenWrt dashboards and the v2beta1 Operations dashboard load automatically from `grafana/provisioning/dashboards/`.

The Operations dashboard is also generated into `grafana-dashboard-exports/openwrt-operations-v2.json` as a manual-import copy.

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
| 1234 | Alloy UI | Alloy debug/config UI (bound to `127.0.0.1` only) |

Added by the optional `netflow` profile (`docker compose --profile netflow up -d`):

| Port | Service | Purpose |
|------|---------|---------|
| 2055/UDP | Akvorado inlet | NetFlow v5/v9 from the routers. Bound on all interfaces |
| 4739/UDP | Akvorado inlet | IPFIX. Bound on all interfaces |
| 6343/UDP | Akvorado inlet | sFlow. Bound on all interfaces |
| 8081 | Akvorado console | Flow explorer UI, **unauthenticated**, bound to `127.0.0.1` only |
| 8123 | ClickHouse | HTTP interface for debugging, bound to `127.0.0.1` only |

This profile also adds Kafka and ClickHouse and takes the stack from roughly
1 GB to 5-6 GB of RAM. It is off by default. It requires the
`grafana-clickhouse-datasource` Grafana plugin, which is installed at startup
via `GF_PLUGINS_PREINSTALL` and needs outbound internet on first run. See
[netflow-akvorado.md](netflow-akvorado.md).

---

## Data persistence

All data (metrics, logs, dashboards) is stored in the `lgtm-data` Docker volume:

```sh
docker volume inspect lgtm-data
```

Alloy's `prometheus.remote_write` write-ahead log lives in a second volume,
`alloy-data`, mounted at `/var/lib/alloy/data`:

```sh
docker volume inspect alloy-data
```

This volume is what keeps samples that have not yet been flushed to `otel-lgtm`
across a container restart or recreation. Without it — and without the matching
`--storage.path=/var/lib/alloy/data` flag in the `command:` block — Alloy writes
its WAL to a working-directory-relative `data-alloy/` on the container's
ephemeral filesystem, and every `docker compose up -d` or `restart` silently
discards whatever had not been flushed yet.

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

The UI has **no authentication**, so the host side of the mapping is bound to
loopback (`127.0.0.1:1234:12345`) and is not reachable from the rest of the
network. To reach it from another machine, use an SSH tunnel rather than
widening the binding:

```sh
ssh -L 1234:127.0.0.1:1234 <monitoring-host>
```

Note that Alloy's own default `--server.http.listen-addr` is
`127.0.0.1:12345` — container-loopback, which no port mapping can reach. The
`command:` block in `docker-compose.yml` passes
`--server.http.listen-addr=0.0.0.0:12345` explicitly to make the UI reachable
inside the container. Before that flag was added the UI was never reachable at
all, despite the port mapping and this section.

---

## Updating

```sh
docker compose pull
docker compose up -d
```
