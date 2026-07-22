# Plan: Client Inventory, Network Node Graph, and Traffic Attribution

Status: proposal. Nothing in this document is implemented yet.

Revision note: this is a second draft. The first draft's recommendations for the
node graph datasource and the NetFlow collector were both changed after
research; §2.2, §3, and §7 say what changed and why. Claims are tagged
**[verified]** (checked against source, a package index, or upstream docs),
**[reasoned]** (follows from verified facts but not directly observed), or
**[unmeasured]** (needs a measurement on the actual router before it can be
trusted). The value of this document is that distinction.

### How to use this document

Sections 0–8 are the **design and its evidence**: what is true today, what was
decided, what was rejected, and what is still unknown. Sections 9–12 are the
**execution spec**: repo conventions, the canonical metric contract,
per-milestone file manifests with acceptance criteria, and the two spike
protocols.

If you are implementing this, read 0–8 once for context, then work from 9–12
and refer back. Do not start writing code until you have read §9 — it records
conventions in this repo that are load-bearing and non-obvious, several of
which exist because they were got wrong once already.

Three standing rules:

- **Respect the confidence tags.** `[verified]` — build on it. `[reasoned]` —
  build on it, but add a runtime check or availability flag so a wrong
  assumption is visible rather than silent. `[unmeasured]` / `[uncertain]` —
  resolve by testing before writing dependent code. §8 "Still open, honestly"
  lists the five outstanding ones. If you cannot test one, stop and report; do
  not guess and proceed.
- **Never ship a metric that reports a plausible wrong value.** An unavailable
  collector exports availability `0` and the dashboard renders "Not collected".
  A silently-zero metric is worse than a missing one.
- **State the cardinality cost of every new label**, in the commit message, and
  check it against §10 before adding it.

Four related goals, in the order they should be built:

1. **Client inventory table** — one Grafana table row per client device, with
   hostname, MAC, IP, which AP/router it is attached to, and its live metrics.
2. **Network node graph** — a Grafana node graph of internet → router → AP →
   radio/SSID → client, driven by the same identity data.
3. **Traffic attribution** — what each client is actually talking to, and how
   much. NetFlow is one way to get this and not the cheapest one.
4. **Cardinality cleanup** — the repo already ships one unbounded metric. It has
   to be fixed before anything is added on top of it.

Phase 1 is the foundation. Phases 2 and 3 both consume the identity model it
establishes, so it should not be skipped or built ad hoc inside a dashboard.
Phase 0 is small and should be done first because it is a live problem.

Hardware target throughout: ASUS RT-AX53U (MediaTek MT7621, 880 MHz dual-core
MIPS, 256 MB RAM, 128 MB flash), OpenWrt 24.10 (opkg) / 25.12 (apk). Package
architecture `mipsel_24kc`. This is a home network with tens of devices, not a
datacenter.

---

## 0. Where the repo is today

### 0.1 Client identity is spread across six metric families

**[verified]** — read from `openwrt/collectors/`, `openwrt/setup.sh`, and the
upstream `prometheus-node-exporter-lua` collector sources.

| Source | Metric | Identity labels |
|---|---|---|
| `openwrt/collectors/device_status.lua` | `router_device_up`, `router_device_status` | `device` (DHCP hostname), `mac`, `ip`, `status` |
| `openwrt/collectors/dnsmasq.lua` | `dhcp_lease` | `dnsmasq` (leasefile path), `ip`, `hostname`, `mac` (**uppercased**), value = expiry epoch |
| upstream `uci_dhcp_host` (required) | `uci_dhcp_host` | `name`, `dns`, `ip`, `duid`, `mac` (**uppercased**) |
| upstream `wifi_stations` (optional) | `wifi_station_*`, `wifi_stations` | `ifname`, `mac` (**lowercase**) |
| upstream `hostapd_stations` (optional) | `hostapd_station_*` | `vif`, `ssid`, `bssid`, `encryption`, `frequency`, `channel`, `station` (MAC) |
| `openwrt/collectors/device_traffic.lua` (`traffic`) | `openwrt_device_info`, `openwrt_device_traffic_bytes_total` | `device`, `ip`, `mac`, `interface` |
| upstream `nat_traffic` (required) | `node_nat_traffic` | `src`, `dest` — see §0.3 |

Two independent normalisation problems, not one:

- **Label name.** `mac` vs `station`, `ifname` vs `vif`.
- **Label value.** `dnsmasq.lua` and `uci_dhcp_host.lua` both call
  `string.upper(mac)`; `wifi_stations.lua` passes through the lowercase MAC
  from `iwinfo.assoclist`. A join between DHCP and WiFi data therefore fails
  even after `label_replace` fixes the label *name*. The first draft of this
  plan missed this; any join has to normalise case, and the collector in §1.1
  must pick one canonical form (lowercase, colon-separated) and stick to it.

The dashboards already paper over the label-name half. From
`grafana dashboards/openwrt-devices.json` **[verified]**:

```promql
hostapd_station_signal_dbm{job="openwrt", router="$router"}
  or (label_replace(label_replace(wifi_station_signal_dbm{...}, "station", "$1", "mac", "(.+)"),
                    "vif", "$1", "ifname", "(.+)")
      unless on(job, router) hostapd_station_signal_dbm{...})
```

and the bitrate panel in the same dashboard is a three-way `or` chain of that
shape, roughly 700 characters long. Every new panel repeats it, and it still
cannot answer "what is the hostname of `a4:83:e7:…`" because the WiFi metrics
carry only a MAC and the DHCP metrics carry only a hostname.

There is also no AP dimension. Multi-AP attribution today is only implicit in
the `router` label from `alloy/config.alloy`, which scrapes exactly one target
**[verified]**.

### 0.2 What the stack looks like

**[verified]** — `docker-compose.yml` is a single `grafana/otel-lgtm` container
plus `grafana/alloy`. No plugin installs, no external image pulls beyond those
two, so `docker compose up` works on a host with a warm image cache and no
internet. Provisioning is bind-mounted from `grafana/provisioning/`
(`dashboards/` and `datasources/`). Dashboards are generated by
`build_dashboards.py` (four classic), `build_openwrt_operations_dashboard.py`,
and `build_openwrt_advanced_dashboard.py`, each writing to **both**
`grafana/provisioning/dashboards/` and `grafana-dashboard-exports/`.

Profiles in `openwrt/setup.sh` are `core|traffic|wifi_mesh|dpi|full`, gated by
`profile_enabled()`, which is true when `OPENWRT_MONITOR_PROFILE` equals the
profile name or `full` **[verified]**. Note the consequence: profiles are not
composable — you get exactly one, or all of them. Adding `clients` and
`netflow` as two more mutually exclusive profiles makes "clients + traffic"
impossible without `full`. **Recommendation:** change `OPENWRT_MONITOR_PROFILE`
to accept a comma-separated list (`traffic,clients`) with `full` as the alias
for all, and keep single-value input working. This is a five-line change to
`profile_enabled()` and the validation `case`, and it should land in M1.

### 0.3 Phase 0: `node_nat_traffic` is already a cardinality bomb

**[verified]** — this is the most important thing the first draft missed.

`prometheus-node-exporter-lua-nat_traffic` is in `REQUIRED_PACKAGES`, so every
install of this repo has it. Its collector reads `/proc/net/nf_conntrack` and
emits, for every distinct source/destination pair in the conntrack table:

```lua
nat_metric = metric("node_nat_traffic", "gauge")
for src, values in next, nat do
  for dest, bytes in next, values do
    nat_metric({ src = src, dest = dest }, bytes)
```

That is a full src × dst cross product with **no bound of any kind**. One
laptop loading a news site with a dozen third-party domains contributes a dozen
series; those series then vanish when the conntrack entries expire and are
replaced by new ones on the next page load. The Devices dashboard consumes it
as `topk(10, sum by(src)(node_nat_traffic))` — the aggregate is bounded, the
stored data is not.

The everyday cost is churn, not a fixed series count: on a home LAN this is
plausibly thousands of new series per day, each alive for minutes
**[unmeasured]** — the actual number depends entirely on browsing habits and
should be measured before deciding how hard to fix it.

Three ways to fix it, in increasing order of effort:

1. **Drop the `dest` label at Alloy.** A `metric_relabel_configs` equivalent in
   `prometheus.scrape` that rewrites `node_nat_traffic` to keep only `src`.
   Cardinality collapses to one series per active local IP. Loses "to whom",
   which nothing currently displays anyway.
2. **Drop the metric entirely** and replace the Devices panel with the
   `traffic`-profile nftables counters, which are already per-device and
   bounded.
3. **Replace with a bounded on-router aggregation** — see §3.2.

**M0 result: (1) does not work — tested and rejected, not just theoretically
uncertain.** Alloy's relabel actions (`prometheus.relabel`) operate per-sample,
not as an aggregation. There is no action that sums values across a set of
same-timestamp samples before forwarding. Dropping the `dest` label at Alloy
would make every sample for a given `src` collide into the same output series
at the same scrape timestamp; Prometheus keeps the first and silently drops
the rest — it does not sum them. Measured against the live reference router on
2026-07-22: of 19 distinct `src` values, 11 (58%) had more than one destination
with a non-zero byte count in the same snapshot (one `src` had 8 simultaneously
active destinations, byte counts ranging from the low hundreds to 13.4M). A
same-scrape label-collapse would have silently under-reported the majority of
active clients' totals — exactly the "plausible wrong value" the standing
rules forbid. This was confirmed by inspection of Alloy's relabel semantics
and by testing against a live Alloy 1.x / Prometheus (bundled in
`grafana/otel-lgtm` 13.0.1) instance, not by simulation.

Option (3) (a bounded on-router aggregation, computed by summing on the router
itself before export) is the only way to preserve a correct per-`src` byte
total, but it is materially more work than a relabel change — a new collector
reading `/proc/net/nf_conntrack`, the same file `nat_traffic` already reads —
and was flagged to a human as the alternative to shipping wrong numbers. **The
choice made was (2): drop `node_nat_traffic` entirely.**
`alloy/config.alloy`'s `prometheus.relabel "openwrt_bound_cardinality"` now
does a plain `action = "drop"` on `__name__ = "node_nat_traffic"` — safe,
because nothing is being derived from the dropped samples, so there is no
collision/undercounting risk in dropping outright, only in trying to reshape.
Verified end-to-end: after the config reload, `node_nat_traffic`'s last
sample timestamp stopped advancing (new scrapes were happening, `up{job=
"openwrt"}` stayed `1`, other collectors kept updating) and the metric aged out
of Prometheus's 5-minute staleness window entirely — `count(node_nat_traffic)`
returned an empty result. The router-side `nat_traffic` collector and its
required package are unchanged; the metric is still generated on the router,
it is simply never stored.

Panels that used `node_nat_traffic` were updated, not just the metric:
`build_dashboards.py`'s classic Devices dashboard and
`build_openwrt_operations_dashboard.py`'s Operations dashboard both had a
`topk(10, sum by(src)(node_nat_traffic))`-shaped "top devices" ranking, now
replaced with `topk(10, sum by(device)(rate(openwrt_device_traffic_bytes_total
[...])))` from the `traffic` profile's nftables counters — already bounded,
already per-device, no correctness risk. The Operations dashboard additionally
had a **second, undocumented-by-the-first-draft panel**, "Top External
Destinations", ranking `topk(10, sum by(dest)(node_nat_traffic{...}))` — i.e.
keyed on raw remote IP, which §10.5 already names as a label that must never
exist on a Prometheus metric. This panel had no bounded replacement available
in-repo and was removed outright rather than given a wrong-shaped substitute;
"who did each client talk to" is deferred to the traffic-attribution phase
(§3), which buckets remote peers into `peer_kind`/Loki before they reach
Prometheus. This is a real, if modest, scope note: the original draft's §0.3
only accounted for one panel's worth of `node_nat_traffic` usage in the repo
as it existed at the time; a second had been added since.

**Conclusion:** phase 1 is "produce one authoritative client identity series",
not "add a table panel" — and phase 0 is "stop shipping an unbounded one".

---

## 1. Phase 1 — Unified client inventory

### 1.1 New collector: `openwrt/collectors/client_inventory.lua`

A new collector under a new `clients` profile in `openwrt/setup.sh`. It joins,
on the router, sources that are already local to it.

**Correction to the first draft:** the first draft proposed reading
`/tmp/dhcp.leases`, `/etc/config/dhcp`, `/proc/net/arp`, and
`/etc/config/wireless` by hand. OpenWrt already does exactly this join and
exposes it over ubus **[verified]**:

```sh
ubus call luci-rpc getHostHints
```

returns, keyed by MAC:

```json
{
  "a4:83:e7:aa:bb:cc": {
    "name": "living-room-tv",
    "ipaddrs": ["192.168.0.42"],
    "ip6addrs": ["fd00::42", "2001:db8::42"]
  }
}
```

`rpcd-mod-luci` builds this by merging, in ascending priority: the neighbour
table (10), `/etc/ethers` (50), the DHCP leasefile (100), reverse DNS (100),
`getifaddrs()` (200), and UCI static leases (200) **[verified]**. That is
strictly more sources than the hand-rolled version, it already handles IPv6,
and it is maintained upstream.

**Cost:** `getHostHints` requires `rpcd-mod-luci`, which is pulled in by LuCI.
A router with LuCI installed (the overwhelming majority, and certainly the
RT-AX53U reference device) already has it. `rpcd-mod-luci` alone is small.
Add it to the `clients` profile's optional package list and fall back to
parsing `/tmp/dhcp.leases` when the ubus call fails, matching the availability
pattern the existing profiles use.

**One caveat [verified]:** `getHostHints` performs reverse-DNS lookups as one of
its sources, and there is an upstream issue about those leaking local IPs to
the upstream resolver. On a home router with dnsmasq answering for the local
domain this is harmless, but it is worth a line in the docs.

So the collector's job reduces to: take `getHostHints` as the identity spine,
and add the two things it does not know — **which radio/SSID the client is
associated with**, and **whether it is associated at all**:

- `ubus call network.wireless status` → radio → interface (`ifname`) → SSID,
  the same call `wifi_dethrash.lua` already makes **[verified]**
- `iwinfo.assoclist(ifname)` → the set of associated MACs per interface, the
  same call upstream `wifi_stations.lua` makes **[verified]**
- UCI `network` → which bridge/zone each interface belongs to, for the
  guest-vs-trusted split (§4.6)

  **[verified against a live router, M1, superseding the three lines above]**
  — no separate UCI `wireless.wifi-iface` cross-reference is needed for SSID
  or network zone. `network.wireless status`'s own
  `interfaces[].config.ssid` and `interfaces[].config.network` carry both
  directly, and `radio.config.band` carries `"2g"`/`"5g"` directly — all
  confirmed present in real ubus output. `iwinfo` is needed only for
  `assoclist()`, not for SSID/frequency/network. See the M1 report in §11 for
  the two live bugs this simplification also fixed.

Emitted as one info-style gauge plus a small number of value metrics:

```
openwrt_client_info{mac="a4:83:e7:aa:bb:cc", hostname="living-room-tv",
                    ip="192.168.0.42", router="openwrt-main", ap="openwrt-main",
                    ssid="Home-5G", band="5g", ifname="wlan1",
                    connection="wifi", network="lan", static="0",
                    mac_type="global"} 1

openwrt_client_up{mac="a4:83:e7:aa:bb:cc"} 1
openwrt_client_lease_expiry_seconds{mac="a4:83:e7:aa:bb:cc"} 1.7e9
openwrt_client_ipv6_addresses{mac="a4:83:e7:aa:bb:cc"} 2
openwrt_client_first_seen_seconds{mac="a4:83:e7:aa:bb:cc"} 1.7e9
```

Design rules:

- **`mac` is the primary key, lowercase, colon-separated.** It is the only
  identifier present in every upstream source. The collector lowercases
  unconditionally. Dashboard joins against `dhcp_lease` / `uci_dhcp_host` must
  apply `lower()`-equivalent handling — Prometheus has no `lower()` for label
  values, so the honest options are (a) join only against
  `openwrt_client_info`, which is the point of this phase, or (b) if a raw join
  against the uppercase metrics is unavoidable, do it with an explicit
  `label_replace` per-nibble hack, which is not worth it. Prefer (a).
- **Attributes live only on `_info`.** Value metrics carry `mac` and nothing
  else, so a hostname or IP change does not reset a counter or fork a series.
- **`ap` is a distinct label from `router`.** On a single-router network they
  are equal. With dumb APs each AP runs the exporter with its own `router`
  value; `ap` names the box the client is *associated with*, which is what the
  table and the node graph both need.
- **`connection`** is one of `wifi`, `wired`, `unknown` — derived from whether
  the MAC appears in any `assoclist`.
- **`ip`** is `ipaddrs[0]` from `getHostHints` (already priority-sorted). IPv6
  addresses are **counted, not enumerated** — see §4.4.
- **`mac_type`** is `global` or `local`, computed as bit 1 of the first octet.
  This replaces the OUI vendor lookup — see §7 and §6.
- **`first_seen`** is persisted to `/etc/openwrt-client-seen` (not `/tmp`, so it
  survives reboot) and enables new-device alerting (§5.1). Bounded by capping
  the file at `CLIENT_INVENTORY_MAX` entries with LRU eviction.
- Bounded: cap at `CLIENT_INVENTORY_MAX` (default 256) and export
  `openwrt_client_inventory_collector_available` plus an
  `openwrt_client_inventory_truncated` flag, matching the existing pattern.
- Availability is reported **after** collection completes, and the whole
  collection runs under `pcall`. `device_traffic.lua` learned this the hard way
  and the comment explaining why is still in that file **[verified]** — the new
  collector must not repeat it.

### 1.2 Multi-AP collection

Replace the single `prometheus.scrape "openwrt"` in `alloy/config.alloy` with a
target list driven by a new `ROUTER_TARGETS` env var
(`name=ip:port,name=ip:port`), keeping `ROUTER_IP`/`ROUTER_NAME` working as the
single-target default. Alloy's `sys.env` returns a string, so the parsing is a
`split`/`map` in the config; alternatively use `discovery.file` with a small
generated targets file, which is easier to read.

This is a prerequisite for the node graph having more than one AP node, and for
roaming attribution (§4.5). On a single-router install it changes nothing.

Syslog labelling has the same single-router assumption
(`router = sys.env("ROUTER_NAME")` on both listeners) and will mislabel logs
from a second AP. Fix it in the same milestone by mapping
`__syslog_message_hostname` to `router` in `loki.relabel.openwrt_syslog`
instead of hardcoding.

### 1.3 The table panel

Once `openwrt_client_info` exists, the table is a vector-matching join, not a
transformation pile:

```promql
# Signal per client, carrying hostname/IP/AP
max by (mac, hostname, ip, ap, ssid, band, connection) (
  openwrt_client_info{router=~"$router"}
  * on (mac) group_left()
  (
    label_replace(hostapd_station_signal_dbm{job="openwrt"}, "mac", "$1", "station", "(.+)")
    or wifi_station_signal_dbm{job="openwrt"}
  )
)
```

Panel construction, using **Instant** queries so each returns a single frame:

| Query | Expression | Column |
|---|---|---|
| A | `openwrt_client_info{router=~"$router"}` | identity columns |
| B | signal join as above | Signal (dBm) |
| C | `openwrt_client_up * on(mac) group_left() openwrt_client_info` | Status |
| D | `sum by (mac) (rate(openwrt_client_bytes_total[$__rate_interval]))` | RX/TX bps |
| E | `openwrt_client_lease_expiry_seconds - time()` | Lease remaining |
| F | `openwrt_client_conntrack_entries` | Connections (§4.2) |

Query D depends on §3 delivering a per-MAC byte counter. Until then, fall back
to `openwrt_device_traffic_bytes_total`, which is keyed on `device` (the DHCP
hostname), not `mac` **[verified]** — so the join is on `device`, and it breaks
for any client without a DHCP hostname. That is a good argument for §3.2
landing early.

Transformations, in order:

1. `Join by field` on `mac` (outer join) — merges all queries into one frame.
2. `Organize fields` — rename `Value #A` → `Hostname`…, drop `job`/`instance`,
   set column order.
3. Field overrides — `Signal` as a coloured gauge cell, `Status` as a
   value-mapped coloured background, byte rates with `bps` unit, lease as `s`.
4. Row-level data links: MAC → the per-client drilldown dashboard.

Deliverable: a dedicated `build_openwrt_clients_dashboard.py` — see §7,
resolved.

### 1.4 Cardinality

`openwrt_client_info` is one series per client with ~12 labels. Sixty devices is
60 series; churn happens only when a hostname or IP changes. The four
`openwrt_client_*` value metrics add 4 × 60 = 240 more. Total: under 350 series
for the whole phase. That is nothing.

The rule to hold to: **no `mac` label on anything that is not either `_info` or
a per-client value metric that is already per-MAC today.** In particular, no
`mac` on anything crossed with a second dimension unless that dimension is
bounded and small (see §3.2, where the second dimension is a ~10-value service
bucket).

---

## 2. Phase 2 — Node graph

### 2.1 Required data shape

**[verified]** — read from `packages/grafana-data/src/utils/nodeGraph.ts` and
`public/app/plugins/panel/nodeGraph/utils.ts` in grafana/grafana `main`.

The field names in the frame contract are **all lowercase** in the enum, and
`getNodeFields`/`getEdgeFields` lowercase every incoming field name before
lookup, so `mainStat`, `mainstat`, and `MAINSTAT` all resolve. Verbatim:

| Constant | Field name | Applies to |
|---|---|---|
| `id` | `id` | nodes (required), edges (required) |
| `title` | `title` | nodes |
| `subTitle` | `subtitle` | nodes |
| `mainStat` | `mainstat` | nodes, edges |
| `secondaryStat` | `secondarystat` | nodes, edges |
| `arc` | `arc__` prefix | nodes |
| `icon` | `icon` | nodes |
| `color` | `color` | nodes, edges |
| `source` | `source` | edges (required) |
| `target` | `target` | edges (required) |
| `detail` | `detail__` prefix | nodes, edges |
| `nodeRadius` | `noderadius` | nodes |
| `thickness` | `thickness` | edges |
| `highlighted` | `highlighted` | **deprecated** (10.5); use `color` |
| `strokeDasharray` | `strokedasharray` | edges |
| `fixedX` / `fixedY` | `fixedx` / `fixedy` | nodes |
| `isInstrumented` | `isinstrumented` | nodes |

Rules that constrain the design:

- `arc__*` values **must sum to 1**.
- `fixedx`/`fixedy`: if either is present, *every* node must have a finite value
  for both, or `processNodes` throws.
- If a nodes frame is supplied, **every edge endpoint must exist in it** —
  `processNodes` does `nodesMap[e.target].incoming++` with no guard, so a
  dangling target is a panel crash, not a missing node.
- **The nodes frame is optional.** "Node graphs, at minimum, require a data
  frame describing the edges of the graph. By default, node graphs compute the
  nodes and any stats based on this data frame." **[verified]** Node stats are
  then the sum of incoming edge `mainstat`/`secondarystat` where those are
  numeric.

### 2.2 Getting those frames out of Prometheus — **the first draft was wrong**

The first draft recommended the Infinity datasource with two queries against
Prometheus' HTTP API, formats `Nodes - Node Graph` / `Edges - Node Graph`,
reshaped with UQL or JSONata, and accepted that this breaks the offline
`docker compose up` because the plugin has to be downloaded.

That is unnecessary. The frame-categorisation logic in Grafana is this, verbatim
**[verified]**:

```ts
// utils.ts — which frames are considered at all
let nodeGraphFrames = frames.filter((frame) => {
  if (frame.meta?.preferredVisualisationType === 'nodeGraph') return true;
  if (frame.name === 'nodes' || frame.name === 'edges' ||
      frame.refId === 'nodes' || frame.refId === 'edges') return true;
  const fieldsCache = new FieldCache(frame);
  if (fieldsCache.getFieldByName(NodeGraphDataFrameFieldNames.id)) return true;
  return false;
});

// utils.ts — nodes vs edges
export const getGraphFrame = (frames: DataFrame[]) => {
  return frames.reduce<GraphFrame>((acc, frame) => {
    const sourceField = frame.fields.filter((f) => f.name === 'source');
    if (frame.name === 'edges' || sourceField.length) {
      acc.edges.push(frame);
    } else {
      acc.nodes.push(frame);
    }
    return acc;
  }, { edges: [], nodes: [] });
};
```

Three consequences that kill the need for a plugin:

1. **`preferredVisualisationType` is not required in a dashboard panel.** It is
   how Explore picks a visualisation. `NodeGraphPanel` only calls
   `getNodeGraphDataFrames`, which accepts a frame that merely *has an `id`
   field*.
2. **`refId` is enough.** These dashboards are generated JSON, so setting
   `"refId": "nodes"` and `"refId": "edges"` on the two targets is a one-line
   change in the builder, not a datasource capability.
3. **A frame is an edges frame iff it has a field literally named `source`**
   (this comparison is case-sensitive, unlike the field lookups). A Prometheus
   instant query in **Table** format produces one string field per label. So a
   metric with labels `id`, `source`, `target` produces an edges frame directly.

**Revised recommendation: emit the node graph contract from the router as
labels, and query it with the existing Prometheus datasource.**

```
openwrt_topology_node{id="client:a4:83:e7:aa:bb:cc", title="living-room-tv",
                      subtitle="192.168.0.42", icon="laptop",
                      detail__mac="a4:83:e7:aa:bb:cc", detail__ssid="Home-5G",
                      arc__online="1", arc__offline="0"} 1

openwrt_topology_edge{id="assoc:a4:83:e7:aa:bb:cc",
                      source="ssid:Home-5G@5g",
                      target="client:a4:83:e7:aa:bb:cc"} 1
```

Panel targets:

```json
{ "refId": "nodes", "format": "table", "instant": true,
  "expr": "openwrt_topology_node{router=~\"$router\"}" },
{ "refId": "edges", "format": "table", "instant": true,
  "expr": "openwrt_topology_edge{router=~\"$router\"} * on (id) group_left() openwrt_topology_edge_bps" }
```

with one `Organize fields` transformation per frame to rename the Prometheus
`Value` column to `mainstat` and drop `Time`, `job`, `instance`, `router`,
`__name__`.

Why this beats the Infinity approach:

- No plugin download, so `docker compose up` stays offline-capable — which was
  the first draft's own stated objection to Infinity, now simply avoided.
- No UQL/JSONata reshaping step, whose feasibility the first draft assumed.
  Research found the Infinity node graph docs say nothing about UQL
  compatibility either way **[verified — it is genuinely undocumented]**, and
  no worked example of Prometheus → Infinity → node graph exists in the
  community forums. Two threads asking for exactly this
  (`community.grafana.com/t/…/138141`, `…/108650`) both end with no working
  answer **[verified]**. Building on an undocumented path was the weakest part
  of the first draft.
- The mainstat is a live Prometheus number, so Grafana units and thresholds
  apply normally.

The trade-off, stated honestly: `title`, `subtitle`, `icon`, and `detail__*`
become **strings computed on the router**, not Grafana-side formatting. A
hostname change rewrites the series. That is acceptable for identity strings;
it would not be acceptable for anything numeric, which is why the numbers stay
as metric values.

Only one number per frame comes free (the metric value → `mainstat`). A
`secondarystat` needs a second query joined on `id` with a `Join by field`
transformation. **Recommendation: skip `secondarystat` in v1.** One number per
node and per edge is enough for a home network, and the transformation chain is
where this kind of panel becomes unmaintainable.

**M4 result: pass, tested against bundled Grafana 13.0.1 (`a100054f`) on
2026-07-22.** A temporary local Prometheus exposition was served from
`/tmp/openwrt-m4-spike/metrics` on the host and added only to the running
Alloy file-SD target list as `router="m4-spike"` / `job="openwrt"`; no router
collector, production metric, committed generator, or generated dashboard JSON
was added. Grafana accepted temporary API-created node graph dashboards using
the existing Prometheus datasource, instant queries, table format, and
`refId="edges"` / `refId="nodes"`. Browser verification through headless
Firefox/WebDriver confirmed:

- edges-only rendered all three required edges, and edge values populated
  node stats (`router:test` = `100`, `client:aa` = `42`, `client:bb` = `17`);
- adding the `nodes` frame rendered the same graph with node titles/subtitles
  from `id`, `title`, `subtitle`, `arc__ok`, and `arc__warn` labels and no
  visible panel error;
- removing the `client:bb` node and its edge from the temporary exposition
  left a valid two-edge graph and did not crash the panel.

Nuance for M5: in the temporary classic dashboard, a single `organize`
transformation renaming `Value` to `mainstat` applied to both the edges and
nodes frames. That is fine if topology nodes should expose their own numeric
node `mainstat`; if M5 wants edge-derived node stats while also supplying a
nodes frame, do not also provide/rename a node-frame `mainstat` field.
Grafana's render endpoint itself returned HTTP 500 because the image renderer
plugin is not available in `grafana/otel-lgtm`; screenshot evidence came from
WebDriver instead. No Grafana dashboard/API errors were observed during the
successful browser checks; the only recorded error was the expected render
plugin absence from the failed `/render/d-solo` attempt.

Fallback, if that test fails: a small topology service container that scrapes
Prometheus and serves `/nodes` + `/edges` JSON, with Infinity in front of it.
That is strictly more machinery, and it is the *fallback*, not the plan.

### 2.3 Topology model

```
internet ──wan──> router:<name> ──ap──> ap:<name> ──radio──> ssid:<ssid>@<band> ──assoc──> client:<mac>
                                                    └──lan──> client:<mac>   (wired)
```

Node IDs are namespaced strings (`client:a4:83:e7:aa:bb:cc`, `ap:openwrt-main`,
`ssid:Home-5G@5g`, `router:openwrt-main`, `internet`) so the two frames can be
generated independently and still join. Because a dangling edge endpoint
crashes the panel (§2.1), the collector must emit both frames from the same
snapshot, in the same scrape.

Per node type:

| Type | title | subtitle | mainstat (metric value) | arc__ | icon |
|---|---|---|---|---|---|
| `internet` | ISP / WAN | public IP | WAN latency ms | `arc__up`/`arc__down` from `openwrt_wan_probe_success` | `cloud` |
| `router` | router name | model | load1 | `arc__ok`/`arc__warn` from service health | `sitemap` |
| `ap` | AP name | uptime | client count | `arc__ok`/`arc__warn` | `wifi` |
| `ssid` | SSID | band + channel | station count | — | `signal` |
| `client` | hostname | IP | throughput bps | `arc__online`/`arc__offline` | `laptop` |

Edges:

| Edge | source → target | mainstat | thickness | color |
|---|---|---|---|---|
| `wan` | `internet` → `router:*` | WAN throughput | scaled | — |
| `ap` | `router:*` → `ap:*` | backhaul throughput | — | dashed via `strokedasharray` if wireless backhaul |
| `radio` | `ap:*` → `ssid:*` | aggregate station throughput | — | — |
| `assoc` | `ssid:*` → `client:*` | per-client throughput | scaled | red when signal < −70 dBm |
| `lan` | `router:*` → `client:*` | per-client throughput | scaled | — |

`detail__mac`, `detail__ip`, `detail__signal`, `detail__lease_expiry` on client
nodes give a useful click-through. Note that **edge details are never rendered**
in the default view — the Infinity discussion thread reports "the details for
edges are never rendered, as they are only available on grid mode"
**[verified]**. Put nothing important in `detail__*` on edges.

`arc__*` must sum to 1, so the online/offline pair is `1,0` or `0,1` — a
two-state ring, which is all it is good for here. Anything richer wants `color`.

Everything above derives from `openwrt_client_info` plus metrics that already
exist. Nothing new is needed on the router for phase 2 beyond a small
`topology.lua` that reshapes phase 1's data into the two metric families.

### 2.4 Deliverable

`build_openwrt_topology_dashboard.py` producing
`grafana/provisioning/dashboards/openwrt-topology-v2.json` and the matching
export, following the existing dual-output convention.

---

## 3. Phase 3 — Traffic attribution

### 3.1 What the question actually is

Phases 1 and 2 show *who is on the network*. Phase 3 answers some subset of:

- **(a) How much** does each client transfer? — already answered by the
  `traffic` profile's nftables counters, keyed on hostname.
- **(b) What kind of traffic** is it? (ports/services)
- **(c) Who is it talking to?** (remote IPs, ASNs, hostnames)
- **(d) Which application** is it? (DPI)

These have wildly different costs. The first draft treated them as one problem
called "NetFlow" and jumped straight to a packet-capture exporter. That is the
most expensive answer to (b) and only a partial answer to (c).

### 3.2 Re-ranked options

**[verified]** against the OpenWrt 24.10 `mipsel_24kc` package index
(`downloads.openwrt.org/releases/24.10.0/packages/mipsel_24kc/`).

| Option | Answers | In the feed? | Installed size | Mechanism | Verdict |
|---|---|---|---|---|---|
| **nlbwmon** | a, b | **yes** (`nlbwmon`, 2024.02.21) | **80 KB** | conntrack netlink, zero-on-read | **Recommended first.** |
| netifyd | a, b, d | **yes** (`netifyd` 4.4.7) | 2.7 MB | libpcap + nDPI | Already half-integrated (`dpi` profile). Extend it. |
| softflowd → collector | a, b, c | **yes** (`softflowd` 1.1.0) | 140 KB + libpcap | libpcap on `br-lan` | Only for (c), and only after measuring. |
| conntrack-based custom collector | a, b, c | n/a (`conntrack` CLI is in the feed, 80 KB) | — | `/proc/net/nf_conntrack` | Already how `nat_traffic` works. Bound it and it is viable. |
| pmacct / nfacctd | a, b, c | **no** | ~1 MB+ | libpcap or NFLOG | See below. |
| ipt-netflow | a, b, c | **no** | kernel module | iptables target | Out-of-tree kmod. Cut. |
| ntopng | a–d | **no** | ~30 MB+ | libpcap + redis | Nowhere near 128 MB flash. Cut. |
| hsflowd (sFlow) | a, b, c (sampled) | **no** | — | sampling | Not packaged. Cut. |

**pmacct, evaluated properly as requested.** Its appeal is real: `pmacctd` can
aggregate on the router before export, which is exactly the right architecture
for a constrained device — `aggregate: src_host, dst_port, proto` collapses a
flow table into a handful of rows before a byte leaves the box, and it can
write to a file or to a memory table read by `pmacct -s` rather than speaking
NetFlow at all. If it were packaged, it would be a serious contender.

It is not packaged. `net/pmacct` does not exist in `openwrt/packages`
**[verified — `contents/net/pmacct` returns 404]** and `pmacct` is absent from
the 24.10 `mipsel_24kc` index **[verified]**. Using it means maintaining an
OpenWrt package inside this repo, which is a different project from
"observability config for a home router". **Cut, with the reason recorded** so
nobody re-derives it. If someone packages pmacct for OpenWrt, revisit — it is a
better fit than softflowd on every axis except availability.

### 3.3 Recommended: nlbwmon

**[verified]** from `jow-/nlbwmon` `database.h` and README, and the package
index.

nlbwmon is jow-'s conntrack-netlink traffic accountant. Its record is:

```c
struct record {
	uint8_t  family;        /* AF_INET / AF_INET6 */
	uint8_t  proto;
	uint16_t dst_port;
	union { struct ether_addr ea; uint64_t u64; } src_mac;
	union { struct in6_addr in6; struct in_addr in; } src_addr;
	uint64_t count;         /* connections */
	uint64_t out_pkts, out_bytes, in_pkts, in_bytes;
};
```

Why this is the right primitive here:

- **Keyed on `src_mac`** — the same primary key as §1. No IP→MAC join needed
  anywhere.
- **Bounded by construction.** The key is (family, proto, port, MAC, IP). The
  port dimension is bounded by nlbwmon's protocol file, which ships ~45 port
  definitions and classifies everything else as `other`.
- **No libpcap, no promiscuous mode.** It pulls counters over a conntrack
  netlink socket with zero-on-read, and gets a notification when an entry is
  destroyed so nothing is lost on teardown. This is the cheapest possible way
  to get per-client byte counts on Linux.
- **80 KB installed, 17 KB download, deps `libubox`/`libnl-tiny`/`zlib`/
  `kmod-nf-conntrack-netlink`** — all of which a router running firewall4
  already has except the kmod.
- `nlbw -c json` / `nlbw -c csv -g mac` gives machine-readable output, so the
  collector is a thin shell/Lua wrapper, in the same shape as the existing
  helper scripts.

**The honest limitation: nlbwmon does not record the remote endpoint.** The
record has `src_addr` (the local host) and `dst_port`, and no `dst_addr`. So it
answers (a) and (b) completely and (c) not at all. If "which server did the TV
talk to" is a requirement, nlbwmon alone does not deliver it.

Proposed metrics, under a `clients` (not `netflow`) profile:

```
openwrt_client_bytes_total{mac, direction="in|out", service}     # service from nlbwmon's proto file
openwrt_client_packets_total{mac, direction="in|out", service}
openwrt_client_connections_total{mac, service}
```

**Cardinality:** 60 clients × 2 directions × ~12 service buckets = 1440 series
for bytes, same again for packets, 720 for connections. ~3600 series. That is
fine, but it is 10× phase 1, so **trim the protocol file** on install to the
buckets worth charting (`https`, `http`, `dns`, `quic`, `ssh`, `smb`, `ntp`,
`imaps`, `rtp`, `other`) rather than shipping the default ~45.

**Router cost [measured, idle-only]:** M6 measured the reference MT7621 for
two adjacent 60-second windows on 2026-07-22, first with `nlbwmon` stopped and
then with it running again. Aggregate CPU busy time was 10.19% stopped versus
10.45% running (a 0.26 percentage-point difference under naturally variable
idle household traffic); `node_load1` fell from 1.32 to 1.16, and all four
`openwrt_softnet_*` dropped counters remained zero. The per-CPU processed
packet deltas varied with traffic, so this is evidence of no observable idle
softnet/drop regression, not a throughput-cost guarantee. Re-measure during a
representative transfer before making performance claims.

**Accounting period.** nlbwmon's counters are cumulative within an accounting
period, one month by default, and it keeps 10 generations. Exposed as a
Prometheus counter, the monthly rollover is a counter reset, which `rate()`
handles. No special handling needed, but say so in the docs so nobody
"fixes" it.

### 3.4 The offload problem — this is the real risk, and it is shared

**[verified]** and it applies to nlbwmon, to the repo's *existing* `traffic`
profile, to `nat_traffic`, and to softflowd, in different ways.

- **Hardware flow offload (MT7621 PPE).** Offloaded packets bypass the kernel
  entirely; they are forwarded by the packet processing engine and never reach
  `netif_receive_skb`. Consequences: conntrack byte counters do not advance,
  nftables counters do not advance, and `tcpdump`/libpcap on `br-lan` sees
  nothing. OpenWrt issue #10947 documents exactly this ("no packet seen with
  tcpdump on DSA port with MT7621/MT7530"), and the PPE bypass is confirmed in
  the discussion of the conntrack/ppe0 desync bug (#17915). **With hardware
  offload on, every option in §3.2 that is not netifyd-in-the-forwarding-path
  silently reports near-zero.** This is not a degradation, it is a total
  failure that looks like an idle network.
- **Software flow offload.** Same class of problem — OpenWrt issue #10399,
  "firewall4: counts bytes and packets incorrectly with offloading enabled";
  only packets that traverse the full kernel path are counted. There *is* a
  mitigation: `nft add flowtable inet fw4 ft { counter }` syncs counters back
  from the fast path, reported at roughly 3 % throughput cost **[verified as a
  reported figure, not independently measured]**.
- **Default state.** OpenWrt ships both offloads **disabled**
  (`option flow_offloading '0'` in `/etc/config/firewall`), so an untouched
  router is fine. But MT7621 users chasing gigabit WAN routinely turn hardware
  offload on, and the RT-AX53U is exactly that class of device.

**Required actions:**

1. The `clients` profile setup must **detect** offload
   (`uci get firewall.@defaults[0].flow_offloading` and `flow_offloading_hw`)
   and export it: `openwrt_flow_offload_enabled{mode="sw|hw"}`.
2. The dashboards must show "traffic accounting unreliable — flow offload is
   enabled" as an explicit state, in the same spirit as the existing
   `Not collected` handling, rather than rendering a plausible wrong zero.
3. `docs/advanced-profiles.md` already carries a one-line warning about this for
   the nftables counters **[verified]**. Promote it to a proper section covering
   all accounting paths and the flowtable-counter mitigation.

This is the single most important correction in this document. The first draft
listed "router CPU under softflowd" as the biggest risk. It is not — the
biggest risk is that the entire measurement approach produces zeros on a
configuration many owners of this hardware actually run, and does so silently.

### 3.5 softflowd, if (c) is genuinely required

Only reach for this after nlbwmon is in place and someone still wants remote
endpoints.

**CPU cost [unmeasured — and the first draft asserted it was fine].** The first
draft said "the ASUS RT-AX53U handles a home LAN unsampled". There is no
evidence for that. What research found is contradictory: softflowd's own
documentation claims minimal overhead by design, while OpenWrt forum reports
describe it as "pretty CPU intensive" and advise checking load before and after
**[verified as reports, no numbers for MT7621]**. Separately, MT7621 is
documented as CPU-bound around 500–700 Mbit/s of routing with ~25 % sirq even
without any capture **[verified as forum reports]**. Adding an AF_PACKET copy
of every forwarded packet to that budget is plausibly significant and is
**unmeasured on this hardware**. Treat any deployment as an experiment with a
before/after load comparison, and do not ship it as a default.

Corrections to the first draft's config:

- **The stock UCI init cannot express two `-t` flags.** `softflowd.init` maps
  `option timeout` through a single `append_string … '-t'` **[verified]**, so
  `-t maxlife=60 -t expint=15` is not expressible. Use one timeout and accept
  the default expiry interval, or ship a procd override.
- **The shipped default config is `export_version 5`, `track_ipv6 0`,
  `sampling_rate 100`** **[verified]** — i.e. enabling softflowd as-shipped
  gives IPv4-only NetFlow v5 sampled 1-in-100. Byte counts then need ×100
  scaling and are statistically meaningless for small flows. The template must
  override all three.
- **The exporter will capture its own export traffic.** The monitoring host is
  on the LAN, so NetFlow packets to `192.168.0.100:2055` egress `br-lan` and
  are captured by softflowd, which exports them, which generates more packets.
  A pcap filter is mandatory, not optional.

`openwrt/netflow/softflowd.config`, as a UCI template:

```
config softflowd
	option enabled          '1'
	option interface        'br-lan'
	option export_version   '9'          # v5 has no IPv6; this repo ships IPv6 monitoring
	option track_ipv6       '1'
	option host_port        '__MONITORING_HOST__:2055'
	option max_flows        '8192'
	option timeout          'maxlife=60' # only one -t is expressible via UCI
	option tracking_level   'full'
	option sampling_rate    '1'          # 1 = unsampled; the package default is 100
	option pid_file         '/var/run/softflowd.pid'
	option control_socket   '/var/run/softflowd.ctl'
	# Positional pcap filter, appended last by the init script.
	# Without this the exporter captures and re-exports its own NetFlow packets.
	option filter           'not (host __MONITORING_HOST__ and port 2055)'
```

Health monitoring: `openwrt/scripts/openwrt-monitor-netflow-health.sh` exporting
`openwrt_netflow_exporter_up` plus softflowd's own counters from
`softflowctl statistics`, so a drowning exporter is visible rather than
silently dropping flows.

**Capture-point note [reasoned].** `br-lan` is the right interface: on `wan`
every flow is post-NAT and appears to come from the router. Capturing on
`br-lan` sees routed LAN↔WAN traffic in both directions. It does **not** see
LAN↔LAN traffic between two switch ports, because DSA offloads that bridging
into the MT7530 switch — which is a feature here (a NAS-to-PC copy will not
drown the exporter) but means "top talkers" excludes intra-LAN transfers. Say
so in the docs rather than letting someone discover it.

### 3.6 Collector side: flowlogs-pipeline, with one claim corrected

**[verified]** against `netobserv/flowlogs-pipeline` `docs/api.md` and
`pkg/api/api.go` on `main`.

The first draft's core recommendation holds up. What it got wrong is the
enrichment.

What is confirmed:

- **NetFlow v9 ingest.** `IpfixType = "ipfix"` (`"collector"` is a deprecated
  alias). Config keys: `hostName`, `port` (IPFIX/NetFlow v9), `portLegacy`
  (NetFlow v5), `workers`, `sockets`, `mapping`. Under the hood it is goflow2,
  so field names are goflow2's: `SrcAddr`, `DstAddr`, `SrcPort`, `DstPort`,
  `Proto`, `Bytes`, `Packets`, `SamplerAddress`, `TimeFlowStartMs`.
- **Prometheus encode.** `prom` with `metrics[].{name,type,help,filters,valueKey,labels,buckets,valueScale}`, plus `prefix`, `expiryTime`
  (default 2m) and — usefully — **`maxMetrics`**, a hard ceiling on reported
  series that the first draft did not know about. Set it.
- **Aggregation.** `aggregates.rules[].{name,groupByKeys,operationType,operationKey,expiryTime}`
  with `operationType` in `sum|min|max|count|avg|raw_values`.
- **TopK.** `timebased.rules[].{name,indexKeys,operationType,operationKey,topK,reversed,timeInterval}`.
- **Service naming.** `transform network` rule `add_service` maps port+protocol
  to a service name from `/etc/services`.
- **Subnet classification.** `add_subnet_label` with a `subnetLabels` list of
  `{cidrs, name}` — this is the right mechanism for guest / trusted / external.

**What is wrong in the first draft:** "FLP's transform stage should enrich the
flow's LAN IP into the MAC from phase 1's `openwrt_client_info` … it works from
a static file or an HTTP lookup". **There is no generic file or HTTP lookup
table in FLP.** The complete list of `transform network` rule types is
`add_subnet`, `add_location`, `add_service`, `add_kubernetes`,
`add_kubernetes_infra`, `reinterpret_direction`, `add_subnet_label`,
`decode_tcp_flags` **[verified]**. `add_location` takes a `file_path` but it is
specifically an ip2location Lite DB9 zip. There is no IP→MAC path.

Nor does the flow carry a MAC: goflow2's message has `SrcMac`/`DstMac` fields,
but softflowd's NetFlow v9 templates do not populate them **[reasoned from
softflowd's template set — verify on-device before relying on it]**, so they
would arrive as `00:00:00:00:00:00`.

**Corrected approach: key flow metrics on `ip`, join to `mac` in PromQL against
`openwrt_client_info`.** This is what the first draft listed as its fallback;
it is the only option.

```promql
sum by (mac, service, direction) (
    openwrt_flow_bytes_total{peer_kind="external"}
  * on (ip) group_left(mac)
    label_replace(openwrt_client_info, "ip", "$1", "ip", "(.+)")
)
```

`netflow/flowlogs-pipeline.yaml`, concrete:

```yaml
log-level: info
pipeline:
  - name: ingest
  - name: enrich
    follows: ingest
  - name: aggregate
    follows: enrich
  - name: prom
    follows: aggregate
  - name: loki
    follows: enrich

parameters:
  - name: ingest
    ingest:
      type: ipfix
      ipfix:
        hostName: 0.0.0.0
        port: 2055          # NetFlow v9 from softflowd
        portLegacy: 0       # v5 disabled
        workers: 1
        sockets: 1

  - name: enrich
    transform:
      type: network
      network:
        subnetLabels:
          - name: trusted
            cidrs: ["192.168.0.0/24"]
          - name: guest
            cidrs: ["192.168.3.0/24"]
        rules:
          - type: add_subnet_label
            add_subnet_label: { input: SrcAddr, output: srcKind }
          - type: add_subnet_label
            add_subnet_label: { input: DstAddr, output: dstKind }
          - type: add_service
            add_service: { input: DstPort, output: service, protocol: Proto }

  - name: aggregate
    extract:
      type: aggregates
      aggregates:
        defaultExpiryTime: 60s
        rules:
          - name: client_egress_bytes
            groupByKeys: [SrcAddr, service, dstKind]
            operationType: sum
            operationKey: Bytes
          - name: client_ingress_bytes
            groupByKeys: [DstAddr, service, srcKind]
            operationType: sum
            operationKey: Bytes

  - name: prom
    encode:
      type: prom
      prom:
        prefix: openwrt_flow_
        expiryTime: 5m
        maxMetrics: 5000          # hard ceiling; nothing reaches Prometheus past this
        metrics:
          - name: bytes_total
            type: counter
            valueKey: recent_op_value
            filters:
              - key: name
                value: client_egress_bytes
                type: equal
            labels: [SrcAddr, service, dstKind]
            remap:
              SrcAddr: ip
              dstKind: peer_kind

  - name: loki
    write:
      type: loki
      loki:
        url: http://otel-lgtm:3100
        staticLabels: { job: openwrt-flows }
        labels: [srcKind]         # low-cardinality stream labels ONLY
```

**[uncertain]** — `valueKey: recent_op_value` and the exact aggregate output key
name should be checked against FLP's aggregate output before this is
implemented; the API doc names the config fields but not the emitted key. Treat
the YAML above as structurally correct and field-name-provisional.

`docker-compose.yml` addition:

```yaml
  flowlogs-pipeline:
    image: quay.io/netobserv/flowlogs-pipeline:main
    container_name: flowlogs-pipeline
    ports:
      - "${NETFLOW_PORT:-2055}:2055/udp"
      - "9102:9102"          # Prometheus metrics, scraped by Alloy
    volumes:
      - ./netflow/flowlogs-pipeline.yaml:/etc/flp/config.yaml:ro
    command: ["--config", "/etc/flp/config.yaml"]
    restart: unless-stopped
    profiles: ["netflow"]    # opt-in; `docker compose --profile netflow up`
```

The compose `profiles:` key keeps the default `docker compose up` a two-container
stack, which preserves the current offline behaviour.

Alternatives kept as documented options, not recommendations:

| Project | Fit here |
|---|---|
| [goflow2](https://github.com/netsampler/goflow2) | The decoder FLP already embeds. Useful standalone only if you want raw JSON to Loki with no aggregation. |
| [ktranslate](https://github.com/kentik/ktranslate) | Fastest path to a prebuilt NetFlow dashboard via the Grafana Cloud integration. Heavier and Kentik-oriented. Document, do not adopt. |
| nfCollector / goNfCollector | **Cut** — requires InfluxDB in a deliberately Prometheus+Loki stack. |

### 3.7 The cardinality rules, unchanged in substance

Raw flows are unbounded: `src × dst × sport × dport × proto`. Prometheus must
never see that.

- **Never label with ports as-is.** Bucket into `service` via `add_service`,
  then filter to a known list.
- **Never label with the raw remote IP.** Classify to `peer_kind` via
  `add_subnet_label`. Local peers may keep their IP (bounded by client count);
  external peers do not.
- **`topk` at the pipeline, not the query**, plus `maxMetrics` as a hard stop.
- **Full-fidelity flows go to Loki, not Prometheus**, with only `srcKind` as a
  stream label and everything else in the log line. "What did this device talk
  to at 3am" then becomes a LogQL query with Loki-bounded retention.

---

## 4. Metrics and features worth adding

Each with its cardinality cost and whether the router can produce it cheaply.

### 4.1 Per-client DNS query attribution — **adopt, and it is nearly free**

dnsmasq's `option logqueries '1'` in `/etc/config/dhcp` maps to
`--log-queries=extra`, which prefixes each line with a query serial and the
requesting client's IP **[verified]**. Those lines go to syslog, and **this repo
already ships syslog to Loki** **[verified]**.

- **Prometheus cardinality: zero.** Nothing new is stored as a series.
- **Router cost:** dnsmasq writes a log line per query. On a home network that
  is maybe a few queries per second; the cost is syslog I/O and the UDP send,
  not CPU.
- **What you get:** "which domains did this client resolve", per-client query
  rate, NXDOMAIN spikes, DNS-based device fingerprinting — all as LogQL.
- **Caveats that must be documented:** (i) this is a full browsing history of
  every person in the household, retained for as long as Loki keeps it — it
  must be **opt-in and loudly flagged**; (ii) it materially increases syslog
  volume; (iii) clients using DoH/DoT bypass dnsmasq entirely and will show
  nothing, which is itself worth a panel.

If a Prometheus counter is wanted, derive it in Alloy as a `loki.metric`-style
count keyed on **client IP only**, never on domain. Domain is unbounded.

### 4.2 Per-client conntrack entries — **adopt**

`/proc/net/nf_conntrack` is already read once per scrape by `nat_traffic`
**[verified]**, so the data is free; the fix is to count by source instead of
emitting the cross product.

```
openwrt_client_conntrack_entries{mac}
```

- **Cardinality:** one series per client. ~60.
- **Router cost:** none beyond what `nat_traffic` already spends. If §0.3 ends
  in replacing `nat_traffic` outright, this collector absorbs its job.
- **Why it earns its place:** a device with 400 open connections is either
  BitTorrent or compromised, and neither is visible today. Pairs with the
  existing `nf_conntrack` limit metrics for a "which client is filling the
  table" answer.

### 4.3 Roaming events between APs — **adopt, via Loki, not Prometheus**

hostapd logs `AP-STA-CONNECTED` / `AP-STA-DISCONNECTED` with the station MAC
**[verified]**, to syslog, which already reaches Loki.

- **Cardinality:** zero in Prometheus if left as logs.
- **Router cost:** zero — the lines are already being emitted and shipped.
- **What you get:** a roam timeline per client, "sticky client" detection (a
  phone holding a −78 dBm association on the far AP), and association-storm
  detection. Rendered as a Loki panel on the clients dashboard and as
  annotations on the signal timeseries.
- If a metric is wanted, count events **per AP and SSID, not per MAC**:
  `openwrt_wifi_assoc_events_total{ap, ssid, event}` — ~2 APs × 3 SSIDs × 2
  events = 12 series. Per-MAC roam counters would be 60 × 2 = 120 series with
  high churn and are not worth it; the log timeline is better anyway.

### 4.4 IPv6 neighbour tracking — **adopt in reduced form**

The first draft said "`openwrt_client_info` should carry the MAC from the
neighbour table and treat IPv6 addresses as an additional attribute". Correct
instinct, but `getHostHints` already returns `ip6addrs` as an array
**[verified]**, and a modern host has a link-local, a stable GUA, and one or
more rotating privacy addresses — putting them on labels means the info series
churns every time the privacy address rotates.

- **Adopt:** `openwrt_client_ipv6_addresses{mac}` (a count) and a
  `has_ipv6="0|1"` label on `_info`.
- **Reject:** any label carrying an IPv6 address.
- **Cardinality:** one series per client.
- **Router cost:** none — it is a field of a call already being made.

### 4.5 Guest vs trusted network separation — **adopt**

A `network` label on `openwrt_client_info`, resolved from the client's interface
to its UCI network (`lan`, `guest`, `iot`).

- **Cardinality:** none — it is a label on an existing series, and it is
  functionally dependent on the client, so it adds no new combinations.
- **Router cost:** one UCI read at collector start.
- **What it unlocks:** a dashboard variable, per-zone rollups, and the alert in
  §5.1 that matters most on a home network — a guest-network device talking to
  a trusted-network address.

### 4.6 Per-client latency — **cut**

Actively probing each client from the router means N pings per interval from a
CPU-bound MIPS box, and half the clients (phones, anything with a sleep state,
anything running a host firewall) will not answer, producing metrics that
measure the client's power management rather than the network.

The passive proxies already exist and are better: `wifi_station_signal_dbm`,
`wifi_station_expected_throughput_kilobits_per_second`, and
`hostapd_station_inactive_seconds` **[verified as existing]**. Chart those.
**Cut, with a note** so it does not get re-proposed.

### 4.7 Retention and downsampling

`grafana/otel-lgtm` does not expose Prometheus retention as an environment
variable **[verified — there is an open request for exactly this]**. Changing it
means bind-mounting a replacement `run-prometheus.sh` over the image's, which is
the same bind-mount pattern the repo already uses for provisioning.

- **Recommendation:** add `PROM_RETENTION` (default `30d`) and
  `PROM_RETENTION_SIZE` (default `8GB`) to `.env.example`, applied through a
  small `otel-lgtm/run-prometheus.sh` override. Bound size, not just time — an
  unbounded `lgtm-data` volume on a home server is how this stack eventually
  fills a disk.
- **Downsampling:** Prometheus has none. If long-horizon per-client history is
  wanted, the answer is recording rules (`openwrt:client_bytes:rate5m`) plus a
  longer retention on a smaller rule-derived series set — not a new TSDB.
  **[uncertain]** whether otel-lgtm's bundled Prometheus reads a rule file from
  a predictable path; check before promising it.

---

## 5. Alerting

The repo ships no alert rules today **[verified — there is no
`grafana/provisioning/alerting/`]**. Grafana provisions unified alerting from
that directory, so this is additive and needs no new container.

### 5.1 Rules worth having

| Alert | Expression sketch | Why |
|---|---|---|
| Unknown device joined | `openwrt_client_first_seen_seconds > time() - 300` | The one alert a home network actually wants. |
| Guest→trusted traffic | flow metric with `peer_kind="trusted"` and client `network="guest"` | Detects a misconfigured or hostile guest device. |
| Client conntrack blowup | `openwrt_client_conntrack_entries > 400` | Torrent, malware, or a broken IoT device. |
| Flow offload masking accounting | `openwrt_flow_offload_enabled{mode="hw"} == 1` | Prevents silently trusting zeros (§3.4). |
| Collector unavailable | `openwrt_client_inventory_collector_available == 0` | Matches the existing availability convention. |
| Inventory truncated | `openwrt_client_inventory_truncated == 1` | The cap in §1.1 was hit; numbers are now wrong. |

Deliberately **not** alerting on per-client bandwidth: on a home network the
threshold that is not either always-firing or never-firing does not exist.

**Cardinality cost:** zero. Alerts evaluate existing series.

---

## 6. What this draft cuts

| Cut | Reason |
|---|---|
| **Infinity datasource + `GF_INSTALL_PLUGINS`** | Unnecessary — plain Prometheus produces valid node graph frames (§2.2). Also removes the offline-install regression the first draft accepted. |
| **UQL / JSONata reshaping** | Undocumented for node graph formats, no working community example found. Building on it was the first draft's weakest assumption. |
| **OUI vendor table on the router** | Two independent reasons. (i) Flash cost for a table that goes stale. (ii) **It mostly does not work any more** — modern phones and laptops present per-SSID randomised MACs with the locally-administered bit set, which have no OUI. Replaced by `mac_type="global\|local"`, one bit-test, which is *more* informative. |
| **`secondarystat` in the node graph v1** | Needs a second query and a join transformation per frame for marginal value. |
| **Per-client active latency probing** | §4.6. |
| **pmacct** | Not in the OpenWrt feed for `mipsel_24kc`; adopting it means maintaining a package (§3.2). Evaluated properly and rejected on availability, not merit. |
| **ipt-netflow, ntopng, hsflowd** | Not packaged / far too large for 128 MB flash. |
| **nfCollector / goNfCollector** | Requires InfluxDB in a Prometheus+Loki stack. |
| **`openwrt_flow_peer_bytes_total{mac, peer}` in Prometheus** | Remote peers belong in Loki. Keeping a topk-bounded version in Prometheus is possible but off by default. |
| **NetFlow as the phase-3 default** | Demoted behind nlbwmon. Still documented, still buildable, but not the recommended first step (§3.2). |

---

## 7. Sequencing

Contents and dependencies below; **file manifests and acceptance criteria are
in §11**, and the two gates have full protocols in §12. Work one milestone at a
time.

| Milestone | Contents | Depends on |
|---|---|---|
| **M0** | Bound `node_nat_traffic` cardinality in Alloy; comma-separated `OPENWRT_MONITOR_PROFILE`; syslog `router` label from hostname | — |
| **M1** | `client_inventory.lua`, `clients` profile, `getHostHints` + assoclist join, `rpcd-mod-luci` dependency, offload detection metric | M0 |
| **M2** | Multi-target Alloy (`ROUTER_TARGETS`) | M0 |
| **M3** | `build_openwrt_clients_dashboard.py` + clients dashboard + docs | M1 |
| **M4** | Node graph feasibility spike: hardcoded 3-edge panel against the bundled Grafana, confirming §2.2 | — |
| **M5** | `topology.lua` emitting `openwrt_topology_node` / `_edge`; `build_openwrt_topology_dashboard.py` | M1, M2, M4 |
| **M6** | nlbwmon in the `clients` profile, trimmed protocol file, `openwrt_client_bytes_total` collector, offload-unreliable dashboard state | M1 |
| **M7** | Per-client conntrack, IPv6 count, roaming Loki panels, guest/trusted `network` label | M1, M3 |
| **M8** | Alert rule provisioning; retention/size env vars | M3 |
| **M9** | Opt-in DNS query attribution (dnsmasq `logqueries` + Loki panels + privacy documentation) | M3 |
| **M10** | *Conditional.* softflowd measurement spike: load and `softnet` before/after on the real router, hardware offload off | M6 |
| **M11** | *Conditional on M10.* `netflow` profile, softflowd UCI template, exporter health script, FLP compose profile, pipeline config, flow panels | M10 |

M4 is deliberately first-in-parallel and cheap: it is the one assumption in this
plan whose failure would change the design rather than the schedule.

M10 is a gate, not a task. If softflowd costs more than a few percent of a core
at typical household load, M11 does not happen and phase 3 ends at nlbwmon —
which still answers (a) and (b) and is 80 % of the value.

M0–M3 and M4–M5 deliver standalone value and do not depend on M6+ at all.

If the router is not reachable from the implementation environment, M0, M1, M6,
M7, M9, M10, and M11 cannot be honestly completed — their acceptance criteria
all require a live exposition check or a measurement. Report them blocked
rather than writing collectors that have never been run.

---

## 8. Risks and open questions

### Risks

- **Flow offload silently zeroes all traffic accounting.** §3.4. Highest
  severity, affects code already shipped, mitigated by detection + explicit
  dashboard state, not by choosing a different tool.
- **softflowd CPU on MT7621 is unmeasured.** §3.5. The first draft asserted it
  was fine; there is no evidence either way. Gated behind M10.
- **Node graph frame detection passed against the bundled Grafana.** §2.2,
  M4. The Prometheus table/refId path is viable; M5 still must emit node and
  edge frames from the same snapshot so disappearing clients do not create
  dangling endpoints.
- **`getHostHints` requires `rpcd-mod-luci`.** Present on any LuCI install;
  needs a leasefile fallback for LuCI-less routers.
- **Dumb-AP deployments** need the exporter on every AP for `ap` attribution to
  mean anything. Single-router users get a degenerate but correct graph.
- **MAC randomisation fragments identity across networks.** Detail below.
- **DNS query logging is a household browsing history.** Opt-in, documented,
  never on by default.

### MAC randomisation — resolved with evidence, not deferred

**[verified]** Randomised MACs are unicast + locally-administered: bit 0 of the
first octet clear, bit 1 set, remaining 46 bits random — so the second hex
character is one of `2`, `6`, `A`, `E`.

The important part, which changes how much this matters: modern implementations
use **per-SSID persistent** randomisation, not per-association. An iPhone or
Android device presents the *same* randomised MAC to `Home-5G` every time it
connects. So:

- Client identity **survives** across reconnects and roams. `mac` remains a
  valid primary key. No stable-ID mechanism is needed.
- What breaks is: (i) OUI vendor lookup — hence the cut in §6; (ii) the same
  physical device appearing as two clients if it joins two SSIDs (guest and
  trusted); (iii) identity resetting if the user forgets and rejoins the
  network.
- (ii) is worth surfacing rather than solving. Emit `mac_type` and let the
  clients dashboard show a "randomised" badge. Attempting to re-link randomised
  identities to a physical device is fingerprinting; it is the wrong thing to
  build into a home monitoring stack, and it does not work reliably anyway.
- Practical mitigation for devices the household cares about: turn off private
  addressing for the home SSID on those devices, or add a static UCI lease so
  the randomised MAC gets a stable name. Document both.

### Open question 1: where does the clients table live? — **resolved**

**A new generated dashboard**, `build_openwrt_clients_dashboard.py` →
`openwrt-clients-v2.json`, dual-output like the other two.

Evidence: `grafana dashboards/openwrt-devices.json` is 47 KB and already
carries device status, NAT top-10, the lease table, static reservations, and the
full WiFi station panel set **[verified]**. It is also generated by
`build_dashboards.py`, the *classic* builder, whereas the v2 convention is one
builder per dashboard with dual output — a convention two dashboards deep and
consistent **[verified]**. Adding a third builder is the established path; the
clients view (identity + signal + traffic + services + roam timeline + DNS) is
more than one screen on its own.

The Devices dashboard stays as-is, with a data link from its station table to
the new clients dashboard.

### Open question 2: OUI vendor lookup on-router or in Grafana? — **resolved by dropping it**

Neither. The question presupposed the lookup is worth doing.

The full IEEE OUI registry is several megabytes; a "trimmed ~1500 prefix" table
is a hand-maintained file that goes stale and still costs flash on a device with
128 MB. Against that cost: on a 2026 home network, phones, tablets, and laptops
— the devices whose vendor a human would actually want to see — present
randomised MACs with **no OUI at all**. The lookup succeeds mainly for the
devices that already have obvious hostnames: printers, TVs, IoT plugs.

Replace it with `mac_type="global|local"` — a single bit test, zero flash, and
it answers a question the vendor string never could ("is this a real hardware
address or a privacy address?").

If someone still wants vendor names later, do it at **dashboard build time** as
a Grafana value mapping over the specific OUIs present on that network. That
keeps it off the router entirely and costs one regeneration when a device is
added.

### Still open, honestly

Each of these must be **resolved by testing** in the milestone named, and this
document updated in the same change to replace the tag with the finding. None
of them may be resolved by assertion.

| # | Open item | Raised in | Resolved by |
|---|---|---|---|
| 1 | Alloy relabel syntax for bounding `node_nat_traffic` — the snippet in §0.3 is explicitly wrong and must not be copied | §0.3 | **M0 — resolved: no relabel syntax preserves it.** A per-`src` rollup cannot be computed at the Alloy layer at all (relabel is per-sample, not an aggregation); the only correct fix was to drop the metric outright via a plain `action = "drop"` on `__name__`, tested against a live Alloy/Prometheus. See §0.3 below. |
| 2 | Actual churn rate of `node_nat_traffic` on a real home LAN | §0.3 | **M0 — measured.** 147 series across 19 distinct `src` on the reference router at time of measurement (2026-07-22); 11 of those 19 (58%) had more than one destination with a non-zero byte count, one with 8 active destinations simultaneously. See §0.3. |
| 3 | Node graph frame detection against the Grafana bundled in otel-lgtm | §2.2 | **M4 — passed.** Grafana 13.0.1 (`a100054f`) rendered Prometheus instant table frames with `refId="edges"` and `refId="nodes"`; see §2.2. |
| 4 | nlbwmon's CPU cost on MT7621 | §3.3 | **M6 — measured idle-only.** Two 60-second stop/run windows on the reference router showed 10.19% versus 10.45% aggregate CPU busy time, `node_load1` 1.32 versus 1.16, and zero softnet drops in both; see §3.3. This does not establish transfer-load cost. |
| 5 | softflowd's CPU cost on MT7621 — the first draft asserted it was fine with no evidence | §3.5 | **M10**, protocol in §12.2 |
| 6 | FLP's aggregate output key name (`recent_op_value` is provisional) | §3.6 | **M11** |
| 7 | Whether softflowd's v9 templates carry MAC fields — asserted no, from its template set | §3.6 | **M11** (only matters if M11 happens) |
| 8 | Whether otel-lgtm's Prometheus reads a rule file from a predictable path | §4.7 | **M8** — if not, drop the recording-rule idea rather than shipping a no-op |

No default may be flipped, and no `[unmeasured]` claim may be restated as fact,
before its milestone runs.

---

## 9. Implementation conventions — read before writing code

All **[verified]** by reading the files named. These are not style preferences;
each one has a failure mode behind it.

### 9.1 Router-side Lua collectors

Location `openwrt/collectors/*.lua`, installed to
`/usr/lib/lua/prometheus-collectors/` at mode `0644`. OpenWrt ships **Lua 5.1** —
no `goto`, no integer division operator, no `table.pack`.

Shape (from `device_traffic.lua`, `dpi_netifyd.lua`, `wifi_dethrash.lua`):

```lua
local ok_ubus, ubus = pcall(require, "ubus")   -- every optional dep under pcall

local function collect(...) ... return true end

local function scrape()
  local available = metric("openwrt_<name>_collector_available", "gauge")
  if not ok_ubus then available({}, 0) return end

  -- Availability is reported only AFTER collection finishes. Claiming 1 up
  -- front made a mid-collection failure look healthy on the dashboard while
  -- no series were exported at all. pcall also keeps malformed input from
  -- failing the exporter's whole scrape.
  local ok, completed = pcall(collect, ...)
  available({}, (ok and completed) and 1 or 0)
end

return {scrape = scrape}
```

- `metric(name, type)` is injected by `prometheus-node-exporter-lua`; it returns
  a function you call as `m(labels_table, value)`.
- Every label value goes through a sanitiser. `device_traffic.lua` uses
  `value:gsub("[^%w%._%-]", "_")` with a non-empty fallback **[verified]**.
  Reuse that exact character class so label values stay consistent across
  collectors. MAC addresses are the one exception — see §9.7.
- Read configuration from `/etc/openwrt-grafana-monitor.conf`, which `setup.sh`
  writes as simple `KEY="value"` lines; `device_traffic.lua` has a
  `config_value(name, fallback)` helper to copy.
- Bound every unbounded loop. Cap, then export a `_truncated` flag.

### 9.2 Router-side helper scripts (POSIX sh)

Location `openwrt/scripts/openwrt-monitor-*.sh`, installed to `/usr/bin/` at
mode `0755`, scheduled with `ensure_cron_line()`. They write text-format
metrics into the textfile collector directory.

The idiom is fixed — copy `openwrt-monitor-link-health.sh` **[verified]**:

```sh
#!/bin/sh
set -e

# Overridable so the logic can be exercised in tests.
OUTDIR="${OPENWRT_MONITOR_TEXTFILE_DIR:-/var/prometheus}"
OUTFILE="$OUTDIR/openwrt_<name>.prom"
# The textfile collector reads every file in $OUTDIR, so a temp file left there
# by a crashed run is scraped as a second copy of every metric below. Stage
# outside $OUTDIR (same filesystem on OpenWrt: /var -> /tmp) and mv atomically.
TMPFILE="/tmp/.openwrt-monitor-openwrt_<name>.$$"

mkdir -p "$OUTDIR"
rm -f "$OUTFILE".[0-9]*          # clean temps leaked by earlier versions
trap 'rm -f "$TMPFILE"' EXIT

{
  printf '# HELP ...\n'; printf '# TYPE ...\n'   # all HELP/TYPE first, once
  ...
} > "$TMPFILE"

mv "$TMPFILE" "$OUTFILE"
```

Two rules with teeth:

- **Never stage a temp file inside `$OUTDIR`.** This is the duplicate-series bug
  documented in `docs/advanced-profiles.md`. Prometheus keeps the first sample
  of a duplicated series, drops the rest, and reports no scrape error — so the
  failure is invisible from the dashboard and shows up as a plausible wrong
  number.
- **Emit each `# HELP`/`# TYPE` exactly once**, at the top, before any samples.

`OPENWRT_MONITOR_TEXTFILE_DIR` exists purely so tests can point the script at a
temp directory. Any new helper must honour it.

### 9.3 `openwrt/setup.sh` integration

- Packages: add to `OPTIONAL_PACKAGES` only if the profile is not needed to
  install them; otherwise install inside the `profile_enabled` block with
  `pkg_install_optional` and a `WARNING:` log line on failure. Never let an
  optional package failure abort setup.
- **Confirm the package exists** in the 24.10 `mipsel_24kc` index before
  depending on it:
  `curl -s https://downloads.openwrt.org/releases/24.10.0/packages/mipsel_24kc/packages/Packages | grep '^Package: <name>$'`
  and check `base/Packages` too. Both `opkg` (24.10) and `apk` (25.12) paths go
  through the same `pkg_*` wrappers, so nothing else changes.
- Files: `install_file "$COLLECTOR_SRC_DIR/x.lua" /usr/lib/lua/prometheus-collectors/x.lua 0644`
  or `install_file "$HELPER_SRC_DIR/x.sh" /usr/bin/x.sh 0755`.
- Cron: `ensure_cron_line '*/N * * * * /usr/bin/openwrt-monitor-x.sh'`, and add
  the script to the "run once so metrics appear immediately" block.
- Config: new tunables go into the `/etc/openwrt-grafana-monitor.conf` heredoc
  **and** the header comment block listing optional environment variables.
- Validate any value interpolated into a file. `TRAFFIC_LAN_INTERFACE` is
  checked with `case "$X" in *[!A-Za-z0-9_.-]*|'') die ... esac` before being
  `sed`-substituted **[verified]**. Do the same for `MONITORING_HOST` in the
  softflowd template.
- **M0 changes `profile_enabled()`** to accept a comma-separated
  `OPENWRT_MONITOR_PROFILE`. Keep single-value input working, keep `full`
  meaning all, and update the validating `case` and the header comment.

### 9.4 Dashboard builders

**The v2 dashboards are not the classic Grafana JSON model.** They are
`dashboard.grafana.app/v2beta1` **[verified]**, and `validate_dashboard()`
asserts both `dash["apiVersion"] == "dashboard.grafana.app/v2beta1"` and that
`"schemaVersion" not in text`. Do not paste classic-model panel JSON into a v2
builder.

Structure, from `build_openwrt_operations_dashboard.py`:

| Concept | v2beta1 form | Helper |
|---|---|---|
| Query | `{"kind": "PanelQuery", "spec": {"refId", "hidden", "query": {"kind": "DataQuery", "group": "prometheus"\|"loki", ...}}}` | `prom_query()`, `loki_query()` |
| Panel | `{"kind": "Panel", ...}`, keyed `panel-<pid>` | `panel()`, and `stat`/`timeseries`/`table`/`bargauge`/`text` wrappers |
| Transformation | `{"kind": <id>, "spec": {"id": <id>, "options": {...}}}` | `tf()`, `organize()`, `limit()`, `convert_time()`, `calculate()` |
| Layout | `TabsLayoutTab` → `RowsLayout` → `GridLayout` → `GridLayoutItem` | `DashboardBuilder.add(tab, item, x, y, w, h)`, `.tab(title, items)` |

Relevant to §2.2: `prom_query()` already takes everything the node graph needs
**[verified]** —

```python
prom_query(expr, ref="nodes", fmt="table", instant=True)
prom_query(expr, ref="edges", fmt="table", instant=True)
```

`ref` becomes `spec.refId`, which is the mechanism Grafana uses to classify node
graph frames. No new helper is required.

Other rules:

- Import shared helpers from `build_openwrt_operations_dashboard.py` rather than
  redefining them. `build_openwrt_advanced_dashboard.py` shows the import list.
- Every builder writes the **same bytes** to both paths in its `OUTS` list.
  `main()` builds the dashboard twice and asserts the output is identical, so
  anything non-deterministic (set iteration, `id()`, timestamps, unsorted dict
  comprehension over a set) fails immediately. Keep it that way.
- `DashboardBuilder.add()` raises on a duplicate element key, so panel ids must
  be unique within a dashboard.
- Colour and override conventions live in `COMMON_OVERRIDES`; note the comment
  explaining that override order matters and that `\bdown\b` is deliberate
  because "down" is a substring of "Download".
- Availability tiles gate on **both** the collector's own `_available` flag and
  `node_scrape_collector_success` for that collector — see the docstring on
  `availability()` in the advanced builder, which records that a collector once
  reported itself available while exporting nothing.

### 9.5 Alloy and compose

- `alloy/config.alloy` is Alloy River, configured entirely through `sys.env()`.
  Any new variable must be added to `docker-compose.yml`'s `environment:` block
  **and** to `.env.example` with a comment.
- New containers go behind a compose `profiles:` key so the default
  `docker compose up` stays a two-container, offline-capable stack.
- No `GF_INSTALL_PLUGINS`. Ever. See §6.

### 9.6 Tests

`tests/run_all.sh` is the gate. It currently **hardcodes** the three builders in
three places **[verified]**: the `py_compile` list, the regenerate list, and the
`cmp` pairs. Every new builder must be added to all three, or the determinism
check silently does not cover it.

Also required per milestone:

- A fixture-driven collector test in the style of `tests/test_device_traffic.lua`
  (Lua) or `tests/test_sqm_collector.sh` (sh, using
  `OPENWRT_MONITOR_TEXTFILE_DIR`), with fixtures under `tests/fixtures/`.
- `sh -n` and `luac5.1 -p` pass — both already run over globs, so new files are
  picked up automatically.
- Against a live router:
  `ROUTER_METRICS_URL=http://192.168.0.1:9100/metrics sh tests/run_all.sh`,
  which runs `tests/check_exposition.py` for duplicate series. **A milestone
  that adds router-side metrics is not done until this passes on real
  hardware.**

Note: `.agents/`, `.codex/`, and `skills/grafana-dashboards/` exist but are
empty **[verified]** — there is no pre-existing agent convention to conform to
beyond what is written here.

### 9.7 Naming rules

- **MAC addresses**: lowercase, colon-separated, everywhere this plan
  introduces them (`a4:83:e7:aa:bb:cc`). Note this deliberately differs from
  upstream `dhcp_lease` and `uci_dhcp_host`, which uppercase — see §0.1. Do not
  run MACs through the generic `gsub("[^%w%._%-]", "_")` sanitiser; it would
  eat the colons. Validate with a MAC-shaped pattern and reject non-matching
  input instead.
- **Metric prefix**: `openwrt_` for everything this repo produces. Flow metrics
  from FLP get `prefix: openwrt_flow_` in the pipeline config.
- **Node graph IDs**: `<type>:<key>` — `client:<mac>`, `ap:<name>`,
  `ssid:<ssid>@<band>`, `router:<name>`, `internet`. Node graph field names on
  Prometheus labels are lowercase (`title`, `subtitle`, `mainstat`, `arc__ok`,
  `detail__ip`) per §2.1.
- **Profiles**: `clients`, `netflow`. Lowercase, no hyphens.
- **Textfile output**: `/var/prometheus/openwrt_<subject>.prom`, matching the
  metric prefix of its contents.

---

## 10. Metric contract

The complete set of new series this plan introduces. Estimates assume **60
clients, 1–2 APs, 3 SSIDs**. Anything not in this table needs a decision
recorded before it ships.

### 10.1 Phase 1 — identity (M1)

| Metric | Type | Labels | Series | Source |
|---|---|---|---|---|
| `openwrt_client_info` | gauge | `mac, hostname, ip, router, ap, ssid, band, ifname, connection, network, static, mac_type, has_ipv6` | 60 | `getHostHints` + assoclist |
| `openwrt_client_up` | gauge | `mac` | 60 | assoclist / neighbour table |
| `openwrt_client_lease_expiry_seconds` | gauge | `mac` | 60 | leasefile |
| `openwrt_client_ipv6_addresses` | gauge | `mac` | 60 | `getHostHints.ip6addrs` count |
| `openwrt_client_first_seen_seconds` | gauge | `mac` | 60 | `/etc/openwrt-client-seen` |
| `openwrt_client_inventory_collector_available` | gauge | — | 1 | self |
| `openwrt_client_inventory_truncated` | gauge | — | 1 | self |
| `openwrt_flow_offload_enabled` | gauge | `mode="sw"\|"hw"` | 2 | UCI firewall |

**Subtotal ≈ 304 series.**

### 10.2 Phase 2 — topology (M5)

| Metric | Type | Labels | Series |
|---|---|---|---|
| `openwrt_topology_node` | gauge | `id, title, subtitle, icon, arc__*, detail__*` | ~70 (clients + APs + SSIDs + router + internet) |
| `openwrt_topology_edge` | gauge | `id, source, target, color, thickness, strokedasharray` | ~70 |

**Subtotal ≈ 140 series.** Churns when a hostname or association changes; that
is inherent to putting display strings on labels and is the accepted trade-off
in §2.2.

### 10.3 Phase 3 — traffic (M6, M7)

| Metric | Type | Labels | Series | Notes |
|---|---|---|---|---|
| `openwrt_client_bytes_total` | counter | `mac, direction, service` | 60 × 2 × ~10 = **1200** | service set trimmed at install |
| `openwrt_client_packets_total` | counter | `mac, direction, service` | **1200** | drop if 10.3 total is too large |
| `openwrt_client_connections_total` | counter | `mac, service` | **600** | |
| `openwrt_client_conntrack_entries` | gauge | `mac` | 60 | |
| `openwrt_wifi_assoc_events_total` | counter | `ap, ssid, event` | ~12 | |

**Subtotal ≈ 3072 series** — an order of magnitude above phase 1, and the
reason the nlbwmon protocol file must be trimmed (§3.3). If it needs cutting
further, drop `openwrt_client_packets_total` first; bytes and connections carry
the useful signal.

### 10.4 Phase 3, conditional — NetFlow (M11, gated on M10)

| Metric | Type | Labels | Series |
|---|---|---|---|
| `openwrt_flow_bytes_total` | counter | `ip, service, peer_kind` | 60 × 10 × 3 = 1800, hard-capped by FLP `maxMetrics: 5000` |

Full-fidelity flow records go to **Loki**, stream-labelled `srcKind` only.

### 10.5 Labels that must never exist

Recorded so nobody re-derives them:

| Label | On | Why not |
|---|---|---|
| `dest` / remote IP | any Prometheus metric | Unbounded. This is the §0.3 bug. |
| domain / QNAME | any Prometheus metric | Unbounded. DNS detail belongs in Loki (§4.1). |
| IPv6 address | `openwrt_client_info` | Privacy addresses rotate; series churn (§4.4). |
| `mac` | `openwrt_wifi_assoc_events_total` | 120 churning series to replace a better log timeline (§4.3). |
| port number | any flow metric | Bucket into `service` first (§3.7). |
| `vendor` | `openwrt_client_info` | Cut; replaced by `mac_type` (§6). |

**Grand total if everything ships: ~3600 series** (M11 adds ~1800 more). For
comparison, `node_nat_traffic` alone plausibly churns through more than that in
a day today (§0.3, `[unmeasured]`).

---

## 11. Milestone specifications

Format per milestone: goal, files, acceptance criteria, verification. "Blocked
if" lists conditions under which you must stop and report rather than improvise.

### M0 — Bound existing cardinality; make profiles composable — done (2026-07-22)

**Files:** `alloy/config.alloy`, `openwrt/setup.sh`, `build_dashboards.py`,
`build_openwrt_operations_dashboard.py`, `docs/advanced-profiles.md`,
`README.md`.

**Done, with one deviation from the original "Do" list** — see §0.3 for the
full account. Summary:
1. Tested a real Alloy relabel rule against a live Alloy/Prometheus and found
   **no relabel rule can bound `node_nat_traffic` to a correct per-`src`
   rollup** — relabeling is per-sample, not an aggregation, and collapsing
   `dest` would have silently under-reported 11 of 19 (58%) active clients
   measured on the reference router. This hit M0's own "Blocked if" clause.
   Reported to a human with the two §0.3 alternatives; the metric was dropped
   entirely (§0.3 option 2), not collapsed. `openwrt_device_traffic_bytes_total`
   (already bounded, already per-device) replaces it on both dashboards that
   used it — the Devices dashboard's classic panel and, undocumented by the
   first draft, a second Operations-dashboard panel keyed on raw remote IP,
   which was removed with no substitute rather than shipped with the same
   unbounded-label problem.
2. Series count measured before/after — see §0.3 and open item #2, resolved.
3. `OPENWRT_MONITOR_PROFILE` accepts a comma-separated list in
   `profile_enabled()` and the validating `case`; `full` remains all-profiles
   and is rejected if combined with other names, to keep the input one
   unambiguous shape.
4. `__syslog_message_hostname` maps to the `router` label in
   `loki.relabel.openwrt_syslog`, falling back to the static `router` label
   (still `ROUTER_NAME`) when a syslog line carries no hostname field.

**Acceptance, as actually met:** `count(node_nat_traffic)` is bounded to `0`
(confirmed empty after the metric aged out of Prometheus's staleness window,
not merely "bounded by active local IP count" as originally specified — a
stronger result than the plan asked for, reached because no weaker bound was
achievable without incorrect values); the Devices and Client-Insights "top
traffic" panels render with equivalent intent (device ranking) but a different
query, byte-total ranking replaced by a Bps-rate ranking, since
`openwrt_device_traffic_bytes_total` is a true monotonic counter rather than
`node_nat_traffic`'s conntrack-table snapshot; `OPENWRT_MONITOR_PROFILE=
traffic,wifi_mesh` enables both (`clients` does not exist yet — that is M1);
existing syslog panels still resolve `router`. `sh tests/run_all.sh` passes.

**Blocked if:** the relabel cannot preserve the Devices panel. Report with the
two options from §0.3 (drop the metric, or replace with §4.2) and let a human
choose.

### M1 — Client inventory collector — done, live-verified (2026-07-22)

**Files:** `openwrt/collectors/client_inventory.lua` (new), `openwrt/setup.sh`
(`clients` profile: package deps, `CLIENT_INVENTORY_MAX` config, collector
install), `tests/test_client_inventory.lua` (new),
`tests/fixtures/{gethosthints,wireless_status,assoclist}.json`,
`tests/fixtures/{proc-net-arp.txt,hostname.txt}` (new),
`tests/run_all.sh`, `docs/advanced-profiles.md`.

**Access note:** this implementation environment has HTTP access to the
router's `:9100/metrics` but no SSH/shell access to deploy code on it. The
operator (not the implementer) ran `scp`/`ssh` deployment across three
iterations; the implementer verified each iteration's result directly over
HTTP. That division of labor is why this took three rounds instead of one —
recorded honestly below because it is the point of the exercise: the plan's
own rule is "resolve `[reasoned]` by testing before writing dependent code,"
and two of the three rounds exist because reasoning from upstream source
without ever seeing this router's actual ubus output was not good enough.

**Done, per §1.1, as built (differs from the original design in two
ways found only by live testing — see below):**
- Identity spine from `ubus call luci-rpc getHostHints`, normalising MAC case
  unconditionally.
- Association from `network.wireless status` + `iwinfo.assoclist`. **Not**
  joined with `wireless.wifi-iface` UCI sections as §1.1 originally specified
  — see "Correction" below.
- `mac_type` from bit 1 of octet 0 (locally-administered bit).
- `first_seen` persisted to `/etc/openwrt-client-seen`, LRU-evicted at
  `CLIENT_INVENTORY_MAX` (default 256, configurable). Staged and renamed
  within `/etc` rather than via `/tmp`, since `/etc` and `/tmp` are not
  guaranteed to be the same filesystem on OpenWrt (unlike `/var`, which the
  existing helper-script convention in §9.2 relies on).
- `rpcd-mod-luci`, `libubus-lua`, `libiwinfo-lua`, `libuci-lua` added as
  optional packages for the new `clients` profile, each confirmed present in
  the 24.10 `mipsel_24kc` feed, with a `/tmp/dhcp.leases` fallback.
- `openwrt_flow_offload_enabled{mode="sw"|"hw"}` from UCI firewall defaults —
  live-confirmed both software and hardware flow offload are **enabled** on
  the reference router (`sw=1, hw=1`). Per §3.4 this means every existing
  conntrack/nftables-based byte counter on this specific router is already
  unreliable; worth flagging loudly before M6 (nlbwmon) or the `traffic`
  profile's numbers are trusted.
- Availability reported only after collection completes; every external read
  happens before any `metric()` call, so a failed read leaves zero partial
  series, not a half-populated scrape.

**Correction to §1.1, found by live testing, not by re-reading source
harder:** the plan specified resolving SSID/network-zone via
`network.wireless status` interface names joined against
`wireless.wifi-iface` UCI sections (mirroring `wifi_dethrash.lua`'s pattern
for radio-level facts). Live ubus output showed this join is unnecessary and
was itself a source of risk: `network.wireless status`'s
`interfaces[].config.ssid` and `interfaces[].config.network` already carry
the SSID and logical network name directly, and `radio.config.band` carries
`"2g"`/`"5g"` directly — no UCI cross-reference and no `iwinfo.ssid()`/
`iwinfo.frequency()` calls needed. The collector was simplified accordingly
after this was confirmed, which is strictly less code and one fewer external
dependency (`iwinfo` is now used only for `assoclist()`, not for per-radio
facts) than §1.1 specified.

**Two real bugs found only by live deployment, both fixed and re-verified:**
1. **The router listed itself as a client.** `getHostHints` returned an entry
   for the router's own `br-lan` MAC/IP (`hostname="OpenWrt.lan"`,
   `ip="192.168.0.1"`), which is not documented behavior in the upstream
   source citations §1.1 relied on. Fixed by reading
   `/sys/class/net/*/address` and excluding any `getHostHints` entry matching
   a local interface's own MAC. Confirmed live: the router's own MAC no
   longer appears, and the total series count dropped from 22 to 21 —
   exactly "minus one."
2. **Every client showed `connection="wired"`, including confirmed-associated
   WiFi stations.** The first deployed version's `wifi_ifaces()` depended on
   `iwinfo` succeeding just to query `network.wireless status` at all
   (`if not ok_iwinfo then return {} end`, before even attempting the ubus
   call). A standalone diagnostic script proved `iwinfo.assoclist()` itself
   works correctly and returns the exact shape the code expects
   (uppercase-MAC-string keys); the actual fault was upstream of that, in
   `wifi_ifaces()`'s now-removed `iwinfo` dependency, which most plausibly
   behaved differently inside the long-running exporter process (loading many
   collectors' native modules together) than in a short-lived standalone
   script — not conclusively isolated further since a standalone repro inside
   the exporter process itself wasn't practical to arrange. The fix (§1.1
   correction above) removed the dependency rather than chasing the exact
   failure mode. Confirmed live: all 6 currently-associated WiFi stations
   (cross-referenced against the working `wifi_station_signal_dbm` collector)
   now show `connection="wifi"` with correct `ssid`, `band`, and `ifname`.

**Acceptance — all verified live, not just against fixtures:**
`openwrt_client_inventory_collector_available` and
`node_scrape_collector_success{collector="client_inventory"}` are both `1`;
21 `openwrt_client_info` series, no duplicates within the collector or across
the full exposition (`check_exposition.py`: 2353 samples, 0 duplicates);
`sh tests/run_all.sh` including the live-router pass; every MAC lowercase
colon-separated; every client has a non-empty hostname (real name or an
explicit `unknown_<mac>` fallback — confirmed common on this network, since
many devices have no reverse-DNS/DHCP name); `openwrt_client_inventory_truncated`
is `0` (21 clients, well under the 256 default cap).

**Not fully explained, flagged rather than silently accepted:** three
`getHostHints` entries (`02:00:00:00:50:01`, `...51:01`, `...52:01`, at
`192.168.0.50`/`.51`/`.52`) have synthetic-looking sequential MACs with the
locally-administered bit set, and do not appear in `dhcp_lease`/`uci dhcp`
static reservations. They came through `cat /etc/config/dhcp` as absent —
they are not UCI static leases. Most likely a `getifaddrs()`-sourced
placeholder for point-to-point/tunnel-style interfaces without a real L2
address (the operator's network includes several homelab VMs/containers per
other entries in this same dump — `aarhus-pi`, `adguard-lxc`, etc. — so a
tunnel/bridge interface without a real MAC is plausible). Not filtered out,
because filtering on an unconfirmed guess about their origin risks hiding
real network entities, which is worse than an unexplained row in a table.
Revisit if the operator can positively identify what these are.

**Device-count semantics, worth recording so nobody "fixes" it:**
`openwrt_client_info` (21) and `router_device_up` (10) count different
things and are not expected to match — `getHostHints` accumulates every host
address resolution has ever seen (neighbour table, leasefile, static
reservations, reverse DNS), while `router_device_up` reflects the existing
ping-based current-reachability check. Both are correct; they answer
different questions. The plan's original M1 acceptance line ("series count
equals the device count") assumed a shared meaning of "device count" that
these two collectors don't actually share.

### M2 — Multi-target Alloy

**Files:** `alloy/config.alloy`, `docker-compose.yml`, `.env.example`,
`README.md`.

**Do:** §1.2. `ROUTER_TARGETS` as `name=ip:port,name=ip:port`;
`ROUTER_IP`/`ROUTER_NAME` remain the single-target default. Prefer
`discovery.file` with a generated targets file over string-splitting in River
if the latter is unreadable.

**Acceptance:** unset `ROUTER_TARGETS` behaves exactly as today (diff the
scraped series set before and after); with two targets, both appear with
distinct `router` labels.

### M3 — Clients dashboard

**Files:** `build_openwrt_clients_dashboard.py` (new),
`grafana/provisioning/dashboards/openwrt-clients-v2.json` (generated),
`grafana-dashboard-exports/openwrt-clients-v2.json` (generated),
`tests/run_all.sh`, `README.md`.

**Do:** §1.3. Note query D's fallback: until M6, per-client traffic joins on
`device` (hostname), not `mac`, and breaks for clients without a DHCP hostname —
render that as an explicit state, not a blank cell.

**Done (2026-07-22):** `build_openwrt_clients_dashboard.py` now generates the
dedicated v2beta1 Clients dashboard to both provisioning and manual-export
paths, with four tabs: Overview, Clients, Traffic, and Data Quality. The main
table is driven by `openwrt_client_info` and includes hostname, MAC, IP,
router, AP, SSID, band, connection, network, `mac_type`, IPv6 presence/count,
status, lease remaining, WiFi signal, pre-M6 download/upload fallback rates,
and an explicit Traffic Join state. Traffic panels still use
`openwrt_device_traffic_bytes_total` until M6; rows without a usable DHCP-style
hostname render `traffic unavailable: hostname join required`, and named
clients without a matching device-counter series render
`traffic unavailable: no hostname match`.

**Live-label correction found during M3:** the reference router's live
`wifi_station_signal_dbm` samples use uppercase MAC label values
(`CC:8C:...`), while `openwrt_client_info` correctly uses lowercase
colon-separated `mac`. No live `hostapd_station_signal_dbm` samples were
present in the checked scrape, though its `# TYPE` line exists. The dashboard
therefore normalizes uppercase A-F in the WiFi signal `mac` label in generated
PromQL before joining to client inventory. This keeps WiFi signal available
without changing router metrics or adding a recording rule. The same live
check confirmed `openwrt_flow_offload_enabled{mode="sw"} = 1` and `{mode="hw"}
= 1`, so every traffic fallback panel visibly warns that nftables accounting
may be unreliable on the reference router.

**Validation evidence (2026-07-22):** `python3 -m py_compile
build_openwrt_clients_dashboard.py`, `python3 build_openwrt_clients_dashboard.py`,
`sh tests/run_all.sh`, and
`ROUTER_METRICS_URL=http://192.168.0.1:9100/metrics sh tests/run_all.sh` all
passed. The live exposition check reported 2320 samples and 0 duplicate
series. The running Grafana 13.0.1 instance provisioned `openwrt-clients`
from `openwrt-clients-v2.json`; representative generated PromQL for client
count, WiFi signal normalization, traffic download fallback, traffic join
state, and flow-offload state parsed and evaluated successfully against the
local Prometheus.

**Acceptance:** `tests/run_all.sh` passes including the new `cmp` pair (all
**three** hardcoded lists in `run_all.sh` updated); the dashboard provisions
into Grafana without errors; a client with no DHCP hostname still produces a
row.

### M4 — Node graph feasibility spike — done (2026-07-22)

**Files:** `docs/client-topology-and-netflow-plan.md` only. Temporary spike
artifacts were left under `/tmp/openwrt-m4-spike/` and were not committed.

**Result:** pass. See §2.2 for the exact Grafana version, data injection
method, screenshots/browser method, vanishing-series check, and transform
nuance.

### M5 — Topology collector and dashboard

**Files:** `openwrt/collectors/topology.lua` (new),
`build_openwrt_topology_dashboard.py` (new), generated JSON ×2,
`openwrt/setup.sh`, `tests/test_topology.lua` (new), `tests/run_all.sh`.

**Do:** §2.2–§2.4, using whatever M4 proved. Node and edge frames must be
emitted from the **same snapshot in the same scrape** — a dangling edge endpoint
crashes the panel (§2.1).

**Acceptance:** every `source`/`target` in `openwrt_topology_edge` resolves to
an `id` in `openwrt_topology_node` (assert this in the collector test); all
`arc__*` groups sum to 1; the panel renders with a client disconnecting and
reconnecting without throwing.

**Blocked if:** M4 failed. Do not start.

### M6 — nlbwmon per-client traffic

**Files:** `openwrt/scripts/openwrt-monitor-client-traffic.sh` (new),
`openwrt/nlbwmon/protocols` (new, trimmed), `openwrt/setup.sh`,
`tests/test_client_traffic.sh` (new), `tests/fixtures/nlbw.json` (new),
`docs/advanced-profiles.md`.

**Do:** §3.3. Install `nlbwmon`, trim the protocol file to the ~10 buckets in
§3.3, wrap `nlbw -c json`. Add the offload-unreliable dashboard state from
§3.4. Document the monthly accounting-period counter reset so nobody "fixes" it.

**Acceptance:** series count matches §10.3 within ~10 %; a deliberate transfer
from a known client moves the expected `mac`/`service` counter; with
`flow_offloading` enabled the dashboard shows "accounting unreliable", not
zeros. Record the before/after `openwrt_softnet_*` and load delta — this closes
the `[unmeasured]` cost question in §3.3.

**M6 result (2026-07-22):** installed and live-verified on the reference
router. `nlbw -c json`'s actual schema was checked before the helper shipped;
the helper validates that schema, normalizes MACs, aggregates IPv4/IPv6 records
by `(mac, service)`, and writes only availability `0` on any parse failure.
The trimmed protocol database was reloaded and live output confirmed `https`,
`http`, and `dns` buckets. Prometheus stored 108 byte, 108 packet, and 54
connection series for 21 inventory clients (2.57 active service buckets per
client, 270 traffic series plus availability); extrapolated at the observed
active-bucket density to 60 clients this is about 309 byte, 309 packet, and
154 connection series, below the §10.3 worst-case budget of 1200/1200/600.
The reference router has software and hardware flow offload enabled, and the
generated dashboard's exact accounting-state query evaluated to `2` while its
traffic query returned an empty vector, proving it renders the explicit
unreliable state instead of counters. No deliberate transfer was generated:
with offload enabled it would not be a valid accounting test. Idle-only CPU
measurement is recorded in §3.3.

### M7 — Conntrack, IPv6, roaming, guest/trusted

**Files:** `openwrt/collectors/client_inventory.lua` (extend),
`openwrt/scripts/openwrt-monitor-client-conntrack.sh` (new),
`build_openwrt_clients_dashboard.py`, `tests/`.

**Do:** §4.2, §4.4, §4.5, §4.3. Roaming stays a **Loki panel** plus the
per-AP/SSID counter; no per-MAC roam series (§10.5).

**Acceptance:** conntrack count for a known busy client tracks
`conntrack -L | grep <ip> | wc -l`; the roam timeline shows a real handoff when
a phone is walked between APs (single-AP installs: verify the panel degrades
cleanly instead of erroring).

### M8 — Alerting and retention

**Files:** `grafana/provisioning/alerting/openwrt-alerts.yaml` (new),
`otel-lgtm/run-prometheus.sh` (new override), `docker-compose.yml`,
`.env.example`, `README.md`.

**Do:** §5.1 and §4.7. `PROM_RETENTION` (30d) and `PROM_RETENTION_SIZE` (8GB).
Resolve the `[uncertain]` in §4.7 about whether the bundled Prometheus reads a
rule file from a predictable path — test it; if it does not, say so in the doc
and drop the recording-rule idea rather than shipping something that silently
does nothing.

**Acceptance:** each rule fires against a synthetic condition; retention flags
visible in the running Prometheus's `/api/v1/status/flags`; `docker compose up`
still works offline.

### M9 — Opt-in DNS attribution

**Files:** `openwrt/setup.sh` (new `DNS_QUERY_LOGGING` env var, default off),
`build_openwrt_clients_dashboard.py`, `docs/`, `README.md`.

**Do:** §4.1. Default **off**. The documentation must state plainly that this
records every DNS lookup by every person in the household and that Loki
retention governs how long. Add a panel showing clients using DoH/DoT, which
appear as absent from the logs.

**Acceptance:** disabled by default in a fresh install; when enabled, per-client
query rate and top domains resolve from Loki; zero new Prometheus series.

### M10 — softflowd measurement spike → see §12.2

### M11 — NetFlow (conditional on M10)

**Files:** `openwrt/netflow/softflowd.config` (new),
`openwrt/scripts/openwrt-monitor-netflow-health.sh` (new),
`openwrt/setup.sh`, `netflow/flowlogs-pipeline.yaml` (new),
`docker-compose.yml`, `alloy/config.alloy`, dashboards, `tests/`.

**Do:** §3.5–§3.7. Resolve the two `[uncertain]` items first: FLP's aggregate
output key name (`recent_op_value`), and whether softflowd's v9 templates carry
MAC fields. Validate the MONITORING_HOST interpolation as in §9.3. The pcap
filter excluding the exporter's own traffic is **mandatory**.

**Acceptance:** FLP's `/metrics` shows bounded series under `maxMetrics`; a
known transfer appears attributed to the right `ip` and `service`; the PromQL
join in §3.6 resolves `mac` correctly; full flows land in Loki with only
`srcKind` as a stream label; `docker compose up` without `--profile netflow`
still starts two containers.

**Blocked if:** M10 failed its threshold. Phase 3 ends at M6.

---

## 12. Spike protocols

These are gates. Their outcome changes the design, not the schedule.

### 12.1 M4 — node graph frame detection

**Question:** does a Prometheus instant query in Table format, with `refId`
`nodes`/`edges`, drive the node graph panel in the Grafana bundled in
`grafana/otel-lgtm`? §2.2 verifies this from Grafana source on `main`; the
bundled version may differ.

**Method:**

1. Record the bundled version: `curl -s http://localhost:3000/api/health`.
2. Push three fixed series to Prometheus by hand (Alloy static targets, or the
   textfile collector on the router, whichever is faster):

   ```
   openwrt_topology_edge{id="e1", source="internet", target="router:test"} 100
   openwrt_topology_edge{id="e2", source="router:test", target="client:aa"} 42
   openwrt_topology_edge{id="e3", source="router:test", target="client:bb"} 17
   ```

3. Build one node graph panel, edges only (the nodes frame is optional and
   Grafana derives nodes from edges — §2.1), with
   `prom_query(expr, ref="edges", fmt="table", instant=True)` plus an
   `organize()` renaming `Value` → `mainstat` and excluding `Time`, `job`,
   `instance`, `__name__`.
4. Then add a nodes frame with `refId="nodes"` carrying `id`, `title`,
   `subtitle`, `arc__ok`, `arc__warn` and confirm both frames coexist.
5. Delete one client series and confirm the panel does not throw (this exercises
   the dangling-endpoint crash path from §2.1).

**Pass:** graph renders with three edges, node stats populated from edge values,
titles and arcs applied, no console errors, and no crash on a vanishing series.

**Partial pass:** edges-only works but the nodes frame does not. Ship edges-only
for v1 — it still produces a correct topology, just with less metadata. Record
the limitation in §2.

**Fail:** neither works. **Stop.** The fallback is a topology sidecar container
serving `/nodes` and `/edges` JSON with Infinity in front of it — a different
architecture. Report and let a human decide; do not start building it.

**Output:** a note in this document under §2.2 recording the Grafana version
tested and the result, replacing the current `[unmeasured]` tag.

### 12.2 M10 — softflowd cost on MT7621

**Question:** what does libpcap capture on `br-lan` cost on this hardware, and
is it acceptable?

**Preconditions:** hardware **and** software flow offload **off**
(`uci get firewall.@defaults[0].flow_offloading` and `flow_offloading_hw` both
`0`) — with offload on, softflowd sees nothing and the measurement is
meaningless (§3.4). Record the offload state in the report.

**Method:** for each of the loads below, measure with softflowd stopped and
again with it running the §3.5 config (`sampling_rate 1`, i.e. unsampled), five
minutes per run, at least two runs per condition:

| Load | How |
|---|---|
| Idle | normal household background traffic |
| Single stream | `iperf3` LAN client → WAN-side host, capped near line rate |
| Many small flows | a few hundred concurrent short connections |

Metrics to capture, all of which this repo already exports **[verified]**:

- `node_load1`
- `openwrt_softnet_*` (backlog / squeeze / dropped)
- `node_cpu_seconds_total` by mode, especially `softirq` and `system`
- softflowd's own `softflowctl statistics`: flows tracked, expired, **dropped**
- achieved iperf3 throughput, with and without

**Pass:** < 5 % additional CPU at typical household load, no increase in
`softnet` drops, no softflowd flow drops, iperf3 throughput within noise.
→ proceed to M11.

**Marginal:** 5–15 % additional CPU, or measurable throughput loss.
→ re-run with `sampling_rate 10` and re-evaluate. If sampled operation passes,
M11 proceeds **with sampling on by default** and byte counts documented as
estimates scaled by the sampling denominator.

**Fail:** > 15 % additional CPU, `softnet` drops appear, softflowd drops flows,
or routing throughput measurably degrades. → **M11 does not happen.** Phase 3
ends at M6 (nlbwmon), which still answers "how much" and "what service".

**Output:** a table of numbers in this document replacing §3.5's `[unmeasured]`
tag, and an explicit go/no-go on M11. Report the numbers; do not decide
unilaterally to proceed past a marginal result.

---

## 13. References

Grafana:

- [Node graph visualization](https://grafana.com/docs/grafana/latest/panels-visualizations/visualizations/node-graph/) — data frame field reference; `highlighted` deprecated in 10.5
- `grafana/grafana`: `packages/grafana-data/src/utils/nodeGraph.ts` (field name enum), `public/app/plugins/panel/nodeGraph/utils.ts` (`getNodeGraphDataFrames`, `getGraphFrame`, `processNodes`)
- [Infinity node graph](https://grafana.com/docs/plugins/yesoreyeram-infinity-datasource/latest/references/display-options/node-graph/) and [discussion #186](https://github.com/grafana/grafana-infinity-datasource/discussions/186) — evaluated, not adopted
- [otel-lgtm retention discussion](https://github.com/grafana/docker-otel-lgtm/discussions/151)

OpenWrt:

- [`luci-rpc getHostHints` address priority](https://github.com/openwrt/luci/issues/4838), [reverse-DNS caveat](https://github.com/openwrt/luci/issues/4089)
- [`wifi_stations.lua`](https://github.com/openwrt/packages/blob/master/utils/prometheus-node-exporter-lua/files/usr/lib/lua/prometheus-collectors/wifi_stations.lua), [`hostapd_stations.lua`](https://github.com/openwrt/packages/blob/master/utils/prometheus-node-exporter-lua/files/usr/lib/lua/prometheus-collectors/hostapd_stations.lua), `nat_traffic.lua`, `uci_dhcp_host.lua`
- [`softflowd` UCI config](https://github.com/openwrt/packages/blob/master/net/softflowd/files/softflowd.config) and `softflowd.init`
- [`jow-/nlbwmon`](https://github.com/jow-/nlbwmon) — `database.h` record layout
- [tcpdump sees nothing on DSA ports with MT7621 offload](https://github.com/openwrt/openwrt/issues/10947)
- [firewall4 counts bytes incorrectly with offloading](https://github.com/openwrt/openwrt/issues/10399)
- [hardware offload / conntrack desync](https://github.com/openwrt/openwrt/issues/17915)

Flow collection:

- [netobserv/flowlogs-pipeline](https://github.com/netobserv/flowlogs-pipeline) — [`docs/api.md`](https://github.com/netobserv/flowlogs-pipeline/blob/main/docs/api.md), `pkg/api/api.go`, `pkg/pipeline/ingest/ingest_ipfix.go`
- [netsampler/goflow2](https://github.com/netsampler/goflow2)
- [kentik/ktranslate](https://github.com/kentik/ktranslate) and the [Grafana Cloud NetFlow integration](https://grafana.com/docs/grafana-cloud/monitor-infrastructure/integrations/integration-reference/integration-ktranslate-netflow/)
- [pmacct](http://www.pmacct.net/) — evaluated, not packaged for OpenWrt

MAC randomisation:

- [Android MAC randomization behavior](https://source.android.com/docs/core/connect/wifi-mac-randomization-behavior)
- [IETF draft-ietf-madinas-mac-address-randomization](https://www.ietf.org/archive/id/draft-ietf-madinas-mac-address-randomization-00.html)
