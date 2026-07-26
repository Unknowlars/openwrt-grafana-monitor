# NetFlow with Akvorado

Per-flow visibility: which host talked to which host, on which port, carrying
how many bytes. This is the tier the polled counters in the rest of this
repository cannot reach — `nlbwmon` gives per-client totals, the nftables
counters give per-device rates, conntrack gives connection counts, but none of
them record the individual conversation.

Opt-in and off by default:

```sh
cp akvorado/exporters.yaml.example akvorado/exporters.yaml   # then edit it
docker compose --profile netflow up -d
```

**Read [Known limits](#known-limits) before enabling this.** On hardware with
flow offload the data is structurally incomplete, and that is not a bug that
can be fixed on the collector side.

---

## Architecture

```
OpenWrt router                     Monitoring host
──────────────                     ───────────────
softflowd  ──NetFlow v9/UDP──▶  akvorado-inlet
(libpcap on br-lan)                      │
                                    Kafka  (buffer)
                                      │
                                 akvorado-outlet
                                 (resolves ifIndex → interface,
                                  classifies internal/external,
                                  optional GeoIP/AS enrichment)
                                      │
                                  ClickHouse  ──▶ Grafana "OpenWrt - NetFlow"
                                      │              (ClickHouse datasource)
                                 akvorado-console
                                 (sankey / ad-hoc filtering, :8081)

openwrt-monitor-netflow-health.sh ──▶ Prometheus ──▶ same dashboard's
(exporter liveness, drops, ifIndex)                  "Pipeline Health" tab
```

Flow records never enter Prometheus. Only the *health* of the pipeline does.

### Components

| Service | Role |
|---|---|
| `akvorado-orchestrator` | Owns the config. Creates the Kafka topic and the ClickHouse schema, serves config to the other three over HTTP. |
| `akvorado-inlet` | Receives UDP flow packets, wraps them, pushes to Kafka without parsing. |
| `akvorado-outlet` | Consumes Kafka, decodes, enriches, batch-inserts into ClickHouse. |
| `akvorado-console` | Akvorado's own web UI. |
| `kafka` | Buffer between inlet and outlet. KRaft mode, no Zookeeper. |
| `clickhouse` | The flow store. |
| `redis` | Console query cache. |

Upstream's Traefik, kafka-ui, geoip-updater and its bundled Prometheus/Grafana/
Loki are all omitted — this repo already has an LGTM stack, and Traefik existed
only to multiplex ports that are bound directly here.

### Resource cost

Akvorado's own docs recommend 8 vCPU / 16 GB / 100 GB, but that sizing is
driven by Kafka and ClickHouse at carrier scale, not by Akvorado. For one home
router, budget roughly **4-6 GB of RAM** and disk proportional to retention.
The base stack (otel-lgtm + Alloy) is about 1 GB, so enabling this profile is
the single largest resource decision in this repository.

---

## Known limits

These are properties of the design, not bugs. Every one of them makes the data
*quietly wrong* rather than obviously broken, which is why the dashboard has a
whole tab dedicated to surfacing them.

### 1. Hardware flow offload hides traffic

softflowd captures with libpcap. Hardware flow offload (MT7621/MT7622 PPE and
similar) forwards packets in the switch ASIC, and the CPU never sees them —
so softflowd cannot capture them at any sampling rate. Flow totals are then
incomplete by an unknown amount.

The dashboard's **Flow Data Complete** tile reads `Incomplete (HW offload)`
whenever `openwrt_flow_offload_enabled{mode="hw"}` is 1. Options:

- **Accept it** (default). Useful for seeing *what* is being talked to, not for
  accounting total volume. The tile keeps the caveat visible.
- **Turn offload off**: `NETFLOW_DISABLE_HW_OFFLOAD=1` when running `setup.sh`.
  Complete accounting, at the cost of routing throughput on this hardware.

Software flow offload is less of a problem: packets still traverse the netdev,
so pcap generally still sees them.

### 2. softflowd's CPU cost on this hardware is unmeasured

Every captured packet is copied to userspace and hashed into a flow table. On a
low-end MIPS/ARM router at line rate this can be the dominant cost. This has
**not** been measured on the routers in this deployment — the measurement spike
has not been benchmarked on the supported router hardware.

Measure before and after on the gateway: `load`, `openwrt_softnet_*` drops, and
an `iperf3` run. Abort if CPU cost exceeds a few percent of a core or `softnet`
drops appear.

### 3. Akvorado discards flows with an unresolvable ifIndex

Flow records carry an interface *index* and nothing else. Akvorado resolves it
via `akvorado/exporters.yaml`; anything it cannot resolve is dropped, silently,
with no error on the router side. A wrong ifIndex is indistinguishable from
"NetFlow isn't working".

The router template passes `ifindex:interface` to softflowd, for example
`8:br-lan`. Do not remove the prefix: without it softflowd can still export
records, but Akvorado rejects them with `input and output interfaces missing`.

Get the real value from Prometheus once the profile is installed:

```
openwrt_netflow_ifindex{router="openwrt-main", interface="wan"}
```

or on the router: `cat /sys/class/net/br-lan/ifindex`.
The **Capture Interfaces and ifIndex** table on the dashboard shows it next to
the exporter state for exactly this comparison.

### 4. GeoIP enrichment is optional, and partial even when it works

`geoip: {optional: true}`. With no database present, `SrcAS`/`DstAS`,
`SrcCountry`/`DstCountry` and the `Geo*` columns stay empty. The compose file
still mounts `akvorado/geoip/` at `/usr/share/GeoIP`: Akvorado watches that
directory at startup even when the databases are optional, so the directory
must exist. Enabling enrichment needs a MaxMind account or an IPinfo token,
which is a per-operator decision.

To enable, add a geoip-updater container or manually place MaxMind/IPinfo
databases into `akvorado/geoip/`. The default config expects the standard
MaxMind names: `GeoLite2-ASN.mmdb`, `GeoLite2-City.mmdb`, and
`GeoLite2-Country.mmdb`. If you use short local aliases such as `asn.mmdb` or
`country.mmdb`, either rename them or add those paths to
`akvorado/akvorado.yaml`.

**ASN and Country enrichment work once the databases are in place**, but do
not expect resolution anywhere close to 100% of flows. On an ordinary home
network, most traffic is LAN-to-LAN, and a private address has no AS or
country by definition — that share of flows will never resolve, and it is not
a fault. The **External** tab's resolution tiles are therefore scoped to
boundary-crossing flows, so they answer "of the traffic that actually left,
how much did we identify" rather than "how much of the enrichment is broken".

**City-level enrichment is deliberately not enabled.** Loading
`GeoLite2-City.mmdb` alongside Country OOM-kills the orchestrator at its
current memory limit, so `SrcGeoCity`/`DstGeoCity`/`Src|DstGeoState` are 0%
populated by choice. No dashboard panel reads them, and
`tests/test_netflow_config.py` fails the build if one starts to — an empty
panel is indistinguishable from a broken pipeline, which is the failure this
whole design exists to prevent.

Two more columns are structurally empty here and equally off-limits:
`DstASPath`/`Dst1st|2nd|3rdAS`/`Dst*Communities` need a BGP peering source
this deployment does not have, and the `Exporter*`/`*Net{Site,Region,Tenant}`
fields are carrier multi-tenancy metadata.

Without GeoIP you still get useful labels: `clickhouse.networks` in
`akvorado.yaml` names your own prefixes, and `clickhouse.asns` can override AS
names for specific numbers.

### 5. Sampling and flow-table overflow

The stock OpenWrt softflowd package ships `sampling_rate 100` — enabling it
as-shipped silently gives 1-in-100 sampled byte counts. This repo's template
sets `1` (every packet), and `akvorado.yaml` sets `default-sampling-rate: 1` to
match. **If you change one, change the other**, or byte counts read low by
exactly that factor. Akvorado rejects flows with no sampling rate at all, which
is why that default exists.

Separately, `max_flows` (8192) overflowing force-expires flows early and
truncates byte counts with no error. Watch
`openwrt_netflow_flows_force_expired_total`.

### 6. Flow lifetime shapes the graphs

softflowd only exports a flow **when it expires**, and its own defaults are
`tcp`/`general` 1 hour and `maxlife` **one week**
(`softflowd.h: DEFAULT_MAXIMUM_LIFETIME = 3600*24*7`). Left alone, an ongoing
download or video stream contributes *nothing* to the dashboard until it
finishes, and then arrives as a single spike stamped at expiry time rather than
spread over when the traffic actually happened. Every throughput graph would be
wrong in both shape and placement.

The template therefore sets `option timeout 'maxlife=60'`
(`NETFLOW_TIMEOUTS`), forcing every flow to be cut and exported once a minute,
which lines up with the 1-minute rollup. The cost is more flow records; on a
home link that is the right trade, and it also keeps the flow table smaller,
which reduces forced expiry.

The stock init script maps exactly one `-t`, so only one timeout name is
settable. `maxlife` is the right one to spend it on — it bounds every flow
regardless of protocol.

### 7. Capture on the LAN bridge, not the WAN device

softflowd captures with libpcap at a device. On the WAN device that is *after*
SNAT, so every outbound flow carries the router's public address as its source
and per-client attribution is gone — which is most of the reason to collect
per-flow data on a home network at all. The default is therefore the LAN bridge
(`NETFLOW_INTERFACES`, defaulting to `TRAFFIC_LAN_INTERFACE`, i.e. `br-lan`),
which sees pre-NAT addresses.

Do not capture both: each packet traverses the bridge and the WAN device, so
every byte would be counted twice.

One consequence to be aware of: **softflowd cannot distinguish ingress from
egress**. It sets `if_index_in` and `if_index_out` to the same value, the
ifIndex of its single capture device (`netflow9.c`:
`dc[0]->if_index_in = dc[0]->if_index_out = htonl(ifidx)`). So Akvorado's
`InIfBoundary`/`OutIfBoundary` are constant across every flow and cannot express
direction — filtering on them returns all rows or none.

The dashboard therefore derives direction and internal/external from
`SrcNetRole`/`DstNetRole`, which come from the `clickhouse.networks` prefixes in
`akvorado.yaml`. **If your LAN is not in one of those prefixes, edit them**, or
every flow will land in a single "inbound" bucket. Akvorado's own console still
defaults to an `InIfBoundary = 'external'` filter on its homepage, which is not
useful here; change the filter in the console UI.

### 8. Retention

`clickhouse.resolutions` in `akvorado/akvorado.yaml` is cut down from upstream's
carrier defaults:

| Table | Interval | Kept | Contains addresses/ports? |
|---|---|---|---|
| `flows` | raw | 7 days | yes |
| `flows_1m` | 1 minute | 7 days | no |
| `flows_1h` | 1 hour | 1 year | no |

Consolidated tables drop `SrcAddr`/`DstAddr`/`SrcPort`/`DstPort`, so every
per-host and per-port panel only works inside the 7-day raw window. Widening
`interval: 0` scales disk roughly linearly.

### 9. softflowd does not export ICMP type or code

ClickHouse ships an `icmp` dictionary mapping `(proto, type, code)` to names
like `echo-request` and `destination-unreachable`, and Akvorado's schema
carries ICMP type/code packed into `DstPort` — upstream's convention is
`type * 256 + code`.

softflowd never populates it. `DstPort` is a constant `0` on every `Proto=1`
and `Proto=58` flow in this deployment, so the dictionary would decode all of
them as whatever `(1, 0, 0)` maps to (`echo-reply`) regardless of what the
packets actually were. That is worse than showing nothing: it is a confident
wrong answer.

The Security Signals tab therefore shows ICMP and ICMPv6 **volume** over time
and states in the panel description why there is no type breakdown. ICMPv6
volume is high and steady on any IPv6 LAN — neighbour discovery and router
advertisement are both ICMPv6 — so a sustained ICMPv4 climb is the more
interesting of the two.

---

## Setup

### 1. Monitoring host

```sh
cp akvorado/exporters.yaml.example akvorado/exporters.yaml
$EDITOR akvorado/exporters.yaml     # router address, ifIndex, WAN description
```

The WAN interface `description` **must** start with `Transit: ` — the
`interface-classifiers` rules in `akvorado.yaml` match on that prefix to mark
the interface external, and the console homepage plus several dashboard panels
filter on `InIfBoundary = 'external'`. Get it wrong and those panels are empty
rather than erroring.

Validate the config without starting anything:

```sh
docker compose --profile netflow run --rm --no-deps \
  akvorado-orchestrator orchestrator --check /etc/akvorado/akvorado.yaml
```

Then bring it up:

```sh
docker compose --profile netflow up -d
```

`akvorado/exporters.yaml` is gitignored — it holds your router addresses, the
same category as `.env`.

### 2. Grafana ClickHouse plugin

The `grafana-clickhouse-datasource` plugin is not bundled in the otel-lgtm
image. That image has no `docker-entrypoint.sh`, so `GF_INSTALL_PLUGINS` is
**not** honoured; `/otel-lgtm/run-grafana.sh` composes with `GF_PLUGINS_PREINSTALL`
instead, which is what `docker-compose.yml` sets from
`GRAFANA_PLUGINS_PREINSTALL` in `.env`.

Grafana downloads it on first start, so the host needs outbound internet once.
It lands in `/data/grafana/plugins` inside the `lgtm-data` volume and persists.
If the host is offline, set `GRAFANA_PLUGINS_PREINSTALL=` and install the plugin
into that volume manually.

The `ClickHouse` datasource is provisioned unconditionally in
`grafana/provisioning/datasources/datasources.yaml`. With the netflow profile
down it simply fails to resolve — expected and harmless, since no other
dashboard queries it.

### 3. Router

```sh
scp -O -r openwrt root@192.168.0.1:/tmp/
ssh root@192.168.0.1 \
  "OPENWRT_MONITOR_PROFILE=core,netflow sh /tmp/openwrt/setup.sh 192.168.0.100"
```

This installs `softflowd`, writes `/etc/config/softflowd`, installs
`openwrt-monitor-netflow-health.sh` on a one-minute cron, and starts the
service. Roll it out to the gateway first.

Relevant environment variables (full list in `setup.sh`'s header):

| Variable | Default | Notes |
|---|---|---|
| `NETFLOW_INTERFACES` | `br-lan` | The LAN bridge, not WAN — see limit 7. One softflowd instance each. |
| `NETFLOW_TIMEOUTS` | `maxlife=60` | See limit 6. |
| `NETFLOW_PORT` | `2055` | Must match `NETFLOW_PORT` in `.env`. |
| `NETFLOW_SAMPLING_RATE` | `1` | Keep in step with `default-sampling-rate`. |
| `NETFLOW_MAX_FLOWS` | `8192` | Raise if forced expiry appears. |
| `NETFLOW_DISABLE_HW_OFFLOAD` | `0` | See [limit 1](#1-hardware-flow-offload-hides-traffic). |

`/etc/config/softflowd` is rendered wholesale from
`openwrt/netflow/softflowd.config` on every run — edit the environment
variables, not the file on the router.

Do **not** add the WAN device alongside the bridge: every packet crosses both,
so all traffic would be counted twice.

### 4. Confirm it works

```sh
# Flows arriving and being stored
docker compose --profile netflow exec clickhouse clickhouse-client --query \
  "SELECT count() FROM flows WHERE TimeReceived > now() - INTERVAL 5 MINUTE"

# Nothing being dropped for unresolvable interfaces
curl -s http://127.0.0.1:8081/api/v0/outlet/metrics | grep -i error
```

Then open **OpenWrt - NetFlow** in Grafana and check the Pipeline Health tab
first: exporter running, flow data complete, no capture drops, no forced expiry.
The dashboard is generated from `scripts/build_openwrt_netflow_dashboard.py` and has
six tabs:

- **Flow Overview**: hero band (flows, traffic, peak bitrate, external share,
  IPv6 flow share, local hosts), peak link utilization, throughput by
  direction in both absolute and percent-stacked form, top local talkers,
  top external destinations, and top conversations.
- **Applications**: source/destination ports, protocol mix, service-class
  buckets as a donut and stacked over time, a packet-size-over-time heatmap,
  decoded TCP flow outcomes, and local service ports.
- **External**: AS and country resolution tiles, top source/destination AS and
  country, a world map of destination countries, a node graph of local hosts
  to the networks they talk to, and AS/country conversation matrices.
- **Security Signals**: reset rate, unanswered connection attempts, fan-out
  per local host, inbound traffic to local hosts, and ICMP volume. Shapes
  worth explaining, framed as such — not alerts.
- **Pipeline Health**: softflowd, Akvorado, ifIndex, drop, and flow-table
  integrity checks, plus an availability state timeline over the selected
  range and a direction cross-check against Akvorado's own `FlowDirection`.
- **Pipeline Internals**: Kafka consumer lag, ClickHouse insert latency and
  batch size, decoder throughput by record type, metadata cache hit ratio,
  and outlet worker load.

Pipeline Health answers "can I trust these numbers"; Pipeline Internals
answers "where in the collector is it struggling", and is only worth opening
once the first tab looks wrong.

Once GeoIP files are mounted, these ClickHouse checks prove the same fields the
External and Applications tabs need:

```sh
docker compose --profile netflow exec clickhouse clickhouse-client --query \
  "SELECT count() AS total, countIf(SrcAS != 0) AS src_as_rows, countIf(DstAS != 0) AS dst_as_rows FROM flows WHERE TimeReceived > now() - INTERVAL 15 MINUTE"

docker compose --profile netflow exec clickhouse clickhouse-client --query \
  "SELECT SrcAS, dictGetOrDefault('asns','name',toUInt64(SrcAS), '') AS as_name, count() AS rows FROM flows WHERE TimeReceived > now() - INTERVAL 15 MINUTE AND SrcAS != 0 GROUP BY SrcAS, as_name ORDER BY rows DESC LIMIT 20"

docker compose --profile netflow exec clickhouse clickhouse-client --query \
  "SELECT SrcPort, Proto, count() AS rows, sum(Bytes * SamplingRate) AS bytes FROM flows WHERE TimeReceived > now() - INTERVAL 15 MINUTE AND SrcPort > 0 GROUP BY SrcPort, Proto ORDER BY bytes DESC LIMIT 20"
```

Akvorado's own console is at <http://127.0.0.1:8081/>. It is bound to loopback
and is **unauthenticated** — upstream fronts it with an auth proxy, this stack
does not. Do not expose that port.

---

## Troubleshooting

Akvorado's own pipeline is inlet UDP → Kafka → outlet received → enriched →
ClickHouse, and each hop has a counter. Walk them in order; the first one that
stops increasing is where the problem is. Metric names below are from Akvorado's
troubleshooting guide.

```sh
# 1. Are packets arriving from the router at all?
curl -s http://127.0.0.1:8081/api/v0/inlet/metrics | grep akvorado_inlet_flow_input_udp_packets

# 2. Is the kernel receive buffer dropping them? (makes byte counts unreliable)
curl -s http://127.0.0.1:8081/api/v0/inlet/metrics | grep akvorado_inlet_flow_input_udp_in_dropped

# 3. Is the outlet receiving and storing them?
curl -s http://127.0.0.1:8081/api/v0/outlet/metrics | grep -E 'akvorado_outlet_core_(received|forwarded)'

# 4. If received > forwarded, this names the reason:
curl -s http://127.0.0.1:8081/api/v0/outlet/metrics | grep 'akvorado_outlet_core.*errors'

# 5. Are only templates arriving, and no data records?
curl -s http://127.0.0.1:8081/api/v0/outlet/metrics | grep akvorado_outlet_flow_decoder_netflow_records

# 6. One decoded flow, end to end:
curl -s 'http://127.0.0.1:8081/api/v0/outlet/flows?limit=1'
```

`metadata missing` in step 4 is the ifIndex problem. `sampling rate missing` is
normal for a moment at startup but must stop increasing. The dashboard's
**Collector Error Reasons** panel shows the same breakdown without shelling in.

On the router side:

```sh
softflowctl -c /var/run/softflowd-<iface>.ctl statistics   # counters
softflowctl -c /var/run/softflowd-<iface>.ctl dump-flows   # current flow table
tcpdump -ni <iface> host <monitoring-host> and port 2055   # is it actually sending?
```

Note the Kafka topic is created as **`flows-v5`**, not `flows` — Akvorado
appends its schema version to the configured topic name.

| Symptom | Likely cause |
|---|---|
| `flows` table empty, exporter up | ifIndex mismatch in `exporters.yaml`. Compare against `openwrt_netflow_ifindex`, and check `akvorado_outlet_core_*errors_total{error="metadata missing"}`. |
| Inlet/outlet counters rise but ClickHouse stays empty with `input and output interfaces missing` | softflowd was started without `ifindex:interface`. Rerun the current `openwrt/setup.sh`; it renders the prefix from `/sys/class/net/<iface>/ifindex`. |
| Throughput graphs flat, then huge spikes | `maxlife` too long, so flows are only exported when they end. See limit 6. |
| Byte counts unreliable, inlet drops climbing | Kernel receive buffer overflow. Raise `net.core.rmem_max` on the host and `receive-buffer` in `akvorado.yaml` together. |
| Only `OptionsDataFlowSet`/`OptionsTemplateFlowSet` records, no `DataFlowSet` | softflowd is sending templates but no data — nothing is being captured. Check the interface and the pcap filter. |
| `flows` table empty, exporter down | softflowd not running. Check `/etc/init.d/softflowd status` and that the package installed. |
| Flows present, all panels using `external` empty | WAN `description` does not start with `Transit: `, so nothing was classified external. |
| Byte counts ~100× too low | `sampling_rate` and `default-sampling-rate` are out of step. |
| Totals lower than `node_network_*` counters | Hardware flow offload. Expected — see limit 1. |
| Export failures climbing | UDP to the inlet failing. Check firewall between router and monitoring host on `NETFLOW_PORT`. |
| AS/country panels empty | GeoIP not configured, files use names not listed in `akvorado/akvorado.yaml`, or Akvorado was not restarted after adding them. See limit 4. Check the External tab's resolution tiles first: a low percentage with a populated map is normal (LAN-to-LAN traffic has no AS), 0% is a real fault. |
| Kafka consumer lag climbing, flows arriving late | The outlet cannot keep up with the inlet. Nothing is lost while Kafka still holds it, but recent panels read low. See the Pipeline Internals tab; check insert latency and outlet worker load to tell a slow ClickHouse from a slow outlet. |
| Only templates on the decoder panel, `DataFlowSet` flat at zero | softflowd is sending schema but capturing no packets. Same root cause as the `OptionsTemplateFlowSet` row above; check the capture interface and pcap filter. |
| Orchestrator logs `cannot watch database directory` | The `akvorado/geoip/` mount point is missing. It should exist even when empty. |
| Top Local Talkers empty, or every source is one address | Capturing on WAN instead of the LAN bridge, so sources are post-SNAT. See limit 7. |
| Everything in one direction bucket | Your LAN prefix is missing from `clickhouse.networks` in `akvorado.yaml`. |
| TCP flag panel errors on an unknown column | `schema.enabled: [TCPFlags]` missing from `akvorado.yaml` — the column is off by default in Akvorado. |
| Orchestrator will not start | `akvorado/exporters.yaml` missing. Deliberate: it refuses rather than discarding every flow. |

## Files

- `akvorado/akvorado.yaml` — orchestrator config (Kafka, ClickHouse, retention,
  inlet listeners, classifiers).
- `akvorado/exporters.yaml.example` — static interface metadata template.
- `akvorado/geoip/` — empty by default; optional MaxMind/IPinfo mount point for
  GeoIP enrichment. The default config expects `GeoLite2-ASN.mmdb`,
  `GeoLite2-City.mmdb`, and `GeoLite2-Country.mmdb`.
- `akvorado/clickhouse/` — ClickHouse server config (log TTLs, Prometheus endpoint).
- `openwrt/netflow/softflowd.config` — UCI template rendered by `setup.sh`.
- `openwrt/scripts/openwrt-monitor-netflow-health.sh` — exporter health collector.
- `scripts/build_openwrt_netflow_dashboard.py` — dashboard source. Edit this, never the
  generated JSON.

## Relationship to the earlier design

The original flow-logging approach was replaced by Akvorado, which provides a
real flow store and queryable schema instead of log lines. The router-side
softflowd limitations and CPU benchmark gate still apply and are documented
above.
