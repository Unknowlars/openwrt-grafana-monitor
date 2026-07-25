#!/bin/sh
# Full validation set for this repository.
#
#   sh tests/run_all.sh                       offline checks only
#   ROUTER_METRICS_URL=http://192.168.0.1:9100/metrics sh tests/run_all.sh
#
# Supplying ROUTER_METRICS_URL additionally checks a live router's exposition
# for duplicate series, which Prometheus drops silently rather than reporting.

set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

echo "== python generators compile =="
python3 -m py_compile build_dashboards.py \
  build_openwrt_operations_dashboard.py \
  build_openwrt_advanced_dashboard.py \
  build_openwrt_clients_dashboard.py \
  build_openwrt_topology_dashboard.py \
  build_openwrt_mission_control.py \
  build_openwrt_netflow_dashboard.py

echo "== dashboards regenerate deterministically =="
python3 build_openwrt_advanced_dashboard.py
python3 build_openwrt_clients_dashboard.py
python3 build_openwrt_operations_dashboard.py
python3 build_openwrt_topology_dashboard.py
python3 build_openwrt_mission_control.py
python3 build_openwrt_netflow_dashboard.py

echo "== generated copies are byte-identical =="
cmp grafana-dashboard-exports/openwrt-advanced-v2.json \
    grafana/provisioning/dashboards/openwrt-advanced-v2.json
cmp grafana-dashboard-exports/openwrt-clients-v2.json \
    grafana/provisioning/dashboards/openwrt-clients-v2.json
cmp grafana-dashboard-exports/openwrt-operations-v2.json \
    grafana/provisioning/dashboards/openwrt-operations-v2.json
cmp grafana-dashboard-exports/openwrt-topology-v2.json \
    grafana/provisioning/dashboards/openwrt-topology-v2.json
cmp grafana-dashboard-exports/openwrt-mission-control.json \
    grafana/provisioning/dashboards/openwrt-mission-control.json
cmp grafana-dashboard-exports/openwrt-netflow-v2.json \
    grafana/provisioning/dashboards/openwrt-netflow-v2.json

echo "== shell syntax =="
sh -n openwrt/setup.sh openwrt/scripts/*.sh

echo "== installer upgrade paths =="
sh tests/test_setup_legacy_crontab.sh
sh tests/test_setup_nlbwmon_optional.sh

echo "== lua syntax =="
if command -v luac5.1 >/dev/null 2>&1; then
  for f in openwrt/collectors/*.lua openwrt/lua/*.lua; do luac5.1 -p "$f"; done
else
  echo "  SKIPPED: no luac5.1 on PATH (OpenWrt ships Lua 5.1)"
fi

echo "== collector behaviour =="
sh tests/test_sqm_collector.sh
sh tests/test_client_traffic.sh
sh tests/test_client_conntrack.sh
sh tests/test_inodes.sh
sh tests/test_wan_quality.sh
sh tests/test_wan_info.sh
sh tests/test_netflow_health.sh
if command -v lua5.1 >/dev/null 2>&1; then
  lua5.1 tests/test_device_traffic.lua
  lua5.1 tests/test_dpi_netifyd.lua
  lua5.1 tests/test_client_inventory.lua
  lua5.1 tests/test_topology.lua
else
  echo "  SKIPPED: no lua5.1 on PATH"
fi

echo "== dashboard node-graph queries against a real PromQL engine =="
# Covers the cross-router reconciliation, which lives in the dashboard's PromQL
# and so is out of reach of the Lua collector tests. Skips itself when docker is
# unavailable.
sh tests/test_topology_promql.sh

if [ -n "${ROUTER_METRICS_URL:-}" ]; then
  echo "== live router exposition =="
  python3 tests/check_exposition.py --url "$ROUTER_METRICS_URL"
fi

echo
echo "ALL CHECKS PASSED"
