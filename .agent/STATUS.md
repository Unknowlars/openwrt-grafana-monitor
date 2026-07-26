# Current status

## NetFlow dashboard v3 (2026-07-26) — built, validated, not committed

`docs/netflow-dashboard-v3-plan.md` is implemented. `openwrt-netflow-v2` is now
72 panels across 6 tabs (added Security Signals, Pipeline Internals). All
checks pass; both generated copies are in sync and uncommitted.

Two things a follow-up agent should know:

- **The v3 plan's open question about empty bar gauges is resolved: it was a
  real bug, not a screenshot artifact.** ClickHouse-backed `bargauge` and
  `piechart` need `reduceOptions.values = true`. Four more render-only bugs
  were found and fixed the same way (long-format timeseries, geomap gazetteer,
  state-timeline legend) — see the 2026-07-26 CHANGELOG entry. All five are
  now covered by tests in `tests/test_netflow_config.py`.
- **Live rendering was verified against the docker-compose stack**, whose
  ClickHouse stopped receiving flows at 2026-07-25 21:41 UTC. The k8s
  `monitoring/akvorado-clickhouse` is the currently-fed instance. Queries were
  verified against both; only the browser rendering used the stale copy. If
  the docker stack is meant to still be receiving flows, that gap is worth
  investigating separately — it is not something this work changed.

Not done, deliberately: no internal-segment panel (operator declined widening
`clickhouse.networks`), and no ICMP type/code decoding (softflowd does not
export those fields — now documented as limit 9 in `docs/netflow-akvorado.md`).

## Remediation plan

`docs/CODE-REVIEW-REMEDIATION-PLAN.md` is still the source of truth.
Remediation tasks `R1`-`R13` are implemented and validated. The next unchecked
plan task is `M1` (global conntrack table saturation metric).

Do not commit, stage, reset, clean, or discard worktree changes unless the user
explicitly asks for that exact git operation.

## In flight: multi-router topology rework (2026-07-25)

The topology node graph was built for a single router and produced a visibly
wrong graph on this two-router deployment. Reworked in source, **not yet
deployed to the routers**.

What changed:

- `openwrt/collectors/topology.lua` detects gateway vs downstream AP. Only a
  gateway emits `internet`, `modem:`, `router:` and `port:` nodes. Node ids are
  now `router:<lan-ip>` and `bss:<bssid>` so every exporter names the same
  thing identically. Every node/edge carries `authority`; new
  `openwrt_topology_infra_mac` publishes each box's own interface MACs.
- New `openwrt/lua/oui.lua` + generated `oui_data.lua` (from
  `build_oui_table.py`) name clients by hardware vendor and pick an icon.
- `alloy/config.alloy` lowercases `mac`, `station` and `bssid` at ingest.
- Topology dashboard reconciles across routers in PromQL, uses the layered
  layout, semantic arcs, live B/s on both frames, signal-coloured association
  edges, and click-through data links. Mission Control panel 707 matches.
- `build_openwrt_clients_dashboard.py` gained a `mac` variable (defaults to
  All) so the node-graph data link can scope it to one device.

### Deployed 2026-07-25

Both routers were updated with `openwrt_run_repo_setup confirm=true` (profile
`full`), the exporter restarted on each, and Alloy restarted. `ip-bridge`
installed on both, so the switch-port tier is live. Verified against live data:
role detection correct (`openwrt-main` gateway, `openwrt-new` downstream AP with
no fabricated uplink), 0 colliding node ids, 0 dangling edge sources/targets.

**Two Prometheis scrape these routers.** `vm.k8s.home.arpa` is scraped by
`kube-prometheus-stack` in the k8s cluster, *not* by this repo's Alloy, so the
MAC-lowercasing relabel rule does not apply there — VictoriaMetrics keeps
uppercase `mac`/`station`, the local otel-lgtm stack has them lowercase. Each
store is internally consistent, so every existing MAC join still works in both;
but do not write a query that assumes one casing without checking which store it
will run against.

### Pending

- Grafana UI has not been re-screenshotted since the rework;
  `OpenWrt-Topology-screenshots/` still shows the old broken graph.

## NetFlow via Akvorado live state (2026-07-25)

The gateway and monitoring host now have working NetFlow data. The router-side
softflowd config renders `option interface '8:br-lan'`, matching
`/sys/class/net/br-lan/ifindex`. `softflowd` is running, exporting flows, and
reported zero libpcap drops, zero interface drops, zero forced expiries, and zero
export failures in the user's live check.

Akvorado is healthy after the GeoIP filename cleanup. Live ClickHouse checks
confirmed fresh rows in `flows`, populated `SrcAS`/`DstAS`, populated source and
destination ports, and AS dictionary names. The dashboard source is
`build_openwrt_netflow_dashboard.py`; it now generates a richer 48-panel
`openwrt-netflow-v2.json` with source/destination AS, source/destination
country, source/destination ports, service-class buckets, AS/country matrices,
and pipeline-health proof.

Remaining caveats:

- Live Grafana rendering/screenshots of `openwrt-netflow-v2.json` have not been
  captured in this status file.
- `openwrt_flow_offload_enabled{mode="hw"}` is enabled, so flow totals are a
  lower bound unless the operator disables hardware flow offload.
- softflowd CPU cost on this MT7621-class router has still not been measured
  with the M10 load-test protocol in `docs/client-topology-and-netflow-plan.md`.
- For additional routers, keep `akvorado/exporters.yaml` in sync with the real
  ifIndex; a mismatch still drops every flow from that exporter.

### Kubernetes guide

`docs/kubernetes-monitoring-setup.md` was rewritten alongside this work. Its
Step 4 (`metricRelabelings`) is a hand-maintained mirror of the
`prometheus.relabel "openwrt_bound_cardinality"` block in `alloy/config.alloy`.
Nothing enforces that they stay in sync — if you add or change a relabel rule
there, update the Kubernetes doc too, or the two stores diverge on MAC casing
and MAC-keyed dashboards silently empty against one of them.

The manifests in that guide are illustrative and are not exercised by
`tests/run_all.sh`. What was checked offline: YAML parses, the relabel rules
match the Alloy block one-for-one, the embedded Alloy config passes `alloy fmt`,
and every metric name cited exists in the repo. Nothing was applied to a
cluster.

### Design corrections from an operator's working Elastic setup (2026-07-25)

A previously-working softflowd config (capturing `br-lan`, exporting v9 to an
Elastic collector) surfaced three real errors in the first implementation, all
now fixed: WAN-vs-LAN capture point, `InIfBoundary` being unusable with
softflowd, and `TCPFlags` being disabled by default in Akvorado's schema. See
`.agent/CHANGELOG.md` for the detail. The lesson worth keeping: softflowd is a
single-interface exporter with no notion of ingress vs egress, so anything in
this stack that reasons about interface direction is wrong by construction.

## Validation

Static/offline checks passed on 2026-07-25:

- `sh tests/run_all.sh` (includes the new `tests/test_topology_promql.sh`)
- `python3 -m unittest discover -s tests -p 'test_*.py'` (35 tests)
- `sh -n openwrt/setup.sh openwrt/scripts/*.sh tests/*.sh`
- `luac5.1 -p openwrt/collectors/*.lua openwrt/lua/*.lua`
- `docker compose config` and `docker compose --profile netflow config`
- `alloy fmt` against `grafana/alloy:latest`
- `akvorado orchestrator --check --dump` against
  `quay.io/akvorado/akvorado:2.4.1`, confirming the config parses, the
  `!include` of `exporters.yaml` splices into `outlet.metadata.providers`, and
  `default-sampling-rate` normalises to `::/0: 1`

`tests/test_topology_promql.sh` runs the dashboard's shipped node-graph queries
against a throwaway VictoriaMetrics container over exposition the real collector
produces for a simulated gateway plus dumb AP. It asserts the reconciliation
end to end: single uplink spine, AP not rendered as a client, wifi client drawn
on its association rather than the gateway's wired guess, vendor titles, live
throughput on both frames, and zero dangling endpoints. Mutating the generator
to drop the infra-MAC suppression makes it fail, so it is live coverage.

## Remaining risks

- The reconciliation is verified against simulated exposition, not against the
  live routers, because they still run the previous collector. Re-verify after
  deployment with the collision/dangling panels on the Data Quality tab.
- The switch-port tier needs the `bridge` command (`ip-bridge`, added as an
  optional package). Without it the collector falls back to attaching wired
  clients directly to the router rather than guessing a port. Not yet confirmed
  present on either router.
- `.env` is private and contains real local values. Do not quote secrets from
  Compose profile output or MCP/router diagnostics.
