# NetFlow Dashboard v3 — Redesign Plan

Handoff document for the agent that builds the next iteration of **OpenWrt -
NetFlow** (`build_openwrt_netflow_dashboard.py` →
`grafana-dashboard-exports/openwrt-netflow-v2.json` +
`grafana/provisioning/dashboards/openwrt-netflow-v2.json`). Goal: make this the
most impressive dashboard in the repo — every metric Akvorado/ClickHouse
actually gives us, shown with real visual craft — while staying operationally
honest per `docs/netflow-akvorado.md`'s "known limits."

This document is analysis + a proposal, not a build. It was produced by:
reviewing the current dashboard JSON and screenshots, reading the
`grafana-dashboards` skill (`skills/grafana-dashboards/SKILL.md` and
`references/{persona-mapping,visual-design-wow,panel-types,transforms}.md`),
and querying the **live** ClickHouse `flows` table and Akvorado Prometheus
metrics in the `monitoring` namespace of the homelab cluster to check what
data actually exists versus what the current dashboard uses.

---

## 1. What exists today

Four tabs, ~25 panels: **Flow Overview** (hero stats, throughput-by-direction,
top talkers/destinations, top conversations table), **Applications** (ports,
protocol mix, packet size, TCP flags, service-class donut), **External**
(AS/country resolution %, top AS/country, AS conversation matrix),
**Pipeline Health** (exporter/collector state, capture drops, flow table
occupancy, collector error reasons).

### What's good

- **Pipeline Health tab is genuinely excellent.** It states plainly that flow
  data can be "plausible but wrong," names all three failure modes (HW
  offload, pcap drops, forced expiry), and gives every number needed to judge
  trust in the rest of the dashboard. This is the right pattern and should be
  extended, not replaced.
- **Resolution-percentage panels** (ASN Coverage, Destination/Source AS
  Resolution, Country Resolution) are a smart honesty pattern — an empty
  enrichment panel and a broken one look identical unless you also show what
  fraction resolved.
- Text intro panels correctly set expectations per tab (e.g. "ports are a
  weak proxy for application identity").
- Sampling-rate correction (`Bytes * SamplingRate`) is applied consistently.
- Router variable, ClickHouse/Prometheus datasource variables, and the
  `$__conditionalAll` pattern are used correctly throughout.

### What's bad / flat

- **Visually it fails the skill's five-second test.** Hero stats use flat
  `background_solid` blue for everything regardless of meaning — there is no
  color identity, no severity signal (compare to `references/visual-design-wow.md`
  §"Hero KPI band" and §"Semantic color identity"). Every stat, every bar
  gauge is the same blue. A dashboard about *security-relevant* per-flow data
  currently looks less alive than the router CPU graph.
- **Screenshots show several bar-gauge panels rendering empty** (Top Local
  Talkers, Top External Destinations, Top Destination/Source Ports, Top Local
  Service Ports, Top Destination/Source AS, Top Destination/Source Country) —
  visible in `OpenWrt-NetFlow-screenshots/*.png`. Live ClickHouse queries in
  §2 confirm the underlying data exists and is non-trivial, so this is most
  likely a headless-screenshot timing/render artifact (ClickHouse queries are
  slower than Prometheus and may not have resolved before the snapshot was
  taken) rather than broken queries — **but the next agent must verify this
  live in a browser**, not just assume it. A dashboard that visibly renders
  empty panels on first paint fails the five-second test even if a manual
  refresh fixes it; consider whether panel-level loading states or a longer
  default `refresh`/`preload` setting is warranted.
- **Only two panel types outside the hero row are non-tabular**: timeseries
  and bargauge, repeated ten times. No heatmap, no state-timeline, no geomap,
  no node-graph — the skill explicitly calls texture variety a requirement,
  and this repo's own tables have Grafana's most visually distinctive panel
  types unused. Packet Size Distribution and TCP Flag Combinations are raw
  tables of numbers that are much stronger as a heatmap and a decoded flag
  legend, respectively.
- **TCP flags are shown as undecoded bitmask integers** (`19`, `27`, `23`...).
  These are genuinely interesting — `19 = FIN+SYN+ACK` (clean completed
  connection), low values with only `SYN` set (`2`) are the classic scan
  signature — but nobody reads bitmasks. This is a missed "wow" opportunity:
  decoded and colored, this becomes a legible security signal.
- **Pipeline health is instantaneous only.** Every health tile is a `stat`
  showing the current value; there's no history of exporter up/down or
  collector-available over time, so "was it down at 3am" is unanswerable
  without going to Prometheus directly. A state-timeline fixes this in one
  panel and is exactly the underused panel type the skill calls out as "the
  most-praised panel in a review."
- **No geography visualization**, despite country enrichment already being
  live (see §2) — this is the single biggest available wow panel per the
  skill and it isn't used at all; countries are shown only as bar-gauge names
  and a raw matrix table.
- **A large slice of already-collected data is invisible**: `FlowDirection`
  (populated, unused — see §2), the `icmp` protocol/type/code dictionary
  (unused), and roughly 20 Akvorado/inlet/outlet Prometheus metrics beyond
  the ~6 currently queried (Kafka lag, worker load, classifier cache,
  decoder throughput, insert latency — see §2.3).
- **"External Share: 9%" is one of the most interesting numbers on the whole
  dashboard** (almost all traffic never leaves the LAN) and it's buried as a
  small stat on tab 3 instead of leading the story on tab 1.

---

## 2. Ground truth from the live cluster

Queried directly against `akvorado-clickhouse-0` in the `monitoring`
namespace (`kubectl -n monitoring exec statefulset/akvorado-clickhouse --
clickhouse-client --query "..."`) and against VictoriaMetrics
(`vm.k8s.home.arpa`) on 2026-07-26. Numbers are a snapshot (~11h of flow
history since the last rollout) — re-verify before building, don't hardcode
these percentages into panel logic.

### 2.1 `flows` table (raw, 7-day TTL) — field population

| Field | Populated | Notes |
|---|---|---|
| `SrcCountry`/`DstCountry` | ~27% / ~31% of rows | GeoIP Country **is live** — `docs/netflow-akvorado.md`'s "GeoIP is not configured by default" is now stale for this cluster; update it. |
| `SrcAS`/`DstAS` | ~37% / ~40% of rows | ASN enrichment live. Most non-enriched rows are internal↔internal LAN traffic, which has no AS by definition — resolution-of-external-only would read higher than resolution-of-everything. |
| `SrcGeoCity`/`DstGeoCity` | **0%** | City DB intentionally not active — see `docs/project/ideas/akvorado-netflow-improvements.md` Priority 5 (OOMKilled orchestrator when City + Country load together under 2Gi). Do not build panels that assume city data; keep it conditional/absent-safe. |
| `DstASPath`, `Dst1st/2nd/3rdAS`, `DstCommunities`, `DstLargeCommunities` | **0%** | BGP-path enrichment requires a BGP peering source Akvorado doesn't have here (home router, no BGP). Do not build panels on these columns. |
| `FlowDirection` (`ingress`/`egress`) | 100%, real signal | **Unused today.** `docs/netflow-akvorado.md` correctly explains `InIfBoundary`/`OutIfBoundary` are constant (softflowd single capture device) and the dashboard already works around that with a `SrcNetRole`/`DstNetRole` `multiIf`. But `FlowDirection` is independently populated and correlates cleanly with that role split (egress+`SrcNetRole=internal` ≈ outbound, ingress+`DstNetRole=internal` ≈ inbound) in the live sample. Worth a cross-check panel or as a corroborating filter, not a replacement — the existing role-based logic is correct and documented; treat `FlowDirection` as an additional signal, e.g. for the security/anomaly tab (unexpected `ingress` to a LAN host on an unsolicited port is more suspicious-shaped).
| `InIfSpeed`/`OutIfSpeed` | 100%, constant `1000` | Known link speed (1 Gbps) — usable as a static denominator for a "% of link" framing on the Peak Bitrate hero stat. |
| `TCPFlags` | ~77% of TCP rows have common combos | Cumulative OR of every packet's flags in the flow lifetime, not a single packet's flags — e.g. `19` = SYN+FIN+ACK seen somewhere in the flow (a completed handshake+close), `2` alone = SYN only, never anything else (half-open/refused/scanned). Confirm this reading before building the security panel (§4.5); it comes from Akvorado's schema docs, not directly observed. |
| IPv4 vs IPv6 | ~90% / ~10% | Worth a small hero/donut split; currently invisible. |
| `ExporterRole/Site/Region/Tenant`, `SrcNetSite/Region/Tenant` etc. | 100% empty | Multi-site/tenant fields for carrier deployments; irrelevant for a single home router. Do not build panels on them. |
| `SrcNetName`/`DstNetName` | Only `lan` populated (from `clickhouse.networks` in `akvorado.yaml`) | If the operator names more prefixes (e.g. `iot`, `guest`, `servers`) this becomes a real internal-segment breakdown. Currently one segment, so a panel here would show one bar — not worth building unless the operator adds prefixes. |
| `ForwardingStatus` | Always `0` | No forwarding-failure signal in this deployment; not useful as a panel today. |

### 2.2 Rollup tables — hard constraint carried forward

`flows_1m0s` (1-minute, 7-day retention) and `flows_1h0m0s` (1-hour, 1-year
retention) **do not have `SrcAddr`, `DstAddr`, `SrcPort`, or `DstPort`** —
confirmed via `DESCRIBE TABLE`. Every per-host and per-port panel is
therefore hard-bound to the 7-day raw `flows` table, exactly as
`build_openwrt_netflow_dashboard.py`'s module docstring already says. This
does **not** change in v3: don't build a "30-day top talkers" panel, it's not
possible without changing Akvorado's `clickhouse.resolutions` schema
(`interval: 0` fields), which is out of scope here. Long-range trend panels
(throughput, protocol mix, direction split) *can* use the rollups if a wider
default time range is wanted, since they don't need addresses — worth
considering for a "last 30 days" overview stat, but keep the interactive
detail panels on the raw table with an explicit time-range cap in the panel
description.

### 2.3 Untapped Akvorado/Prometheus metrics

The dashboard's Pipeline Health tab uses `openwrt_netflow_*` (router-side)
plus a handful of `akvorado_inlet_*`/`akvorado_outlet_*` metrics. Live
`__name__` discovery against VictoriaMetrics turned up ~20 more Akvorado
metrics not used anywhere:

- **Kafka health**: `akvorado_outlet_kafka_consumergroup_lag_messages`,
  `akvorado_outlet_kafka_workers` / `_min_workers` / `_max_workers`,
  `akvorado_outlet_kafka_worker_increase_total` /
  `_worker_decrease_total` (autoscaling signal), `_disconnects_total`.
  A consumer-lag panel is the single most valuable addition here — it's the
  #1 candidate alert in `akvorado-netflow-improvements.md` Priority 3 and
  currently has zero visibility on any dashboard.
- **ClickHouse write path**: `akvorado_outlet_clickhouse_insert_time_seconds_bucket`
  (histogram → p50/p95 insert latency), `_flow_per_batch`, `_worker_overloaded_total`
  / `_worker_underloaded_total` / `_worker_steady_total`, `_wait_time_seconds_bucket`.
- **Classifier/metadata cache**: `akvorado_outlet_core_classifier_exporter_cache_items_total`,
  `_interface_cache_items_total`, `akvorado_outlet_metadata_cache_hits_total` /
  `_misses_total` / `_expired_entries_total` / `_refreshes_total`,
  `akvorado_outlet_metadata_provider_errors_total` — a hit/miss ratio here is
  a good "is the ifIndex cache healthy" signal, complementing the existing
  Collector Error Reasons panel.
- **Decoder throughput**: `akvorado_outlet_flow_decoder_netflow_records_total`,
  `_packets_total`, `_sets_total`, `_templates_total` — templates-only-no-data
  is a named troubleshooting symptom in `docs/netflow-akvorado.md`'s
  troubleshooting table and currently has no dashboard signal at all.
- **Routing**: `akvorado_outlet_routing_routing_lookups_total` /
  `_routing_failed_lookups_total`.
- **HTTP/self-observability**: `akvorado_common_httpserver_*` (inflight
  requests, request duration) — lower priority, mostly useful if the console
  gets exposed publicly (`akvorado.daddylars.dk` via Pangolin, per
  `docs/operations/akvorado-netflow.md`) and its own load becomes worth
  watching.

None of these were visible on the current dashboard. A fifth tab (§4.6)
should surface the Kafka/ClickHouse-write/decoder ones at minimum — they turn
"is the pipeline healthy" (current tab, binary/instant) into "where exactly
in the pipeline is it struggling" (rate-of-change, saturation, backlog).

### 2.4 Other live facts worth designing around

- Single exporter (`openwrt-main`), single interface (`br-lan`) in this
  deployment — the `router` variable and per-exporter breakdowns exist for
  future multi-router growth but currently always resolve to one value. Keep
  the variable (cheap, future-proof) but don't spend panel budget on
  per-exporter comparison panels that would show one bar today.
- Top destination ASes by bytes in the live sample: Quad9, Google, Statens IT
  (Danish public sector network — worth noting for the operator, this is
  likely a VPN or work connection), Cloudflare, Meta, Datacamp (frequently a
  hosting/proxy network worth flagging), Apple, Akamai, TDC A/S (Danish ISP),
  Amazon. This is a genuinely interesting "who is my home network actually
  talking to" story — exactly the kind of thing that makes people say wow
  when they see their own traffic mapped to real company names, and it's
  already working end to end.
- Top destination countries: DK, US, SE, DE, FI, CY, IE, NL, NO, FR — real
  geographic spread, good geomap material (§4.3).

---

## 3. Design direction

Apply `references/persona-mapping.md` and `references/visual-design-wow.md`
from the `grafana-dashboards` skill. This is a **single-audience,
General/home-operator dashboard** with an OPS-style health tab bolted on —
not a multi-persona split. Keep the existing 4-tab structure (it maps
cleanly to: "what happened" / "what app-ish thing" / "who on the internet" /
"can I trust these numbers") and add:

- **Tab 5: Pipeline Internals** (or fold into Pipeline Health as a second
  half) for the untapped Kafka/ClickHouse/decoder metrics from §2.3. This
  keeps Pipeline Health itself under the OPS panel budget (25 panels, 12+
  requires sub-grouping) rather than overloading one tab.
- Optionally **Tab 6: Security Signals**, small and opinionated, built from
  TCP flag decoding + fan-out detection (§4.5) — only if the operator wants
  a "should I be worried" surface; flag this as optional in the build
  conversation rather than assuming it.

Panel budget: this dashboard is closest to the skill's "OPS/SRE" bucket (≤25
panels, tabs required above 12) given it already has 4 tabs and ~25 panels.
Stay near that ceiling; the goal is *better* panels, not just *more* — several
proposed panels below explicitly replace an existing one rather than adding
alongside it.

### Color identity

Pick one accent for "this is netflow data" (the current blue is fine as the
base) and reserve red/orange/yellow strictly for the Pipeline Health tab's
severity states, per the skill's semantic-color rule. Do not apply
threshold red/yellow/green to size-ranking bar gauges (Top Talkers, Top
Destinations, Top AS) — those are neutral rankings, keep
`continuous-blues` as already used. Do use real semantic thresholds on the
new Kafka-lag and insert-latency panels, where a number genuinely means
"something is wrong."

> **Correction (2026-07-26, from the build).** The "keep `continuous-blues`"
> instruction above was followed and then reversed on the operator's report
> that the bars were hard to read — which a side-by-side render confirmed. A
> continuous scheme maps the *smallest* values to the dark end of its ramp,
> so on Grafana's dark theme everything below the top two or three rows is
> dark blue on dark grey; and because `valueMode` is `"color"`, the **values
> become unreadable too**, not just the bars. On a 12-row top-N, rows 6-12
> had no legible number at all.
>
> All neutral rankings now use `color: {"mode": "thresholds"}` with a single
> neutral base step: the same blue accent, uniform and readable, still
> implying nothing. `palette-classic` was the other candidate and is equally
> readable, but hands some rows red and green, which reads as severity on
> data that has none.
>
> The rest of the paragraph stands — graded red/yellow/green still belongs
> only on Kafka lag, insert latency, and utilization bars.
>
> This also surfaced a latent bug: `THRESHOLDS["neutral"]` used a base step
> of `0` rather than `null`, which left negative values matching no step.
> Invisible while rankings used a continuous scheme (those ignore thresholds
> entirely), it would have rendered every bar of the dBm signal rankings
> uncoloured. Fixed repo-wide; see SKILL.md golden rule 22.

---

## 4. Concrete panel proposals

Each item names: what tab, what panel type, why it's better than what's
there (or why it's new), and a SQL/PromQL sketch. Sketches are directional —
verify exact `dictGetOrDefault`/enum syntax against a live query before
committing to the generator, per the skill's "verify before generating"
rule, and confirm option key names against the target Grafana's Panel JSON
inspector per SKILL.md golden rule 27.

### 4.1 Flow Overview — lead with the real story

- **Promote "External Share" and an IPv4/IPv6 split into the hero row.**
  These are two of the most tell-a-story numbers on the whole dataset and
  are currently buried on tab 3 (External Share) or absent entirely (IPv6
  share). New hero stat: `% of bytes leaving the LAN` and `% of bytes over
  IPv6`.
- **Peak Bitrate as "% of gigabit link"**: since `InIfSpeed`/`OutIfSpeed` are
  a known constant 1000 Mbps, add a second small stat or a gauge showing
  peak bitrate against link capacity. Purely contextual, cheap, effective.
- **Replace "Throughput by Direction" `timeseries` with a stacked-percent
  variant as a second panel**, not a replacement — keep the current
  absolute-bps view (answers "how much"), add a `stacking.mode: "percent"`
  companion (answers "what share is outbound vs inbound vs local right
  now"), per the skill's stacked-composition guidance. Only if panel budget
  allows; otherwise skip.
- **Top Local Talkers / Top External Destinations**: keep as bar gauges
  (already correctly styled with `continuous-blues`), but verify live in a
  browser that they actually populate — see §1's screenshot flag — before
  treating this redesign as fixing a real bug versus a non-issue.

### 4.2 Applications — decode, don't dump raw numbers

- **TCP Flag Combinations → decode the bitmask.** Replace or augment the raw
  integer table with a `field.mappings`/regex-decoded label:
  `SYN only (2)` → "Scan / half-open", `SYN+ACK+FIN (19)` → "Completed
  connection", `RST` present → "Reset/refused". This can be done in SQL
  (`multiIf` on bitwise `bitAnd(TCPFlags, 2) != 0` etc., building a readable
  string column) or via Grafana value mappings on the numeric field if the
  combination space is small enough (confirm cardinality live — the sample
  had ~10+ distinct combos, manageable). This is the single highest-value
  "decode raw data into a real signal" change on the whole dashboard.
- **Packet Size Distribution → heatmap over time**, not a static table.
  `PacketSizeBucket` already exists as a ClickHouse computed column; bucket
  by time and size to show a heatmap (small-packet-heavy = interactive/VoIP,
  large-packet-heavy = bulk transfer, and the shift between them over a day
  is visually striking). Keep the current table as a secondary/detail panel
  if space allows, but the heatmap should lead.
- **Service Class Mix → add a stacked-over-time companion** to the existing
  donut (donut answers "right now", stacked timeseries answers "did the mix
  change today") — same treatment as 4.1's direction panel.

### 4.3 External — add the geomap (the single biggest available wow panel)

- **World map of destination countries.** `DstCountry` is a live,
  reasonably-populated 2-letter code (~27 distinct countries in the sample).
  Use the `fieldLookup` transform (documented in
  `references/transforms.md`) to resolve country codes to coordinates, then
  a `geomap` panel with marker size/color by bytes. This is explicitly
  called out in `references/visual-design-wow.md` as "the single biggest wow
  panel available" when real coordinates exist — and they do here, already
  live, unused. Same idea works for `SrcCountry` as a second panel if
  inbound-from-abroad is interesting (likely small given 9% external share,
  but worth showing as a contrast).
- **Node-graph experiment (optional, higher effort)**: Local host → AS/
  country as nodes and edges, sized by bytes. Grafana's node-graph panel
  needs a strict `nodes`/`edges` frame shape (`references/panel-types.md`)
  and has a ~200-node display cap — cardinality-check the distinct
  (local host, external AS) pair count live before committing to this; if
  it's under ~100 pairs in a typical time range it's a strong "wow" addition,
  if it's in the thousands, skip it in favor of the geomap and existing AS
  Conversation Matrix table.
- Keep AS/Country resolution-percentage stats — they're correct and honest,
  just restyle for the new color identity.

### 4.4 Pipeline Health — add history, not just instant state

- **State-timeline for exporter/collector availability** over the selected
  range, replacing or complementing the current instant `stat` tiles for
  "Exporter Running" / "Flow Data Complete" / "Collector Available". This
  directly answers "was it down at 3am" which the current tab cannot. Per
  SKILL.md golden rule 21, put the value mapping in field defaults, not a
  `byType` override (documented Grafana bug).
- Keep every existing drop/expiry/error panel — they're correct and already
  follow the "no data must never render as healthy" rule.

### 4.5 Optional: Security Signals tab

Only build if the operator confirms interest — this is a stretch goal, not
core scope.

- **SYN-only flow count** (potential scan/probe signature) as a small stat
  with a sensible threshold, using the decoded TCP flags from §4.2.
- **Fan-out detector**: local hosts contacting an unusually high count of
  distinct external destinations/ports in the window — `uniq(DstAddr)` /
  `uniq(DstPort)` grouped by `SrcAddr`, top N. Frame as "hosts talking to the
  most distinct destinations," not as a definitive alert — false positives
  (browsers doing CDN fan-out) are common and the panel description should
  say so.
- **ICMP diagnostics**: join `Proto = 1` flows against the `icmp` dictionary
  table (`proto, type, code, name` — confirmed present in ClickHouse, unused
  today) to show human-readable ICMP types (echo request/reply, destination
  unreachable, time exceeded) instead of raw type/code numbers. Useful for
  "is something on my network scanning/traceroute-ing."

### 4.6 Pipeline Internals (new tab or Pipeline Health extension)

Built from the untapped Prometheus metrics in §2.3:

- Kafka consumer lag (`akvorado_outlet_kafka_consumergroup_lag_messages`) —
  timeseries with a threshold line; this is the top candidate alert in
  `akvorado-netflow-improvements.md` and currently has zero dashboard
  visibility.
- ClickHouse insert latency (`akvorado_outlet_clickhouse_insert_time_seconds_bucket`
  → `histogram_quantile` p50/p95) and batch size (`_flow_per_batch`).
- Outlet worker load (`_worker_overloaded_total` / `_underloaded_total` /
  `_steady_total`) as a stacked state-over-time panel.
- Metadata cache hit ratio (`hits / (hits + misses)`), guarded against
  divide-by-zero per SKILL.md's expression guidance.
- Decoder throughput (`netflow_records_total`, `_templates_total`) — a flat
  templates-only line with zero records growth is the exact symptom named in
  the troubleshooting table ("Only templates arriving, no data records").

---

## 5. Build notes for whoever implements this

- **Edit `build_openwrt_netflow_dashboard.py`, never the generated JSON** —
  this repo's hard rule (`AGENTS.md`, SKILL.md golden rule 28). Both output
  copies (`grafana-dashboard-exports/` and `grafana/provisioning/dashboards/`)
  must stay byte-identical to what the generator produces; regenerate with
  `python3 build_openwrt_netflow_dashboard.py` and diff both.
- Reuse existing helpers already imported from `build_openwrt_operations_dashboard`
  (`stat`, `timeseries`, `bargauge`, `table`, `piechart`, `text`, `organize`,
  `limit`, `panel`, `data_group`, `datasource_var`, `query_var`,
  `DashboardBuilder`, `THRESHOLDS`, `AVAILABILITY_MAPPINGS`, color constants)
  plus this file's own `ch_query`/`ch_stat` for ClickHouse SQL panels. Add new
  shared helpers (e.g. a `heatmap()` or `state_timeline()` builder, a
  `geomap()` builder) to the shared operations module if they'll be reused,
  or locally if netflow-only.
- Validate with `python3 -m unittest discover -s tests -p 'test_*.py'` and
  `tests/run_all.sh`; there's an existing `tests/test_netflow_config.py` and
  `tests/test_netflow_health.sh` to extend if new panels need coverage.
- **Verify every new field/metric against the live cluster before writing
  SQL/PromQL into the generator** — this document's numbers are a snapshot;
  re-run the queries in §2 (commands are in
  `docs/project/ideas/akvorado-netflow-improvements.md` and this file) before
  assuming percentages or cardinalities still hold.
- After building, **open the dashboard in a real browser and check the
  reported-empty panels from §1 render correctly** — do not declare that
  finding resolved without live verification, per this session's own
  screenshot review being inconclusive (screenshot timing vs real bug is
  unresolved).
- Update `docs/netflow-akvorado.md`: the "GeoIP is not configured by default"
  line is now stale for this cluster (Country/ASN are live per §2.1); update
  the External tab's panel list to match whatever ships; add the new tab(s)
  to the tab-list bullet near the end of that doc.
- Do not touch BGP-path columns (`DstASPath`, `Dst1st/2nd/3rdAS`,
  `*Communities`) — confirmed 0% populated and there's no BGP source in this
  deployment to populate them; building panels there would be a permanent
  "no data" wall, which the skill explicitly calls a five-second-test
  failure.
- Do not build city-level geo panels — confirmed 0% populated and
  intentionally disabled (OOM risk); this is a documented, deliberate
  limitation, not a gap to fill here.

---

## 6. Open questions for the operator (ask before assuming)

1. Interest in the optional Security Signals tab (§4.5)? It's opinionated
   and can produce false-positive-flavored panels (fan-out from normal
   browsing) if not framed carefully.
2. Node-graph (§4.3) is higher-effort with an uncertain cardinality payoff —
   worth the build time, or is the geomap + existing AS matrix enough?
3. Any appetite for widening `clickhouse.networks` in `akvorado.yaml` to name
   more internal segments (IoT, guest, servers)? Currently everything
   internal is just `lan`, which caps how interesting an internal-segment
   breakdown panel could be.
4. Should Pipeline Internals (§4.6) be its own tab or folded into Pipeline
   Health as a second section? Affects whether Pipeline Health needs
   row/tab-grouping to stay under the OPS panel-budget-per-tab guidance.
