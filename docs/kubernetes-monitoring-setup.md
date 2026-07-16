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

## What you are building

```text
OpenWrt router
├── metrics: http://192.168.0.1:9100/metrics
│       |
│       v
│   Kubernetes Prometheus
│       |
│       v
│   Grafana OpenWrt dashboards
│
└── syslog: UDP 514 to a Kubernetes LoadBalancer IP
        |
        v
    Grafana Alloy in Kubernetes
        |
        v
    Loki
```

In plain terms:

- The router exposes metrics on port `9100`.
- Prometheus pulls those metrics every 30 seconds.
- The router sends logs to a Kubernetes IP on port `514`.
- Alloy receives those logs and forwards them to Loki.
- Grafana shows the dashboards from this repo.

## Example values

This guide uses these example values. Replace them with your own.

| Name | Example | Meaning |
| --- | --- | --- |
| Router IP | `192.168.0.1` | Your OpenWrt router |
| Router name label | `openwrt` | Label used in Prometheus, Loki, and Grafana |
| Kubernetes namespace | `monitoring` | Where your monitoring stack runs |
| Syslog LoadBalancer IP | `192.168.0.221` | LAN IP where the router sends logs |
| Prometheus datasource UID | `prometheus` | Grafana datasource UID for metrics |
| Loki datasource UID | `loki` | Grafana datasource UID for logs |

If your Grafana datasources use different UIDs, change the dashboard variables
after import. For repo changes, edit `build_dashboards.py` and regenerate the
dashboard JSON with `python3 build_dashboards.py`; do not hand-edit generated
dashboard JSON.

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

Replace `192.168.0.221` with the syslog IP you chose in Step 1.

```sh
scp -O openwrt/setup.sh root@192.168.0.1:/tmp/
ssh root@192.168.0.1 "sh /tmp/setup.sh 192.168.0.221"
```

The `-O` flag is important for many OpenWrt routers because their SSH server
does not support SFTP.

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

## Step 3 - Create the Kubernetes metrics target

Prometheus Operator discovers scrape targets through Kubernetes objects. For an
external router, create:

- A headless `Service`
- A classic `Endpoints` object
- An `EndpointSlice`
- A `ServiceMonitor`

The classic `Endpoints` object is important. Some Prometheus Operator setups
will not discover an external ServiceMonitor target from only an EndpointSlice.

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
    router: openwrt
spec:
  type: ClusterIP
  clusterIP: None
  ports:
    - name: metrics
      port: 9100
      targetPort: metrics
      protocol: TCP
---
apiVersion: v1
kind: Endpoints
metadata:
  name: openwrt-exporter
  namespace: monitoring
  labels:
    app.kubernetes.io/name: openwrt-exporter
subsets:
  - addresses:
      - ip: 192.168.0.1
    ports:
      - name: metrics
        port: 9100
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
```

Apply it with your normal GitOps flow, or manually for a first test:

```sh
kubectl apply -f openwrt-exporter.yaml
```

## Step 4 - Verify metrics in Prometheus

Wait about one minute, then check Prometheus.

Run these PromQL queries:

```promql
up{job="openwrt", router="openwrt"}
node_openwrt_info{job="openwrt", router="openwrt"}
router_device_up{job="openwrt", router="openwrt"}
```

If `up` is `1`, Prometheus is scraping the router.

If there is no result:

```sh
kubectl -n monitoring get svc,endpoints,endpointslice openwrt-exporter
kubectl -n monitoring get servicemonitor openwrt-exporter
```

Also confirm your Prometheus is configured to discover ServiceMonitors in the
`monitoring` namespace.

## Step 5 - Create the syslog receiver

For logs, run a small dedicated Alloy Deployment in Kubernetes.

Why a dedicated Deployment?

- The normal Kubernetes Alloy DaemonSet usually collects pod logs.
- Syslog is a different job.
- Port `514` is a privileged port inside containers.
- This example exposes Service port `514`, but Alloy listens on container port
  `1514`, so it can still run as a non-root user.

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

      rule {
        action = "labelmap"
        regex  = "__syslog_(.+)"
      }

      rule {
        source_labels = ["__syslog_message_severity"]
        target_label  = "level"
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
          router = "openwrt",
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
          router = "openwrt",
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

## Step 6 - Point OpenWrt syslog at Kubernetes

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

## Step 7 - Verify logs in Loki

In Grafana Explore, select Loki and run:

```logql
{job="openwrt-syslog", router="openwrt"}
```

If you do not see logs, check Alloy:

```sh
kubectl -n monitoring logs deploy/alloy-openwrt --tail=100
```

Also check that the router is sending to the right IP:

```sh
ssh root@192.168.0.1 'uci show system | grep log_'
```

## Step 8 - Import the Grafana dashboards

This repo includes four dashboards:

- `grafana/provisioning/dashboards/openwrt-overview.json`
- `grafana/provisioning/dashboards/openwrt-network.json`
- `grafana/provisioning/dashboards/openwrt-devices.json`
- `grafana/provisioning/dashboards/openwrt-logs.json`

The easiest way is to import them in the Grafana UI:

1. Open Grafana.
2. Go to **Dashboards**.
3. Choose **New**.
4. Choose **Import**.
5. Upload one JSON file.
6. Select your Prometheus datasource for metrics dashboards.
7. Select your Loki datasource for the logs dashboard.
8. Repeat for all four dashboards.

If your Grafana is managed as code, copy those JSON files into your dashboard
provisioning system instead.

## Step 9 - Check dashboard variables

Open the dashboards and check the variables at the top.

Common defaults are:

| Variable | Default |
| --- | --- |
| Router | `openwrt` |
| WAN interface | `wan` |
| 2.4 GHz WiFi interface | `phy0-ap0` |
| 5 GHz WiFi interface | `phy1-ap0` |
| VPN interface | `tailscale0` |

Your router may use different interface names.

Find interface names:

```sh
ssh root@192.168.0.1 'ip route | grep default; cat /proc/net/dev'
```

Then change the dashboard variables in Grafana.

## Quick troubleshooting

### Prometheus has no `openwrt` target

Check the Kubernetes objects:

```sh
kubectl -n monitoring get svc,endpoints,endpointslice openwrt-exporter
kubectl -n monitoring get servicemonitor openwrt-exporter
```

Make sure:

- The `Endpoints` IP is your router IP.
- The Service port is named `metrics`.
- Your Prometheus discovers ServiceMonitors in this namespace.

### The target exists but is down

Check from your workstation:

```sh
curl http://192.168.0.1:9100/metrics | head
```

If that fails, check the router exporter:

```sh
ssh root@192.168.0.1 '/etc/init.d/prometheus-node-exporter-lua status'
ssh root@192.168.0.1 'uci show prometheus-node-exporter-lua'
```

### Logs do not appear

Check that Alloy is running:

```sh
kubectl -n monitoring get pod -l app.kubernetes.io/name=alloy-openwrt
kubectl -n monitoring logs deploy/alloy-openwrt --tail=100
```

Check OpenWrt syslog settings:

```sh
ssh root@192.168.0.1 'uci show system | grep log_'
```

Send a fresh test message:

```sh
ssh root@192.168.0.1 'logger "test message from openwrt"'
```

### Dashboard panels show no data

In Grafana Explore, test Prometheus first:

```promql
up{job="openwrt", router="openwrt"}
node_openwrt_info{job="openwrt", router="openwrt"}
```

Then test Loki:

```logql
{job="openwrt-syslog", router="openwrt"}
```

If Explore works but the dashboard does not, check dashboard variables and
datasource selection.

## Simple checklist

- [ ] Pick and reserve a syslog LoadBalancer IP.
- [ ] Run `openwrt/setup.sh` on the router.
- [ ] Confirm `http://ROUTER_IP:9100/metrics` works.
- [ ] Add Service, Endpoints, EndpointSlice, and ServiceMonitor.
- [ ] Confirm `up{job="openwrt"}` is `1`.
- [ ] Deploy the Alloy syslog receiver.
- [ ] Point OpenWrt syslog to the Alloy LoadBalancer IP.
- [ ] Confirm logs with `{job="openwrt-syslog"}`.
- [ ] Import the four dashboards.
- [ ] Adjust dashboard interface variables if needed.
