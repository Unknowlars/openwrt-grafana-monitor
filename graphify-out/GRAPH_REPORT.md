# Graph Report - openwrt-grafana-monitor  (2026-07-25)

## Corpus Check
- 101 files · ~369,894 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 986 nodes · 1706 edges · 95 communities (68 shown, 27 thin omitted)
- Extraction: 99% EXTRACTED · 1% INFERRED · 0% AMBIGUOUS · INFERRED: 13 edges (avg confidence: 0.52)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `f5066af5`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- MCP Server Core
- Advanced Dashboard
- Mission Control Dashboard
- Lua Collectors
- Legacy Dashboards
- Topology Collection
- Setup & Installation
- DPI & Traffic Analysis
- Device Traffic Metrics
- Client Conntrack Monitoring
- Client Traffic Monitoring
- WAN Quality Monitoring
- Topology Tests
- Metrics Exposition
- WiFi Dethrash Detection
- DNS & DHCP Monitoring
- Firewall Counters
- Package Init
- Device Status Monitoring
- DHCP Pool Monitoring
- Filesystem Monitoring
- Inode Monitoring
- IPv6 Health Monitoring
- Link Health Monitoring
- Packet Loss Monitoring
- Service Health Monitoring
- Softnet Stats Monitoring
- SQM Queue Monitoring
- WAN Info Monitoring
- WiFi Radio Monitoring
- Test Suite Runner
- Conntrack Tests
- Client Traffic Tests
- SQM Collector Tests
- CODE-REVIEW-REMEDIATION-PLAN.md
- 4.1 `docs/openwrt-setup.md`
- Current agent-readiness
- 2.2 `docker-compose.yml`
- 3.1 `build_dashboards.py` (the 4 classic dashboards)
- Troubleshooting
- Kubernetes monitoring setup
- 1.3 `openwrt/scripts/`
- OpenWrt Router Setup
- 1.1 `openwrt/setup.sh`
- OpenWrt Grafana Monitor
- 5.2 Coverage gaps
- Part 6 — Missing metrics / data the OpenWrt router exposes but the repo doesn't capture
- Safe instruction improvements
- 11. Milestone specifications
- Advanced Router Profiles
- Monitoring Host Setup
- OpenWrt Grafana Monitor
- Plan: Client Inventory, Network Node Graph, and Traffic Attribution
- 1.2 `openwrt/collectors/`
- Monitoring Host Setup
- Current status
- 3. Phase 3 — Traffic attribution
- 4. Metrics and features worth adding
- 9. Implementation conventions — read before writing code
- SSH MCP sidecar
- Repository map
- 10. Metric contract
- 8. Risks and open questions
- CODE-REVIEW-FINDINGS.md
- 1. Phase 1 — Unified client inventory
- 2. Phase 2 — Node graph
- Dashboard previews
- 0. Where the repo is today
- opencode.json
- Agent changelog
- graphify.js
- CLAUDE.md
- test_setup_legacy_crontab.sh
- test_wan_quality.sh
- Manual Setup
- test_wan_info.sh
- test_mcp_policy.py
- server.py
- core.py
- resolve_host_key_policy
- ._connect
- test_inodes.sh
- build_oui_table.py
- SecurityMiddleware
- oui.lua

## God Nodes (most connected - your core abstractions)
1. `PolicyError` - 29 edges
2. `build_dashboard()` - 28 edges
3. `prom_query()` - 23 edges
4. `build_dashboard()` - 22 edges
5. `DashboardBuilder` - 22 edges
6. `tf()` - 19 edges
7. `collect()` - 19 edges
8. `1.3 `openwrt/scripts/`` - 19 edges
9. `tab_lan()` - 18 edges
10. `deep()` - 18 edges

## Surprising Connections (you probably didn't know these)
- `CommandPolicyTests` --uses--> `PolicyError`  [INFERRED]
  tests/test_mcp_policy.py → mcp_server/core.py
- `HostKeyPolicyTests` --uses--> `PolicyError`  [INFERRED]
  tests/test_mcp_policy.py → mcp_server/core.py
- `build_dashboard()` --calls--> `DashboardBuilder`  [EXTRACTED]
  build_openwrt_advanced_dashboard.py → build_openwrt_operations_dashboard.py
- `build_dashboard()` --calls--> `DashboardBuilder`  [EXTRACTED]
  build_openwrt_clients_dashboard.py → build_openwrt_operations_dashboard.py
- `stat()` --calls--> `prom_query()`  [EXTRACTED]
  build_openwrt_mission_control.py → build_openwrt_operations_dashboard.py

## Import Cycles
- None detected.

## Communities (95 total, 27 thin omitted)

### Community 0 - "MCP Server Core"
Cohesion: 0.14
Nodes (24): build_system_facts_command(), CommandSpec, require_confirm(), openwrt_configure_syslog(), openwrt_diagnostic(), openwrt_metrics_sample(), openwrt_monitoring_status(), openwrt_restart_monitoring() (+16 more)

### Community 1 - "Advanced Dashboard"
Cohesion: 0.08
Nodes (84): availability(), build_dashboard(), iter_strings(), layout_refs(), main(), Any, Profile health tile.      A collector's own availability flag is not sufficient, validate_dashboard() (+76 more)

### Community 2 - "Mission Control Dashboard"
Cohesion: 0.16
Nodes (50): bargauge(), build_dashboard(), clean(), color_override(), data_group(), _edges_expr(), gauge(), grid_items_by_tab() (+42 more)

### Community 3 - "Lua Collectors"
Cohesion: 0.23
Nodes (18): arp_by_ip(), assoc_by_mac(), collect(), config_value(), flow_offload_state(), is_locally_administered(), lease_expiry_by_mac(), leasefile_hosts() (+10 more)

### Community 4 - "Legacy Dashboards"
Cohesion: 0.32
Nodes (16): bargauge(), build_devices(), build_logs(), build_network(), build_overview(), loki_logs(), loki_stat(), loki_tgt() (+8 more)

### Community 5 - "Topology Collection"
Cohesion: 0.19
Nodes (24): add_edge(), add_node(), annotate_channels(), arp_table(), assoc_by_mac(), bridge_fdb(), collect(), default_route() (+16 more)

### Community 6 - "Setup & Installation"
Cohesion: 0.31
Nodes (12): die(), ensure_cron_line(), ensure_dir(), fetch_url(), install_file(), log(), pkg_install_optional(), pkg_install_required() (+4 more)

### Community 7 - "DPI & Traffic Analysis"
Cohesion: 0.38
Nodes (9): collect(), label(), list_from(), number(), read_status(), scrape(), status_age(), top_entries() (+1 more)

### Community 8 - "Device Traffic Metrics"
Cohesion: 0.36
Nodes (4): collect(), config_value(), lease_map(), read_set()

### Community 9 - "Client Conntrack Monitoring"
Cohesion: 0.32
Nodes (11): collect_client_hosts(), collect_client_hosts_fallback(), collect_client_hosts_hints(), collect_conntrack_rows(), emit_assoc_events(), emit_conntrack(), fail_closed(), headers() (+3 more)

### Community 11 - "Client Traffic Monitoring"
Cohesion: 0.52
Nodes (5): fail_closed(), openwrt-monitor-client-traffic.sh script, valid_mac(), valid_number(), write_headers()

### Community 12 - "WAN Quality Monitoring"
Cohesion: 0.38
Nodes (3): openwrt-monitor-wan-quality.sh script, write_dns_probe(), write_probe()

### Community 15 - "Metrics Exposition"
Cohesion: 0.60
Nodes (4): find_duplicates(), main(), parse(), Return (name, normalised-labels, value) for every sample line.

### Community 16 - "WiFi Dethrash Detection"
Cohesion: 0.60
Nodes (3): hostname(), sanitize(), scrape()

### Community 27 - "Inode Monitoring"
Cohesion: 0.70
Nodes (4): emit_inodes(), emit_mount_df(), emit_mount_stat(), openwrt-monitor-inodes.sh script

### Community 41 - "CODE-REVIEW-REMEDIATION-PLAN.md"
Cohesion: 0.06
Nodes (35): A.1 — R1 Alloy image defaults, A.2 — R2 legacy-crontab abort, A.3 — R3 awk on a missing ROWSFILE, A.4 — R13 profile validation asymmetry, Appendix A — reproductions, Appendix B — relationship to `docs/CODE-REVIEW-FINDINGS.md`, Batch A — outright breakage, Batch A — outright breakage (+27 more)

### Community 42 - "4.1 `docs/openwrt-setup.md`"
Cohesion: 0.06
Nodes (31): 4.1 `docs/openwrt-setup.md`, 4.2 `docs/monitoring-host-setup.md`, 4.3 `docs/troubleshooting.md`, 4.4 `docs/kubernetes-monitoring-setup.md`, 4.5 `docs/advanced-profiles.md`, 4.6 `docs/client-topology-and-netflow-plan.md`, [P0] "Adding more routers" advice is stale/wrong for the current stack, [P0] Document header is stale and contradicts its own body (+23 more)

### Community 43 - "Current agent-readiness"
Cohesion: 0.07
Nodes (29): 1. Missing shared root `AGENTS.md`, 2. Duplicated Claude-only instructions, 3. README dashboard map is stale, 4. Validation script and fixture set are ignored, 5. Tracked `.env` contains private values, 6. Large docs and generated/reference files invite unnecessary context loading, 7. No bounded durable agent handoff files, Agent exploration cost (+21 more)

### Community 44 - "2.2 `docker-compose.yml`"
Cohesion: 0.08
Nodes (25): 2.1 `alloy/config.alloy`, 2.2 `docker-compose.yml`, 2.3 `.env.example`, [P1] `depends_on: service_healthy` against an image with no explicit compose `healthcheck`, [P1] `MONITORING_HOST_IP` is documented but consumed by no compose service or Alloy config, [P1] `node_nat_traffic` drop rule uses unanchored regex; matches `node_nat_traffic_total` too if it appears, [P1] Read-only provisioning mount contradicts `allowUiUpdates: true`, [P1] Syslog `router`-label rewrite ordering is fragile (+17 more)

### Community 45 - "3.1 `build_dashboards.py` (the 4 classic dashboards)"
Cohesion: 0.09
Nodes (23): 3.1 `build_dashboards.py` (the 4 classic dashboards), 3.2 v2 builders (`build_openwrt_operations_dashboard.py`, `build_openwrt_advanced_dashboard.py`, `build_openwrt_topology_dashboard.py`, `build_openwrt_clients_dashboard.py`), 3.3 Metric/label mismatches between emit-side and dashboards, [P0] "Static Reservations" panel queries the wrong metric, [P0] v1 `router` template variable is hardcoded to `openwrt`; multi-router breaks, [P1] Cross-dashboard nav links omit all 4 v2 dashboards, [P1] Hardcoded `version: 1` overwrites saved versions; no determinism check; no overlap detection, [P1] Heavy code duplication across the v2 builders (+15 more)

### Community 46 - "Troubleshooting"
Cohesion: 0.09
Nodes (22): 1. Check if logd is sending syslog, 1. Check the router's metrics endpoint, 1a. Check which collectors are actually succeeding, 2. Check Alloy is receiving syslog, 2. Check Alloy is scraping, 3. Check port 514 is accessible, 3. Check Prometheus received data, 3a. Check the custom dashboard metrics directly (+14 more)

### Community 47 - "Kubernetes monitoring setup"
Cohesion: 0.11
Nodes (18): Dashboard panels show no data, Example values, Kubernetes monitoring setup, Logs do not appear, Prometheus has no `openwrt` target, Quick troubleshooting, Simple checklist, Step 1 - Pick and reserve a syslog IP (+10 more)

### Community 48 - "1.3 `openwrt/scripts/`"
Cohesion: 0.11
Nodes (19): 1.3 `openwrt/scripts/`, `openwrt-monitor-client-conntrack.sh`, `openwrt-monitor-client-traffic.sh`, `openwrt-monitor-device-status.sh`, `openwrt-monitor-dhcp-pool.sh`, `openwrt-monitor-filesystem.sh`, `openwrt-monitor-firewall-counters.sh`, `openwrt-monitor-inodes.sh` (+11 more)

### Community 49 - "OpenWrt Router Setup"
Cohesion: 0.18
Nodes (11): Files This Repo Adds To The Router, Important Notes, On the monitoring host, On the router, OpenWrt Router Setup, Optional Packages, Recommended Setup, Required Packages (+3 more)

### Community 50 - "1.1 `openwrt/setup.sh`"
Cohesion: 0.13
Nodes (15): 1.1 `openwrt/setup.sh`, [P0] `conntrack-tools` is never installed, but the `clients` profile depends on it, [P0] Profile downgrade is not idempotent — stale collectors, cron lines, nft rule, protocols file persist, [P0] UCI `textfile_dir` is never configured — silent loss of all custom metrics, [P1] `/etc/init.d/firewall restart` runs unconditionally on every traffic-profile rerun, [P1] Five `/etc/init.d/* restart` calls under `set -e` with no per-service diagnostic, [P1] `pkg_install_required` aborts the whole setup on the first missing/offline package, [P1] UCI configuration calls are unguarded and not batched (+7 more)

### Community 51 - "OpenWrt Grafana Monitor"
Cohesion: 0.14
Nodes (13): Agent Handoff Files, Architecture Boundaries, Component Map, Default Exclusions, Definition of Done, Editing, Efficient Repository Navigation, Git and Reporting (+5 more)

### Community 52 - "5.2 Coverage gaps"
Cohesion: 0.14
Nodes (14): 5.1 Coverage, 5.2 Coverage gaps, 5.3 Fixture realism — verified, 5.4 Are the Lua tests testing the collectors correctly?, [P1] 13 of 16 helper scripts have no tests, [P1] Classic dashboards are not determinism-verified, [P1] Core-profile Lua collectors have no behaviour tests, [P2] `check_exposition.py` scope is narrow (+6 more)

### Community 53 - "Part 6 — Missing metrics / data the OpenWrt router exposes but the repo doesn't capture"
Cohesion: 0.14
Nodes (14): [P1] CPU frequency / thermal-throttling counters, [P1] IPv6 device-traffic accounting, [P2] BATMAN-adv mesh counters, [P2] Disk/IO stats for USB/SD storage, [P2] Ingress SQM/ifb stats, [P2] uhttpd (admin-UI) access/error counters, [P3] Deprecated vs preferred IPv6 address distinction, [P3] `nft_counter_bytes` only — packets queried, bytes not (+6 more)

### Community 54 - "Safe instruction improvements"
Cohesion: 0.15
Nodes (12): 1. Add concise `docs/repository-map.md`, 1. Create root `AGENTS.md`, 2. Simplify `CLAUDE.md`, 2. Update `.gitignore` for validation harness visibility, 3. Add explicit efficient-navigation and exclusion rules, 3. Add README link to repository map, 4. Add `.agent` memory rules, 4. Document tracked `.env` risk without editing secrets (+4 more)

### Community 55 - "11. Milestone specifications"
Cohesion: 0.15
Nodes (13): 11. Milestone specifications, M0 — Bound existing cardinality; make profiles composable — done (2026-07-22), M10 — softflowd measurement spike → see §12.2, M11 — NetFlow (conditional on M10), M1 — Client inventory collector — done, live-verified (2026-07-22), M2 — Multi-target Alloy, M3 — Clients dashboard, M4 — Node graph feasibility spike — done (2026-07-22) (+5 more)

### Community 56 - "Advanced Router Profiles"
Cohesion: 0.15
Nodes (13): Advanced Router Profiles, Client inventory collector, Dashboard and validation, DPI collector, Flow offload makes traffic accounting unreliable, Per-client conntrack and WiFi roaming, Per-client nlbwmon traffic, Profiles (+5 more)

### Community 58 - "OpenWrt Grafana Monitor"
Cohesion: 0.12
Nodes (17): Adapting to your router, Architecture, Configuration, Dashboard previews, Devices, Docs, Logs, Network (+9 more)

### Community 59 - "Plan: Client Inventory, Network Node Graph, and Traffic Attribution"
Cohesion: 0.18
Nodes (10): 12.1 M4 — node graph frame detection, 12.2 M10 — softflowd cost on MT7621, 12. Spike protocols, 13. References, 5.1 Rules worth having, 5. Alerting, 6. What this draft cuts, 7. Sequencing (+2 more)

### Community 60 - "1.2 `openwrt/collectors/`"
Cohesion: 0.20
Nodes (10): 1.2 `openwrt/collectors/`, `client_inventory.lua`, `device_status.lua`, `device_traffic.lua`, `dnsmasq.lua`, `dpi_netifyd.lua`, `packet_loss.lua`, `topology.lua` (+2 more)

### Community 61 - "Monitoring Host Setup"
Cohesion: 0.17
Nodes (12): 1. Clone the repo, 2. Configure, 3. Start, 4. Open Grafana, Adding more routers, Alloy UI, Data persistence, Monitoring Host Setup (+4 more)

### Community 62 - "Current status"
Cohesion: 0.25
Nodes (7): Current status, Deployed 2026-07-25, In flight: multi-router topology rework (2026-07-25), Pending, Remaining risks, Remediation plan, Validation

### Community 63 - "3. Phase 3 — Traffic attribution"
Cohesion: 0.25
Nodes (8): 3.1 What the question actually is, 3.2 Re-ranked options, 3.3 Recommended: nlbwmon, 3.4 The offload problem — this is the real risk, and it is shared, 3.5 softflowd, if (c) is genuinely required, 3.6 Collector side: flowlogs-pipeline, with one claim corrected, 3.7 The cardinality rules, unchanged in substance, 3. Phase 3 — Traffic attribution

### Community 64 - "4. Metrics and features worth adding"
Cohesion: 0.25
Nodes (8): 4.1 Per-client DNS query attribution — **adopt, and it is nearly free**, 4.2 Per-client conntrack entries — **adopt**, 4.3 Roaming events between APs — **adopt, via Loki, not Prometheus**, 4.4 IPv6 neighbour tracking — **adopt in reduced form**, 4.5 Guest vs trusted network separation — **adopt**, 4.6 Per-client latency — **cut**, 4.7 Retention and downsampling, 4. Metrics and features worth adding

### Community 65 - "9. Implementation conventions — read before writing code"
Cohesion: 0.25
Nodes (8): 9.1 Router-side Lua collectors, 9.2 Router-side helper scripts (POSIX sh), 9.3 `openwrt/setup.sh` integration, 9.4 Dashboard builders, 9.5 Alloy and compose, 9.6 Tests, 9.7 Naming rules, 9. Implementation conventions — read before writing code

### Community 66 - "SSH MCP sidecar"
Cohesion: 0.20
Nodes (10): Available tools, Claude Code, Codex, Configure and start, OpenCode, Router user, Security model, SSH known_hosts (required by default) (+2 more)

### Community 67 - "Repository map"
Cohesion: 0.25
Nodes (8): Dashboards, Documentation, Normally avoid by default, Optional MCP sidecar, Repository map, Router-side OpenWrt code, Runtime stack, Tests and validation

### Community 68 - "10. Metric contract"
Cohesion: 0.33
Nodes (6): 10.1 Phase 1 — identity (M1), 10.2 Phase 2 — topology (M5), 10.3 Phase 3 — traffic (M6, M7), 10.4 Phase 3, conditional — NetFlow (M11, gated on M10), 10.5 Labels that must never exist, 10. Metric contract

### Community 69 - "8. Risks and open questions"
Cohesion: 0.33
Nodes (6): 8. Risks and open questions, MAC randomisation — resolved with evidence, not deferred, Open question 1: where does the clients table live? — **resolved**, Open question 2: OUI vendor lookup on-router or in Grafana? — **resolved by dropping it**, Risks, Still open, honestly

### Community 70 - "CODE-REVIEW-FINDINGS.md"
Cohesion: 0.33
Nodes (5): How to read this document, OpenWrt Grafana Monitor — Code Review Findings, Part 1 — Router side (`openwrt/`), Part 7 — Summary / prioritisation map, Part 8 — Notes for the implementing agent

### Community 71 - "1. Phase 1 — Unified client inventory"
Cohesion: 0.40
Nodes (5): 1.1 New collector: `openwrt/collectors/client_inventory.lua`, 1.2 Multi-AP collection, 1.3 The table panel, 1.4 Cardinality, 1. Phase 1 — Unified client inventory

### Community 72 - "2. Phase 2 — Node graph"
Cohesion: 0.40
Nodes (5): 2.1 Required data shape, 2.2 Getting those frames out of Prometheus — **the first draft was wrong**, 2.3 Topology model, 2.4 Deliverable, 2. Phase 2 — Node graph

### Community 73 - "Dashboard previews"
Cohesion: 0.23
Nodes (9): config_text(), promoted_labels(), Guards the Loki syslog stream-label contract in alloy/config.alloy.  Two failure, Stream labels the syslog relabel block creates via target_label., A labelmap over __syslog_(.+) promotes the PID -- see R5., Load bearing for multi-router setups (ROUTER_TARGETS)., The regression that R5's own prescribed snippet would have caused.          If a, strip_comments() (+1 more)

### Community 74 - "0. Where the repo is today"
Cohesion: 0.50
Nodes (4): 0.1 Client identity is spread across six metric families, 0.2 What the stack looks like, 0.3 Phase 0: `node_nat_traffic` is already a cardinality bomb, 0. Where the repo is today

### Community 75 - "opencode.json"
Cohesion: 0.50
Nodes (3): plugin, $schema, .opencode/plugins/graphify.js

### Community 82 - "Manual Setup"
Cohesion: 0.29
Nodes (7): 1. Install exporter packages, 2. Configure the exporter to listen on LAN, 3. Copy the bundled collectors and scripts, 4. Add the helper cron jobs, 5. Configure remote syslog, 6. Restart services, Manual Setup

### Community 84 - "test_mcp_policy.py"
Cohesion: 0.16
Nodes (6): build_diagnostic_command(), build_metrics_sample_command(), build_monitoring_status_command(), build_restart_monitoring_command(), build_test_log_command(), CommandPolicyTests

### Community 85 - "server.py"
Cohesion: 0.13
Nodes (16): HostKeyPolicy, SSH host-key verification policy for the MCP sidecar.      ``policy`` is the par, Resolve the setup command timeout from an env-style string., redact(), resolve_setup_timeout_seconds(), _build_openwrt_tar(), _csv_set(), _int_env() (+8 more)

### Community 86 - "core.py"
Cohesion: 0.23
Nodes (16): build_configure_syslog_command(), build_setup_command(), normalize_profile(), _parse_host_port(), parse_router_inventory(), PolicyError, Policy and command construction for the OpenWrt SSH MCP server.  This module int, Parse `name=host[:port],name=host[:port]` into an allowlist. (+8 more)

### Community 87 - "resolve_host_key_policy"
Cohesion: 0.21
Nodes (7): _env_flag(), host_key_failure_message(), Resolve host-key policy from config values (not env lookup).      Default is str, Actionable error when SSH host-key verification fails., resolve_host_key_policy(), HostKeyPolicyTests, R11: default RejectPolicy; AutoAddPolicy only via explicit insecure flag.

### Community 88 - "._connect"
Cohesion: 0.29
Nodes (7): check(), ids(), import(), query(), scalar(), test_topology_promql.sh script, stat_of()

### Community 90 - "build_oui_table.py"
Cohesion: 0.33
Nodes (9): base36(), build(), classify(), load_rows(), lua_string(), main(), Path, render() (+1 more)

### Community 91 - "SecurityMiddleware"
Cohesion: 0.29
Nodes (6): BaseHTTPMiddleware, healthz(), SecurityMiddleware, PlainTextResponse, Request, Response

### Community 92 - "oui.lua"
Cohesion: 0.52
Nodes (6): hex_only(), is_local(), local_range(), M.lookup(), search(), vendor_at()

## Knowledge Gaps
- **394 isolated node(s):** `$schema`, `.opencode/plugins/graphify.js`, `openwrt-monitor-device-status.sh script`, `openwrt-monitor-dhcp-pool.sh script`, `openwrt-monitor-filesystem.sh script` (+389 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **27 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Part 1 — Router side (`openwrt/`)` connect `CODE-REVIEW-FINDINGS.md` to `1.3 `openwrt/scripts/``, `1.1 `openwrt/setup.sh``, `1.2 `openwrt/collectors/``?**
  _High betweenness centrality (0.011) - this node is a cross-community bridge._
- **Why does `Part 4 — Docs` connect `4.1 `docs/openwrt-setup.md`` to `CODE-REVIEW-FINDINGS.md`?**
  _High betweenness centrality (0.009) - this node is a cross-community bridge._
- **Why does `Part 2 — Monitoring host (`alloy/`, `docker-compose.yml`, `.env.example`)` connect `2.2 `docker-compose.yml`` to `CODE-REVIEW-FINDINGS.md`?**
  _High betweenness centrality (0.007) - this node is a cross-community bridge._
- **Are the 6 inferred relationships involving `PolicyError` (e.g. with `SecurityMiddleware` and `Settings`) actually correct?**
  _`PolicyError` has 6 INFERRED edges - model-reasoned connections that need verification._
- **What connects `$schema`, `.opencode/plugins/graphify.js`, `openwrt-monitor-device-status.sh script` to the rest of the system?**
  _394 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `MCP Server Core` be split into smaller, more focused modules?**
  _Cohesion score 0.13756613756613756 - nodes in this community are weakly interconnected._
- **Should `Advanced Dashboard` be split into smaller, more focused modules?**
  _Cohesion score 0.07815230961298376 - nodes in this community are weakly interconnected._