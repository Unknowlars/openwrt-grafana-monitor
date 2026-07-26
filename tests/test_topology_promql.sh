#!/bin/sh
# Runs the topology dashboard's *shipped* node-graph queries against a real
# PromQL engine, over exposition produced by the real collector for a simulated
# two-router network (one gateway, one dumb AP).
#
# This is the only test that covers the cross-router reconciliation, which
# lives in PromQL rather than in Lua and therefore cannot be reached by
# tests/test_topology.lua. It exists because every one of the bugs it asserts
# against was live on a real dashboard: an access point rendering as a laptop,
# a wifi client drawn as wired, two internet uplinks, and node ids colliding
# between exporters so that titles flickered between scrapes.
#
# The queries are read out of the generated dashboard JSON, not restated here,
# so the test fails if the generator stops emitting them.
#
#   sh tests/test_topology_promql.sh
#
# Requires docker (pulls victoriametrics/victoria-metrics), lua5.1, python3,
# curl and jq. Skips loudly rather than failing when any is unavailable.

set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

DASHBOARD="grafana-dashboard-exports/openwrt-topology-v2.json"
IMAGE="victoriametrics/victoria-metrics:latest"
CONTAINER="openwrt-topology-promql-test-$$"

for tool in docker lua5.1 python3 curl jq; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "  SKIPPED: $tool is not on PATH"
    exit 0
  fi
done
if ! docker info >/dev/null 2>&1; then
  echo "  SKIPPED: docker is installed but not usable"
  exit 0
fi
if [ ! -f "$DASHBOARD" ]; then
  echo "  SKIPPED: $DASHBOARD not generated yet"
  exit 0
fi

WORK=$(mktemp -d)
cleanup() {
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT INT TERM

failures=0
check() {
  # check <description> <expected> <actual>
  if [ "$2" = "$3" ]; then
    printf '    ok   %s\n' "$1"
  else
    printf '    FAIL %s (expected %s, got %s)\n' "$1" "$2" "$3"
    failures=$((failures + 1))
  fi
}

echo "  generating exposition from the real collector..."
REPO="$ROOT" lua5.1 tests/topology_exposition.lua gateway > "$WORK/main.prom"
REPO="$ROOT" lua5.1 tests/topology_exposition.lua ap > "$WORK/ap1.prom"
python3 tests/check_exposition.py "$WORK/main.prom" >/dev/null
python3 tests/check_exposition.py "$WORK/ap1.prom" >/dev/null

echo "  starting $IMAGE..."
# -search.latencyOffset=0s: by default VictoriaMetrics evaluates instant
# queries at now-30s to tolerate scrape lag, which makes samples imported a
# moment ago invisible and every assertion below fail for the wrong reason.
docker run -d --name "$CONTAINER" -p 127.0.0.1:0:8428 "$IMAGE" \
  -search.latencyOffset=0s -search.disableCache >/dev/null
PORT=$(docker port "$CONTAINER" 8428/tcp | head -1 | sed 's/.*://')
VM="http://127.0.0.1:$PORT"
ready=0
i=0
while [ "$i" -lt 60 ]; do
  if curl -sf "$VM/health" >/dev/null 2>&1; then ready=1; break; fi
  i=$((i + 1))
  sleep 1
done
if [ "$ready" -ne 1 ]; then
  echo "  SKIPPED: VictoriaMetrics did not become ready"
  exit 0
fi

# Two points 120s apart so rate() has something to work with. 1.2 MB over 120s
# is 10 kB/s on the TV, 600 kB is 5 kB/s on the camera that lives on AP1.
NOW=$(date +%s)
T0=$(( (NOW - 120) * 1000 ))
T1=$(( NOW * 1000 ))
cat > "$WORK/traffic.prom" <<EOF
openwrt_device_info{device="192.168.0.42",mac="a4:83:e7:aa:bb:cc",ip="192.168.0.42",interface="br-lan"} 1 $T0
openwrt_device_info{device="192.168.0.42",mac="a4:83:e7:aa:bb:cc",ip="192.168.0.42",interface="br-lan"} 1 $T1
openwrt_device_traffic_bytes_total{device="192.168.0.42",direction="download"} 0 $T0
openwrt_device_traffic_bytes_total{device="192.168.0.42",direction="download"} 1200000 $T1
openwrt_device_info{device="192.168.0.122",mac="78:8c:b5:93:fb:a9",ip="192.168.0.122",interface="br-lan"} 1 $T0
openwrt_device_info{device="192.168.0.122",mac="78:8c:b5:93:fb:a9",ip="192.168.0.122",interface="br-lan"} 1 $T1
openwrt_device_traffic_bytes_total{device="192.168.0.122",direction="upload"} 0 $T0
openwrt_device_traffic_bytes_total{device="192.168.0.122",direction="upload"} 600000 $T1
EOF

import() {
  # import <file> <router> <instance>; timestamps default to now for the
  # topology frames, which is what makes them visible to an instant query.
  curl -sS -o /dev/null -X POST \
    "$VM/api/v1/import/prometheus?extra_label=job=openwrt&extra_label=router=$2&extra_label=instance=$3" \
    --data-binary "@$1"
}
import "$WORK/main.prom" openwrt-main 192.168.0.1:9100
import "$WORK/ap1.prom" openwrt-ap1 192.168.0.2:9100
import "$WORK/traffic.prom" openwrt-main 192.168.0.1:9100

# VictoriaMetrics caches rollup results including empty ones, and the imports
# above land after the cache was warmed by the readiness probe.
sleep 2
curl -sS -o /dev/null -X POST "$VM/internal/resetRollupResultCache"
sleep 2

expr_for() {
  # expr_for <panel-id> <refId>; pulls the shipped query and substitutes the
  # dashboard variables an instant query cannot resolve on its own.
  python3 - "$DASHBOARD" "$1" "$2" <<'PY'
import json, sys
dash, pid, ref = sys.argv[1], sys.argv[2], sys.argv[3]
spec = json.load(open(dash))["spec"]["elements"][f"panel-{pid}"]["spec"]
for q in spec["data"]["spec"]["queries"]:
    if q["spec"]["refId"] == ref:
        print(q["spec"]["query"]["spec"]["expr"].replace("$router", ".*").replace("$__rate_interval", "5m"))
        break
else:
    raise SystemExit(f"panel {pid} has no refId {ref}")
PY
}

query() {
  curl -sS -G "$VM/prometheus/api/v1/query" --data-urlencode "query=$1"
}

NODES=$(expr_for 100 nodes)
EDGES=$(expr_for 100 edges)

ids() {
  # ids <expr> <label>: sorted label values from a query result
  query "$1" | jq -r ".data.result[].metric.$2" | sort
}
scalar() {
  query "$1" | jq -r '.data.result[0].value[1] // "0"'
}
stat_of() {
  # stat_of <id> <label>: mainstat for one node/edge id
  query "$2" | jq -r --arg id "$1" '.data.result[] | select(.metric.id == $id) | .value[1]'
}

echo "  asserting the rendered graph..."

ids "$NODES" id > "$WORK/nodes.txt"
ids "$EDGES" id > "$WORK/edges.txt"
has() { grep -qx "$2" "$1" && echo yes || echo no; }

# The uplink spine exists exactly once, and only the gateway contributes it.
check "one internet node"                 1     "$(grep -cx 'internet' "$WORK/nodes.txt")"
check "one gateway node keyed by LAN ip"  1     "$(grep -cx 'router:192.168.0.1' "$WORK/nodes.txt")"
check "upstream modem is its own hop"     yes   "$(has "$WORK/nodes.txt" 'modem:192.168.2.1')"
check "no second wan edge from the ap"    1     "$(grep -c '^wan:' "$WORK/edges.txt")"
check "the ap uplinks to the gateway"     yes   "$(has "$WORK/edges.txt" 'uplink:openwrt-ap1')"

# Both APs and all four of their radios survive as distinct nodes.
check "both access points present"        2     "$(grep -c '^ap:' "$WORK/nodes.txt")"
check "four distinct bssids"              4     "$(grep -c '^bss:' "$WORK/nodes.txt")"

# The AP is not a client of the gateway, and its edge went with it.
check "ap mac is not a client node"       no    "$(has "$WORK/nodes.txt" 'client:60:cf:84:f5:89:90')"
check "ap mac has no lan edge"            no    "$(has "$WORK/edges.txt" 'lan:60:cf:84:f5:89:90')"

# The client that is really on AP1's radio keeps the gateway's identity but is
# drawn on the association, not on the gateway's wired guess.
check "wifi client on the other ap"       yes   "$(has "$WORK/edges.txt" 'assoc:78:8c:b5:93:fb:a9')"
check "its wired guess is suppressed"     no    "$(has "$WORK/edges.txt" 'lan:78:8c:b5:93:fb:a9')"
check "it keeps the gateway's hostname"   C100  "$(query "$NODES" | jq -r '.data.result[] | select(.metric.id=="client:78:8c:b5:93:fb:a9") | .metric.title')"

# Vendor identification replaces the old unknown_<hex> titles.
check "nameless client named by vendor"   "Raspberry Pi c0:5e:e2" \
  "$(query "$NODES" | jq -r '.data.result[] | select(.metric.id=="client:d8:3a:dd:c0:5e:e2") | .metric.title')"
check "modem named by vendor"             Sagemcom \
  "$(query "$NODES" | jq -r '.data.result[] | select(.metric.id=="modem:192.168.2.1") | .metric.title')"

# Live throughput reaches both frames, and follows the client onto the
# association edge rather than the suppressed wired one.
check "node mainstat is live B/s"         10000 "$(stat_of 'client:a4:83:e7:aa:bb:cc' "$NODES")"
check "silent client reads a real zero"   0     "$(stat_of 'client:aa:bb:cc:dd:ee:01' "$NODES")"
check "assoc edge carries throughput"     5000  "$(stat_of 'assoc:78:8c:b5:93:fb:a9' "$EDGES")"

# The invariants that decide whether the panel renders at all. These are the
# dashboard's own Data Quality panels, run as assertions.
check "no colliding node ids"             0     "$(scalar "$(expr_for 201 A)")"
check "no dangling edge sources"          0     "$(scalar "$(expr_for 202 A)")"
check "no dangling edge targets"          0     "$(scalar "$(expr_for 203 A)")"

if [ "$failures" -eq 0 ]; then
  echo "PASS: tests/test_topology_promql.sh ($(wc -l < "$WORK/nodes.txt" | tr -d ' ') nodes, $(wc -l < "$WORK/edges.txt" | tr -d ' ') edges rendered)"
  exit 0
fi
echo "$failures failure(s)"
exit 1
