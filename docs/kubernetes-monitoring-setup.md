# Kubernetes monitoring setup

This guide shows how to use this OpenWrt router project with an existing
Kubernetes monitoring stack instead of the included Docker Compose stack.

Use this if your homelab already has Kubernetes with:

- Prometheus, usually from `kube-prometheus-stack` or Prometheus Operator
- Grafana
- Loki, if you want OpenWrt logs
- A way to expose one UDP/TCP port on your LAN, usually MetalLB

You do not need to run `docker compose up -d` for this setup. Kubernetes will
scrape the router metrics and receive the router logs.

> [!IMPORTANT]
> **Step 4 is not optional.** The Docker Compose stack does two pieces of metric
> relabeling that this repository's dashboards depend on, and a stock
> ServiceMonitor does neither. Without them you get an unbounded cardinality
> series in your cluster Prometheus, and the Clients and Topology dashboards
> render empty. This was the single biggest gap in earlier versions of this
> guide.

> [!NOTE]
> The Kubernetes manifests in this guide are illustrative and are not covered by
> `tests/run_all.sh`, which is an offline check of the repo's own shell, Lua,
> Python, and Compose files. Treat the YAML here as a starting point to adapt,
> not as a tested artifact.

## What you are building

```text
OpenWrt router
├── metrics: http://192.168.0.1:9100/metrics
│       |
│       v
│   Kubernetes Prometheus  ── metricRelabelings (Step 4) ──▶ Grafana dashboards
│
├── syslog: UDP 514 to a Kubernetes LoadBalancer IP
│       |
│       v
│   Grafana Alloy in Kubernetes ──▶ Loki
│
└── NetFlow (optional): UDP 2055 to an Akvorado inlet
        |
        v
    ClickHouse ──▶ Grafana "OpenWrt - NetFlow"
```

In plain terms:

- The router exposes metrics on port `9100`.
- Prometheus pulls those metrics every 30 seconds, and rewrites two label
  families on the way in.
- The router sends logs to a Kubernetes IP on port `514`.
- Alloy receives those logs and forwards them to Loki.
- Optionally, the router also exports per-flow NetFlow records to Akvorado.
- Grafana shows the dashboards from this repo.

## Example values

This guide uses these example values. Replace them with your own.

| Name | Example | Meaning |
| --- | --- | --- |
| Router IP | `192.168.0.1` | Your OpenWrt gateway |
| Second router IP | `192.168.0.2` | A downstream AP, if you have one |
| Router name labels | `openwrt-main`, `openwrt-ap` | Label used in Prometheus, Loki, Akvorado, and Grafana |
| Kubernetes namespace | `monitoring` | Where your monitoring stack runs |
| Syslog LoadBalancer IP | `192.168.0.221` | LAN IP where the router sends logs |
| NetFlow collector IP | `192.168.0.222` | LAN IP where the router sends flows, if used |
| Prometheus datasource UID | `prometheus` | Grafana datasource UID for metrics |
| Loki datasource UID | `loki` | Grafana datasource UID for logs |

If your Grafana datasources use different UIDs, change the dashboard variables
after import. For repo changes, edit the relevant generator and regenerate; do
not hand-edit generated dashboard JSON. See
[repository-map.md](repository-map.md) for which generator owns which file.

## Step 1 - Pick and reserve a syslog IP

Choose one unused LAN IP for the Kubernetes syslog receiver.

Example:

```text
192.168.0.221
```

Reserve or exclude that IP from DHCP in OpenWrt so no other device gets it.

If you use MetalLB, the IP must be inside your MetalLB address pool.

## Step 2 - Install the router side

From your workstation, copy and run the router setup script.

> [!WARNING]
> Copy the **whole `openwrt/` directory**, not just `setup.sh`. The installer
> needs the bundled collectors, helper scripts, and shared Lua modules that sit
> alongside it, and it exits with
> `setup.sh expects the whole openwrt/ directory` if they are missing. Earlier
> versions of this guide copied only `setup.sh`, which fails immediately.

Replace `192.168.0.221` with the syslog IP you chose in Step 1.

```sh
scp -O -r openwrt root@192.168.0.1:/tmp/
ssh root@192.168.0.1 "sh /tmp/openwrt/setup.sh 192.168.0.221"
```

The `-O` flag is important for many OpenWrt routers because their SSH server
does not support SFTP.

### Choosing a profile

The default profile is `core`. Optional profiles add collectors:

```sh
ssh root@192.168.0.1 \
  "OPENWRT_MONITOR_PROFILE=core,clients sh /tmp/openwrt/setup.sh 192.168.0.221"
```

Profiles are `core`, `traffic`, `wifi_mesh`, `dpi`, `clients`, `netflow`, and
`full`. See [advanced-profiles.md](advanced-profiles.md) for what each one adds
and which availability metric reports its health. The `clients` profile is what
feeds the Clients and Topology dashboards, and it is the one that makes Step 4's
MAC normalisation matter.

The setup script:

- Installs the OpenWrt Prometheus exporter packages.
- Enables this project's custom textfile metrics.
- Makes metrics available on `http://192.168.0.1:9100/metrics`.
- Configures remote syslog to send logs to `192.168.0.221:514/udp`.

Check the router metrics from your workstation:

```sh
curl http://192.168.0.1:9100/metrics | head
```

You should see Prometheus text output.

Also check the custom metrics:

```sh
curl http://192.168.0.1:9100/metrics | grep -E 'node_openwrt_info|router_device_up|dhcp_lease|node_textfile' | head
```

### If you have more than one router

Run the same command against each, pointing them all at the same syslog IP.
Every router needs its own Kubernetes scrape target in Step 3, with a distinct
`router` label. The topology node graph is explicitly a multi-router contract —
if you run two routers, both must be on the same version of the collectors, or
the graph will be wrong.

Example for a downstream AP at `192.168.0.2`:

```sh
scp -O -r openwrt root@192.168.0.2:/tmp/
ssh root@192.168.0.2 \
  "OPENWRT_MONITOR_PROFILE=full sh /tmp/openwrt/setup.sh 192.168.0.221"
```

If the gateway already exports NetFlow and you only want AP metrics, omit the
`netflow` profile on the AP to avoid double-counting flows that the gateway
already sees:

```sh
ssh root@192.168.0.2 \
  "OPENWRT_MONITOR_PROFILE=core,traffic,wifi_mesh,dpi,clients sh /tmp/openwrt/setup.sh 192.168.0.221"
```

## Step 3 - Create the Kubernetes metrics target

Prometheus Operator discovers scrape targets through Kubernetes objects. For an
external router, create a headless `Service`, an `EndpointSlice`, and a
`ServiceMonitor`.

> [!NOTE]
> Older Prometheus Operator versions would not discover an external
> ServiceMonitor target from an EndpointSlice alone, so this guide used to also
> include a classic `v1 Endpoints` object. That API is
> [deprecated in Kubernetes v1.33+](https://kubernetes.io/blog/2025/04/24/endpoints-deprecation/)
> and the API server now emits warnings when you read or write it. Prefer
> EndpointSlice. Only add an `Endpoints` object as a fallback if your target
> genuinely fails to appear, and expect the deprecation warning.

Save this as something like `openwrt-exporter.yaml` in your Kubernetes
manifests repo:

```yaml
apiVersion: v1
kind: Service
metadata:
  name: openwrt-exporter
  namespace: monitoring
  labels:
    app.kubernetes.io/name: openwrt-exporter
    prometheus-job: openwrt
    router: openwrt-main
spec:
  type: ClusterIP
  clusterIP: None
  ports:
    - name: metrics
      port: 9100
      targetPort: metrics
      protocol: TCP
---
apiVersion: discovery.k8s.io/v1
kind: EndpointSlice
metadata:
  name: openwrt-exporter
  namespace: monitoring
  labels:
    kubernetes.io/service-name: openwrt-exporter
    app.kubernetes.io/name: openwrt-exporter
addressType: IPv4
ports:
  - name: metrics
    protocol: TCP
    port: 9100
endpoints:
  - addresses:
      - 192.168.0.1
---
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: openwrt-exporter
  namespace: monitoring
  labels:
    app.kubernetes.io/name: openwrt-exporter
spec:
  jobLabel: prometheus-job
  selector:
    matchLabels:
      app.kubernetes.io/name: openwrt-exporter
  namespaceSelector:
    matchNames:
      - monitoring
  targetLabels:
    - router
  endpoints:
    - port: metrics
      path: /metrics
      interval: 30s
      # See Step 4. Do not omit this block.
      metricRelabelings: []
```

The `router` label comes from the Service's own `router:` label, promoted by
`targetLabels`. For a second router, duplicate all three objects with a
different name (`openwrt-exporter-ap`), a different `router:` label value, and
the second router's IP — keeping the same `prometheus-job: openwrt` so both land
in `job="openwrt"`, which is what every dashboard filters on.

Apply it with your normal GitOps flow, or manually for a first test:

```sh
kubectl apply -f openwrt-exporter.yaml
```

## Step 4 - Add the required metric relabeling

This is the step that a plain ServiceMonitor gets wrong. The Compose stack does
this work in `alloy/config.alloy`; on Kubernetes it has to happen in the
ServiceMonitor's `metricRelabelings`.

Replace the empty `metricRelabelings: []` from Step 3 with:

```yaml
      metricRelabelings:
        # 1. Drop node_nat_traffic.
        #
        # This is a conntrack-derived series labelled with both source AND
        # destination address, i.e. a cross-product that grows with every pair
        # of hosts that has ever talked. It will bloat your cluster Prometheus
        # and nothing in this repo queries it. The Compose stack drops it too.
        - sourceLabels: [__name__]
          regex: node_nat_traffic
          action: drop

        # 2. Lowercase MAC-shaped labels.
        #
        # The upstream OpenWrt collectors (wifi_stations, hostapd_stations,
        # dnsmasq, uci_dhcp_host) emit MAC addresses UPPERCASE. Every collector
        # in this repository emits them lowercase. PromQL has no tolower(), so
        # the two can never be joined unless they are normalised at ingest.
        #
        # These three MUST be done together. The Operations dashboard's WiFi
        # Client Detail panel rewrites `station` into a `mac` field and joins it
        # against wifi_station_signal_dbm; lowercasing one side only silently
        # produces an empty join rather than an error.
        - sourceLabels: [mac]
          targetLabel: mac
          action: lowercase
        - sourceLabels: [bssid]
          targetLabel: bssid
          action: lowercase
        - sourceLabels: [station]
          targetLabel: station
          action: lowercase
```

Applying the lowercase rules unconditionally is safe: MAC addresses are
case-insensitive by definition, so it is idempotent on series that are already
lowercase, and a metric without the label produces an empty result that
relabeling drops rather than sets.

> [!NOTE]
> `action: lowercase` needs Prometheus v2.36+ and a Prometheus Operator recent
> enough to accept it in its `RelabelConfig` schema. If your operator rejects
> the manifest, upgrade it — there is no clean workaround, because the
> alternative is doing the case-folding in every dashboard query, and PromQL
> cannot.

### Why this matters, concretely

If you already scrape these routers from both a cluster Prometheus and the
Compose stack, the two stores will disagree on MAC casing unless both apply
these rules. A query written against one will silently return nothing against
the other. Decide which store is authoritative for MAC-keyed dashboards, and
make sure it has this relabeling.

## Step 5 - Verify metrics in Prometheus

Wait about one minute, then check Prometheus.

```promql
up{job="openwrt", router="openwrt-main"}
node_openwrt_info{job="openwrt", router="openwrt-main"}
router_device_up{job="openwrt", router="openwrt-main"}
```

If `up` is `1`, Prometheus is scraping the router.

Confirm Step 4 actually took effect:

```promql
# Should return NOTHING.
node_nat_traffic

# Should return only lowercase hex, no A-F.
count by (mac) (dhcp_lease)
```

If there is no result at all:

```sh
kubectl -n monitoring get svc,endpointslice openwrt-exporter
kubectl -n monitoring get servicemonitor openwrt-exporter
```

Also confirm your Prometheus is configured to discover ServiceMonitors in the
`monitoring` namespace.

## Step 6 - Create the syslog receiver

For logs, run a small dedicated Alloy Deployment in Kubernetes.

Why a dedicated Deployment?

- The normal Kubernetes Alloy DaemonSet usually collects pod logs.
- Syslog is a different job.
- Port `514` is a privileged port inside containers.
- This example exposes Service port `514`, but Alloy listens on container port
  `1514`, so it can still run as a non-root user.

> [!WARNING]
> Do **not** use `action: labelmap` with `regex: "__syslog_(.+)"` here, which
> earlier versions of this guide did. That wildcard promotes
> `__syslog_message_proc_id` — the sending process's PID — into a Loki stream
> label, so every process restart on the router creates a brand new log stream.
> Stream churn inflates Loki's index and degrades ingestion far more than
> high-cardinality log *content* does. The config below promotes an explicit
> allowlist instead, matching `alloy/config.alloy`.

Save this as `openwrt-syslog-alloy.yaml`.

Change:

- `192.168.0.221` to your chosen syslog IP
- the Alloy image to the pinned version you use in your cluster

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: alloy-openwrt
  namespace: monitoring
automountServiceAccountToken: false
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: alloy-openwrt-config
  namespace: monitoring
data:
  config.alloy: |
    loki.relabel "openwrt_syslog" {
      forward_to = [loki.write.default.receiver]

      // Explicit allowlist, NOT labelmap. The label names keep the `message_`
      // prefix because that is what the dashboards select on -- renaming them
      // to bare `severity`/`app_name` returns no data in every log panel.
      //
      // regex "(.+)" on each rule so a frame missing the field does not get an
      // empty-string label.
      rule {
        source_labels = ["__syslog_message_severity"]
        regex         = "(.+)"
        target_label  = "message_severity"
      }

      rule {
        source_labels = ["__syslog_message_facility"]
        regex         = "(.+)"
        target_label  = "message_facility"
      }

      rule {
        source_labels = ["__syslog_message_app_name"]
        regex         = "(.+)"
        target_label  = "message_app_name"
      }

      // Prefer the sending device's own hostname over the static `router`
      // label below, so a second AP labels correctly instead of every log
      // being attributed to the first router.
      rule {
        source_labels = ["__syslog_message_hostname"]
        regex         = "(.+)"
        target_label  = "router"
      }
    }

    loki.source.syslog "openwrt" {
      listener {
        address  = "0.0.0.0:1514"
        protocol = "udp"

        syslog_format                   = "rfc3164"
        use_incoming_timestamp          = true
        rfc3164_default_to_current_year = true

        labels = {
          job    = "openwrt-syslog",
          router = "openwrt-main",
        }
      }

      listener {
        address  = "0.0.0.0:1514"
        protocol = "tcp"

        syslog_format                   = "rfc3164"
        use_incoming_timestamp          = true
        rfc3164_default_to_current_year = true

        labels = {
          job    = "openwrt-syslog",
          router = "openwrt-main",
        }
      }

      relabel_rules = loki.relabel.openwrt_syslog.rules
      forward_to    = [loki.write.default.receiver]
    }

    loki.write "default" {
      endpoint {
        url = "http://loki.monitoring.svc.cluster.local:3100/loki/api/v1/push"
      }
    }
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: alloy-openwrt
  namespace: monitoring
spec:
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: alloy-openwrt
  template:
    metadata:
      labels:
        app.kubernetes.io/name: alloy-openwrt
    spec:
      serviceAccountName: alloy-openwrt
      automountServiceAccountToken: false
      securityContext:
        runAsNonRoot: true
        runAsUser: 10001
        runAsGroup: 10001
        fsGroup: 10001
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: alloy
          image: docker.io/grafana/alloy:v1.11.3
          imagePullPolicy: IfNotPresent
          command:
            - /bin/alloy
          args:
            - run
            - /etc/alloy/config.alloy
            - --storage.path=/tmp/alloy/data
            - --server.http.listen-addr=0.0.0.0:12345
          ports:
            - name: http
              containerPort: 12345
              protocol: TCP
            - name: syslog-udp
              containerPort: 1514
              protocol: UDP
            - name: syslog-tcp
              containerPort: 1514
              protocol: TCP
          readinessProbe:
            httpGet:
              path: /-/ready
              port: http
            initialDelaySeconds: 10
          livenessProbe:
            httpGet:
              path: /-/ready
              port: http
            initialDelaySeconds: 30
          resources:
            requests:
              cpu: 25m
              memory: 64Mi
            limits:
              cpu: 100m
              memory: 128Mi
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities:
              drop:
                - ALL
          volumeMounts:
            - name: config
              mountPath: /etc/alloy
              readOnly: true
            - name: tmp
              mountPath: /tmp
      volumes:
        - name: config
          configMap:
            name: alloy-openwrt-config
        - name: tmp
          emptyDir: {}
---
apiVersion: v1
kind: Service
metadata:
  name: alloy-openwrt
  namespace: monitoring
  annotations:
    metallb.io/loadBalancerIPs: 192.168.0.221
  labels:
    app.kubernetes.io/name: alloy-openwrt
    app.kubernetes.io/component: syslog
spec:
  type: LoadBalancer
  ports:
    - name: syslog-udp
      port: 514
      targetPort: syslog-udp
      protocol: UDP
    - name: syslog-tcp
      port: 514
      targetPort: syslog-tcp
      protocol: TCP
  selector:
    app.kubernetes.io/name: alloy-openwrt
---
apiVersion: v1
kind: Service
metadata:
  name: alloy-openwrt-metrics
  namespace: monitoring
  labels:
    app.kubernetes.io/name: alloy-openwrt
    app.kubernetes.io/component: metrics
spec:
  type: ClusterIP
  ports:
    - name: http
      port: 12345
      targetPort: http
      protocol: TCP
  selector:
    app.kubernetes.io/name: alloy-openwrt
```

Apply it:

```sh
kubectl apply -f openwrt-syslog-alloy.yaml
```

Check it:

```sh
kubectl -n monitoring get deploy,pod,svc -l app.kubernetes.io/name=alloy-openwrt
```

The `alloy-openwrt` Service should show your chosen LoadBalancer IP.

## Step 7 - Point OpenWrt syslog at Kubernetes

If the setup script already used the correct syslog IP, you can skip this.

Otherwise, update OpenWrt:

```sh
ssh root@192.168.0.1 'uci set system.@system[0].log_ip="192.168.0.221"; uci set system.@system[0].log_port="514"; uci set system.@system[0].log_proto="udp"; uci commit system; /etc/init.d/log restart'
```

Verify:

```sh
ssh root@192.168.0.1 'uci show system | grep log_'
```

Expected important values:

```text
system.@system[0].log_ip='192.168.0.221'
system.@system[0].log_port='514'
system.@system[0].log_proto='udp'
```

Send a test log:

```sh
ssh root@192.168.0.1 'logger "test message from openwrt"'
```

## Step 8 - Verify logs in Loki

In Grafana Explore, select Loki and run:

```logql
{job="openwrt-syslog", router="openwrt-main"}
```

Confirm the relabel allowlist worked — this should return nothing:

```logql
{job="openwrt-syslog"} | message_proc_id != ""
```

If `message_proc_id` exists as a stream label, you are still using `labelmap`.

If you do not see logs at all:

```sh
kubectl -n monitoring logs deploy/alloy-openwrt --tail=100
ssh root@192.168.0.1 'uci show system | grep log_'
```

## Step 9 - Import the Grafana dashboards

This repo generates two families of dashboards. All of them live in
`grafana/provisioning/dashboards/`.

**v2 dashboards** (current; also published as manual-import copies in
`grafana-dashboard-exports/`):

| File | Generator |
| --- | --- |
| `openwrt-mission-control.json` | `build_openwrt_mission_control.py` |
| `openwrt-operations-v2.json` | `build_openwrt_operations_dashboard.py` |
| `openwrt-clients-v2.json` | `build_openwrt_clients_dashboard.py` |
| `openwrt-advanced-v2.json` | `build_openwrt_advanced_dashboard.py` |
| `openwrt-topology-v2.json` | `build_openwrt_topology_dashboard.py` |
| `openwrt-netflow-v2.json` | `build_openwrt_netflow_dashboard.py` |

**Classic dashboards** (all four from `build_dashboards.py`):

`openwrt-overview.json`, `openwrt-network.json`, `openwrt-devices.json`,
`openwrt-logs.json`

For manual import, prefer the files in `grafana-dashboard-exports/` for the v2
set — they are the same bytes, published separately for exactly this use.

1. Open Grafana.
2. Go to **Dashboards** → **New** → **Import**.
3. Upload one JSON file.
4. Select your Prometheus datasource for metrics dashboards, Loki for the logs
   dashboard, and ClickHouse for the NetFlow dashboard.
5. Repeat.

If your Grafana is managed as code, copy those JSON files into your dashboard
provisioning system instead.

Notes:

- `openwrt-clients-v2` and `openwrt-topology-v2` need the `clients` router
  profile **and** Step 4's MAC normalisation. They will render empty otherwise.
- `openwrt-netflow-v2` is a 48-panel dashboard generated by
  `build_openwrt_netflow_dashboard.py`. It needs the optional NetFlow setup
  below and a ClickHouse datasource. Skip it if you are not running NetFlow.
  With GeoIP enabled it shows source/destination AS, source/destination country,
  source/destination ports, service-class buckets, AS/country matrices, and
  Akvorado pipeline-health proof.

## Step 10 - Check dashboard variables

Open the dashboards and check the variables at the top.

Common defaults are:

| Variable | Default |
| --- | --- |
| Router | `openwrt` |
| WAN interface | `wan` |
| 2.4 GHz WiFi interface | `phy0-ap0` |
| 5 GHz WiFi interface | `phy1-ap0` |
| VPN interface | `tailscale0` |

Your router may use different interface names, and the `Router` variable must
match whatever you set as the `router` label in Step 3.

Find interface names:

```sh
ssh root@192.168.0.1 'ip route | grep default; cat /proc/net/dev'
```

## Optional - NetFlow with Akvorado

Per-flow visibility (who talked to whom, on which port). Read
[netflow-akvorado.md](netflow-akvorado.md) first — especially the limits
section, because on hardware with flow offload the data is structurally
incomplete.

**This repository ships no Kubernetes manifests for Akvorado.** It is a
multi-service stack — inlet, outlet, orchestrator, console, Kafka, ClickHouse,
Redis — and running it well on Kubernetes means bringing your own Kafka and
ClickHouse operators. Two honest options:

### Option A - Run the Compose profile on a separate host (recommended)

Keep Kubernetes for metrics and logs, and run the NetFlow stack as-is on any
Docker host:

```sh
cp akvorado/exporters.yaml.example akvorado/exporters.yaml   # then edit it
docker compose --profile netflow up -d
```

For MaxMind GeoIP enrichment, place the databases in `akvorado/geoip/` using
the filenames the repo config expects:

```text
akvorado/geoip/GeoLite2-ASN.mmdb
akvorado/geoip/GeoLite2-City.mmdb
akvorado/geoip/GeoLite2-Country.mmdb
```

Point the router at that host instead of a MetalLB IP:

```sh
ssh root@192.168.0.1 \
  "OPENWRT_MONITOR_PROFILE=core,netflow NETFLOW_PORT=2055 \
   sh /tmp/openwrt/setup.sh <docker-host-ip>"
```

For a second AP that also exports NetFlow, run the same command against the AP
and add another exporter entry in `akvorado/exporters.yaml`. The exporter
`name:` must match the Prometheus/Grafana router label, and the ifIndex must be
the real bridge ifIndex from that device:

```sh
ssh root@192.168.0.2 "cat /sys/class/net/br-lan/ifindex"
```

```yaml
    192.168.0.2/32:
      name: openwrt-ap
      ifindexes:
        8:
          name: br-lan
          description: "LAN bridge"
          speed: 1000
```

Use the value printed by `cat /sys/class/net/br-lan/ifindex`, not the example
`8`, then restart the Akvorado roles.

To query the flows from your cluster Grafana, add a ClickHouse datasource
pointing at that host. This requires exposing ClickHouse beyond loopback, which
`docker-compose.yml` deliberately does not do — it binds `8123` to `127.0.0.1`.
If you change that binding, restrict it: ClickHouse in this stack has no
authentication configured (`CLICKHOUSE_SKIP_USER_SETUP=1`), so anything that can
reach the port can read every flow record. Bind it to the specific interface
your cluster reaches, and firewall it to the cluster nodes.

You will also need the `grafana-clickhouse-datasource` plugin installed in your
cluster Grafana — for `kube-prometheus-stack` that is `grafana.plugins` in your
Helm values, not the `GF_PLUGINS_PREINSTALL` variable the Compose stack uses.

### Option B - Run Akvorado in Kubernetes

Real work, and out of scope for this repo, but the shape is:

- Kafka via an operator such as Strimzi.
- ClickHouse via the Altinity operator, with a persistent volume.
- The four Akvorado services as Deployments, all pointed at the orchestrator's
  in-cluster URL. `akvorado/akvorado.yaml` from this repo is the config; mount
  it as a ConfigMap and mount `exporters.yaml` alongside it, since the
  orchestrator `!include`s it and will not start without it.
- A `LoadBalancer` Service on UDP `2055` for the inlet — the same MetalLB
  pattern as the syslog receiver in Step 6, with its own reserved IP.
- ClickHouse must be able to reach the orchestrator over HTTP; it pulls
  dictionaries and GeoIP data from it. That is what `clickhouse.orchestrator-url`
  is for, and it needs to be an in-cluster address.

### Scraping Akvorado's own metrics

Either way, Akvorado exposes Prometheus metrics at
`/api/v0/{inlet,outlet}/metrics` on port `8080`. The NetFlow dashboard's
Pipeline Health tab expects them under `job="akvorado"` — deliberately **not**
`job="openwrt"`, which every router panel filters on. Add a ServiceMonitor (or a
scrape config for an external host) with that job label and one endpoint per
component, since the metrics path differs.

## Quick troubleshooting

### Prometheus has no `openwrt` target

```sh
kubectl -n monitoring get svc,endpointslice openwrt-exporter
kubectl -n monitoring get servicemonitor openwrt-exporter
```

Make sure:

- The EndpointSlice address is your router IP.
- The Service port is named `metrics`.
- Your Prometheus discovers ServiceMonitors in this namespace.

### The target exists but is down

```sh
curl http://192.168.0.1:9100/metrics | head
```

If that fails, check the router exporter:

```sh
ssh root@192.168.0.1 '/etc/init.d/prometheus-node-exporter-lua status'
ssh root@192.168.0.1 'uci show prometheus-node-exporter-lua'
```

### Clients or Topology dashboard is empty

Almost always one of two things:

1. The router was installed without the `clients` profile. Check
   `openwrt_client_inventory_collector_available` and
   `openwrt_topology_collector_available`.
2. Step 4's MAC relabeling is missing, so the MAC joins resolve to nothing.
   Check whether `dhcp_lease` and `wifi_station_signal_dbm` agree on casing:

```promql
count by (mac) (dhcp_lease)
count by (mac) (wifi_station_signal_dbm)
```

If one set is uppercase and the other lowercase, that is the problem.

### Cluster Prometheus memory grew after adding the router

Check that `node_nat_traffic` is actually being dropped:

```promql
count(node_nat_traffic)
```

Any result means the Step 4 drop rule is not applied. This series is a
source×destination cross-product and grows without bound.

### Logs do not appear

```sh
kubectl -n monitoring get pod -l app.kubernetes.io/name=alloy-openwrt
kubectl -n monitoring logs deploy/alloy-openwrt --tail=100
ssh root@192.168.0.1 'uci show system | grep log_'
ssh root@192.168.0.1 'logger "test message from openwrt"'
```

### Loki is slow or its index is large

Check for PID-derived stream labels, which `labelmap` would have created:

```logql
{job="openwrt-syslog"} | message_proc_id != ""
```

See the warning in Step 6.

### Dashboard panels show no data

In Grafana Explore, test Prometheus first:

```promql
up{job="openwrt", router="openwrt-main"}
node_openwrt_info{job="openwrt", router="openwrt-main"}
```

Then Loki:

```logql
{job="openwrt-syslog", router="openwrt-main"}
```

If Explore works but the dashboard does not, check dashboard variables and
datasource selection.

## Simple checklist

- [ ] Pick and reserve a syslog LoadBalancer IP.
- [ ] Copy the **whole `openwrt/` directory** and run `setup.sh` on the router.
- [ ] Pick a profile — `clients` if you want the Clients/Topology dashboards.
- [ ] Confirm `http://ROUTER_IP:9100/metrics` works.
- [ ] Add Service, EndpointSlice, and ServiceMonitor (one set per router).
- [ ] **Add the `metricRelabelings` from Step 4.** Not optional.
- [ ] Confirm `up{job="openwrt"}` is `1` and `node_nat_traffic` returns nothing.
- [ ] Deploy the Alloy syslog receiver with the explicit label allowlist.
- [ ] Point OpenWrt syslog to the Alloy LoadBalancer IP.
- [ ] Confirm logs with `{job="openwrt-syslog"}`.
- [ ] Import the dashboards you need.
- [ ] Adjust dashboard variables to match your `router` label and interfaces.
- [ ] Optional: set up NetFlow, and scrape Akvorado as `job="akvorado"`.
