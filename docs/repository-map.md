# Repository map

This map helps agents and contributors choose where to inspect first. It is not a
complete file inventory.

## Runtime stack

- `docker-compose.yml`: runs Grafana OTEL-LGTM, Grafana Alloy, and the optional
  `openwrt-ssh-mcp` profile.
- `alloy/config.alloy`: reads generated file-SD targets, scrapes OpenWrt metrics,
  drops unbounded NAT traffic, receives syslog, and forwards telemetry.
- `grafana/provisioning/datasources/`: provisioned Prometheus, Loki, and Tempo.
- `grafana/provisioning/alerting/`: provisioned alerting resources.

## Router-side OpenWrt code

- `openwrt/setup.sh`: installer and profile wiring. Keep POSIX shell and
  BusyBox compatibility.
- `openwrt/collectors/`: Lua 5.1 collectors loaded by
  `prometheus-node-exporter-lua`.
- `openwrt/scripts/`: textfile helper scripts run from cron. Helpers should
  stage files under `/tmp` and atomically `mv` into the textfile directory.
- `openwrt/nftables/`: optional traffic accounting rules.
- `openwrt/nlbwmon/`: protocol mapping used by client traffic collection.

## Dashboards

- `build_dashboards.py`: source for classic dashboards.
- `build_openwrt_operations_dashboard.py`: source for Operations v2.
- `build_openwrt_clients_dashboard.py`: source for Clients v2.
- `build_openwrt_advanced_dashboard.py`: source for Advanced v2.
- `build_openwrt_topology_dashboard.py`: source for Topology v2.
- `build_openwrt_mission_control.py`: source for Mission Control.
- `grafana/provisioning/dashboards/`: generated provisioned dashboard JSON.
- `grafana-dashboard-exports/`: generated manual-import copies for v2 dashboards.
- `grafana dashboards/`: preserved legacy/PR dashboard exports.

Edit generators first. Inspect generated JSON only to verify output pairs,
schema/import shape, or exact provisioning content.

## Optional MCP sidecar

- `mcp_server/core.py`: allowlist, command construction, policy boundaries.
- `mcp_server/server.py`: HTTP/MCP service implementation.
- `mcp_server/Dockerfile` and `mcp_server/requirements.txt`: pinned sidecar
  runtime dependencies.
- `docs/mcp-ssh.md`: setup, security model, and validation sequence.

MCP/router mutations require explicit authorization and `confirm=true`.

## Tests and validation

- `tests/run_all.sh`: broad offline validation gate.
- `tests/test_mcp_policy.py`: MCP policy/unit coverage.
- `tests/test_client_conntrack.sh`, `tests/test_client_traffic.sh`,
  `tests/test_sqm_collector.sh`: shell collector tests.
- `tests/test_*.lua`: Lua collector tests, run only when Lua 5.1 is available.
- `tests/fixtures/`: fixture inputs for collector tests.
- `tests/check_exposition.py`: optional authorized live duplicate-series check.

Common validation:

```sh
python3 -m unittest discover -s tests -p 'test_*.py'
sh -n openwrt/setup.sh openwrt/scripts/*.sh
docker compose config
sh tests/run_all.sh
```

## Documentation

- `README.md`: operator quick start and high-level project description.
- `docs/openwrt-setup.md`: router install details.
- `docs/advanced-profiles.md`: optional profile behavior.
- `docs/monitoring-host-setup.md`: Docker host setup.
- `docs/troubleshooting.md`: known runtime symptoms and fixes.
- `docs/client-topology-and-netflow-plan.md`: long historical implementation
  plan; read targeted sections only.
- `docs/CODE-REVIEW-FINDINGS.md`: long review backlog (2026-07-23, report-only);
  read targeted findings only.
- `docs/CODE-REVIEW-REMEDIATION-PLAN.md`: prescriptive fix plan and task list
  from the 2026-07-25 review (task IDs `R1`-`R13`, `M1`-`M2`). Complements, does
  not supersede, `CODE-REVIEW-FINDINGS.md`.

## Normally avoid by default

- `.env` and `.env.*`: private local configuration.
- `.agents/`, `.claude/`, `.codex/`, `skills/`, `grafana_docs/`: ignored local
  agent/tooling/reference state.
- Generated dashboard JSON, screenshots, and Python caches unless directly
  relevant.
