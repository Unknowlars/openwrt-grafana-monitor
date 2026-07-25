# Graph Report - .  (2026-07-25)

## Corpus Check
- 95 files · ~320,911 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 396 nodes · 1017 edges · 41 communities (21 shown, 20 thin omitted)
- Extraction: 99% EXTRACTED · 1% INFERRED · 0% AMBIGUOUS · INFERRED: 8 edges (avg confidence: 0.5)
- Token cost: 0 input · 0 output

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

## God Nodes (most connected - your core abstractions)
1. `build_dashboard()` - 28 edges
2. `PolicyError` - 26 edges
3. `prom_query()` - 23 edges
4. `build_dashboard()` - 22 edges
5. `DashboardBuilder` - 22 edges
6. `tf()` - 19 edges
7. `tab_lan()` - 18 edges
8. `deep()` - 18 edges
9. `CommandSpec` - 18 edges
10. `tab_wifi()` - 17 edges

## Surprising Connections (you probably didn't know these)
- `build_dashboard()` --calls--> `DashboardBuilder`  [EXTRACTED]
  build_openwrt_advanced_dashboard.py → build_openwrt_operations_dashboard.py
- `build_dashboard()` --calls--> `prom_query()`  [EXTRACTED]
  build_openwrt_advanced_dashboard.py → build_openwrt_operations_dashboard.py
- `build_dashboard()` --calls--> `DashboardBuilder`  [EXTRACTED]
  build_openwrt_clients_dashboard.py → build_openwrt_operations_dashboard.py
- `build_dashboard()` --calls--> `prom_query()`  [EXTRACTED]
  build_openwrt_clients_dashboard.py → build_openwrt_operations_dashboard.py
- `loki_stat()` --calls--> `loki_query()`  [EXTRACTED]
  build_openwrt_mission_control.py → build_openwrt_operations_dashboard.py

## Import Cycles
- None detected.

## Communities (41 total, 20 thin omitted)

### Community 0 - "MCP Server Core"
Cohesion: 0.06
Nodes (65): BaseHTTPMiddleware, build_configure_syslog_command(), build_diagnostic_command(), build_metrics_sample_command(), build_monitoring_status_command(), build_restart_monitoring_command(), build_setup_command(), build_system_facts_command() (+57 more)

### Community 1 - "Advanced Dashboard"
Cohesion: 0.09
Nodes (71): availability(), build_dashboard(), iter_strings(), layout_refs(), main(), Any, Profile health tile.      A collector's own availability flag is not sufficient, validate_dashboard() (+63 more)

### Community 2 - "Mission Control Dashboard"
Cohesion: 0.19
Nodes (46): bargauge(), build_dashboard(), clean(), color_override(), data_group(), gauge(), grid_items_by_tab(), histogram() (+38 more)

### Community 3 - "Lua Collectors"
Cohesion: 0.22
Nodes (18): arp_by_ip(), assoc_by_mac(), collect(), config_value(), flow_offload_state(), is_locally_administered(), lease_expiry_by_mac(), leasefile_hosts() (+10 more)

### Community 4 - "Legacy Dashboards"
Cohesion: 0.32
Nodes (16): bargauge(), build_devices(), build_logs(), build_network(), build_overview(), loki_logs(), loki_stat(), loki_tgt() (+8 more)

### Community 5 - "Topology Collection"
Cohesion: 0.33
Nodes (11): add_edge(), add_node(), assoc_by_mac(), collect(), leasefile_hosts(), normalize_mac(), reachable_ips(), router_hostname() (+3 more)

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
Cohesion: 0.50
Nodes (7): emit_assoc_events(), emit_conntrack(), fail_closed(), headers(), openwrt-monitor-client-conntrack.sh script, valid_ipv4(), valid_mac()

### Community 11 - "Client Traffic Monitoring"
Cohesion: 0.52
Nodes (5): fail_closed(), openwrt-monitor-client-traffic.sh script, valid_mac(), valid_number(), write_headers()

### Community 12 - "WAN Quality Monitoring"
Cohesion: 0.38
Nodes (3): openwrt-monitor-wan-quality.sh script, write_dns_probe(), write_probe()

### Community 15 - "Metrics Exposition"
Cohesion: 0.60
Nodes (4): find_duplicates(), main(), parse(), Return (name, normalised-labels, value) for every sample line.

## Knowledge Gaps
- **16 isolated node(s):** `openwrt-monitor-device-status.sh script`, `openwrt-monitor-dhcp-pool.sh script`, `openwrt-monitor-filesystem.sh script`, `openwrt-monitor-inodes.sh script`, `openwrt-monitor-ipv6-health.sh script` (+11 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **20 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `DashboardBuilder` connect `Mission Control Dashboard` to `Advanced Dashboard`?**
  _High betweenness centrality (0.009) - this node is a cross-community bridge._
- **Why does `prom_query()` connect `Mission Control Dashboard` to `Advanced Dashboard`?**
  _High betweenness centrality (0.007) - this node is a cross-community bridge._
- **Are the 5 inferred relationships involving `PolicyError` (e.g. with `SecurityMiddleware` and `Settings`) actually correct?**
  _`PolicyError` has 5 INFERRED edges - model-reasoned connections that need verification._
- **What connects `openwrt-monitor-device-status.sh script`, `openwrt-monitor-dhcp-pool.sh script`, `openwrt-monitor-filesystem.sh script` to the rest of the system?**
  _16 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `MCP Server Core` be split into smaller, more focused modules?**
  _Cohesion score 0.06046511627906977 - nodes in this community are weakly interconnected._
- **Should `Advanced Dashboard` be split into smaller, more focused modules?**
  _Cohesion score 0.09330143540669857 - nodes in this community are weakly interconnected._