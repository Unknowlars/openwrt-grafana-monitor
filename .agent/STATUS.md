# Current status

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

## Validation

Static/offline checks passed on 2026-07-25:

- `sh tests/run_all.sh` (includes the new `tests/test_topology_promql.sh`)
- `python3 -m unittest discover -s tests -p 'test_*.py'` (27 tests)
- `sh -n openwrt/setup.sh openwrt/scripts/*.sh tests/*.sh`
- `luac5.1 -p openwrt/collectors/*.lua openwrt/lua/*.lua`
- `docker compose config`
- `alloy fmt` and `alloy validate` against `grafana/alloy:latest`

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
