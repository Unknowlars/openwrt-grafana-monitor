#!/usr/bin/env python3
"""Build the Grafana v2beta1 network topology dashboard for OpenWrt.

The generated JSON files are artifacts. This script is the source of truth.
It writes both the manual import export and the provisioned dashboard copy.

The node graph panel is driven directly by the Prometheus datasource
using instant table queries with refId="nodes"/"edges" -- no Infinity plugin,
no GF_INSTALL_PLUGINS, confirmed viable against the bundled Grafana setup.

MULTI-ROUTER RECONCILIATION

Several exporters feed one graph, so the queries below do three things the
collector cannot do on its own, because no single router can see the whole
network:

  1. Prefer first-hand facts. Every node and edge carries `authority`: "1"
     when the emitting box observed it directly, "0" when it is a placeholder
     that exists only so an edge endpoint resolves. `prefer_authority()` keeps
     the authority-1 series and falls back to authority-0 for ids nothing
     claims first-hand, which is what lets a client keep the hostname only the
     gateway knows while the AP it is associated to still draws the link.
  2. Drop routers that appear as each other's clients. A dumb AP is a DHCP
     client of the gateway, so it used to render as a laptop hanging off it.
     `openwrt_topology_infra_mac` lists each box's own interface MACs; the
     matching client node *and every edge pointing at it* are suppressed.
     Dropping the node alone would leave a dangling target, which crashes the
     panel rather than rendering incompletely.
  3. Resolve the wired-vs-wifi conflict. The gateway cannot see another AP's
     association list, so it classifies that AP's wireless clients as wired
     and emits a `lan:` edge for them. Whenever some AP claims the same MAC
     with an `assoc:` edge, the gateway's `lan:` edge is dropped.

Node-value choice: the transformation pipeline applies a
single `organize` rename uniformly to every frame it touches by field name,
not by refId, so there is no way to rename `Value` -> `mainstat` on the edges
frame only while leaving the nodes frame's `Value` field alone within one
panel's transformation list. This builder therefore renames `Value` on both
frames and makes both frames carry the same quantity -- current throughput in
bytes/sec -- so one `organize` and one unit are correct for both.

`secondarystat`, `thickness` and `highlighted` are still not populated, for
the same reason: one Prometheus table query yields exactly one numeric column,
and a `joinByField` to add a second would merge the nodes and edges frames
into one and corrupt both. Wi-Fi link quality is therefore carried as the
association edge's `color`, which the collector computes from the signal it
already reads -- and which is arguably where it belongs, since signal is a
property of the link and not of the device.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .build_openwrt_operations_dashboard import (
    AVAILABILITY_MAPPINGS,
    GRAY,
    GREEN,
    THRESHOLDS,
    YELLOW,
    DashboardBuilder,
    datasource_var,
    iter_strings,
    organize,
    panel,
    prom_query,
    query_var,
    sort_by,
    stable_json,
    stat,
    table,
    text,
)


ROOT = Path(__file__).resolve().parents[1]
OUTS = [
    ROOT / "grafana-dashboard-exports/openwrt-topology-v2.json",
    ROOT / "grafana/provisioning/dashboards/openwrt-topology-v2.json",
]

PROM_DS = "${DS_PROMETHEUS}"
PROM_FILTER = 'job="openwrt", router=~"$router"'

# Dashboards are addressed by their metadata.name once provisioned.
CLIENTS_DASHBOARD = "openwrt-clients"
MISSION_CONTROL_DASHBOARD = "openwrt-mission-control"

NODE_METRIC = "openwrt_topology_node"
EDGE_METRIC = "openwrt_topology_edge"
INFRA_METRIC = "openwrt_topology_infra_mac"

# Common to both node-graph frames: strip Prometheus/scrape plumbing columns
# that mean nothing to the node graph panel and would otherwise show up as
# stray fields. `Value` is renamed, not dropped -- see module docstring.
# `authority` is reconciliation bookkeeping and must not reach the panel.
NOISE_COLUMNS = [
    "Time",
    "__name__",
    "authority",
    "job",
    "instance",
    "router",
    "cluster",
    "endpoint",
    "namespace",
    "prometheus",
    "prometheus_replica",
    "service",
]


def field_override(name: str, properties: list[dict[str, Any]]) -> dict[str, Any]:
    return {"matcher": {"id": "byName", "options": name}, "properties": properties}


def selector(metric: str, extra: str = "") -> str:
    return f"{metric}{{{PROM_FILTER}{', ' + extra if extra else ''}}}"


def prefer_authority(metric: str) -> str:
    """Keep first-hand series, fall back to placeholders for unclaimed ids.

    `A or (B unless on(id) A)` is the whole cross-router dedup: series that
    some box observed directly win, and a placeholder survives only when no
    box claims that id first-hand -- which is exactly the case where the
    authoritative exporter is unreachable and the placeholder is all there is.
    """
    first = selector(metric, 'authority="1"')
    fallback = selector(metric, 'authority="0"')
    return f"({first} or ({fallback} unless on(id) {first}))"


def infra_as(label: str) -> str:
    """The infra MAC list, reshaped so it can be matched against `label`."""
    return f'label_replace({selector(INFRA_METRIC)}, "{label}", "client:$1", "mac", "(.+)")'


# Per-client throughput, keyed by MAC. openwrt_device_traffic_bytes_total is
# keyed by `device` (the nftables set element), so identity comes from
# openwrt_device_info. Only routers running the traffic profile have it; on the
# others this simply yields nothing and the `or` below restores a truthful 0.
TRAFFIC_BY_MAC = (
    f"sum by (mac) (rate({selector('openwrt_device_traffic_bytes_total')}[$__rate_interval]) "
    f"* on(device) group_left(mac) {selector('openwrt_device_info')})"
)


def traffic_as(prefix: str) -> str:
    return f'sum by (id) (label_replace({TRAFFIC_BY_MAC}, "id", "{prefix}:$1", "mac", "(.+)"))'


def overlay_traffic(base: str, traffic: str) -> str:
    """Replace a frame's presence value with live throughput.

    `base * 0` zeroes the presence value, `+ on(id) group_left()` overlays
    throughput for the ids that have any, and the trailing `or` puts back every
    node/edge that had no traffic -- at a truthful 0 B/s rather than vanishing.
    """
    zeroed = f"(({base}) * 0)"
    return f"(({zeroed} + on(id) group_left() ({traffic})) or {zeroed})"


def nodes_expr() -> str:
    reconciled = f"({prefer_authority(NODE_METRIC)} unless on(id) {infra_as('id')})"
    return overlay_traffic(reconciled, traffic_as("client"))


def edges_expr() -> str:
    reconciled = prefer_authority(EDGE_METRIC)
    # A `lan:` edge is the gateway's best guess for a MAC it cannot see on its
    # own radios. If any AP reports an association for that MAC, the guess is
    # wrong and the real link is the assoc edge.
    assoc_as_lan = (
        f'label_replace({selector(EDGE_METRIC, 'id=~"assoc:.+"')}, "id", "lan:$1", "id", "assoc:(.+)")'
    )
    reconciled = f"({reconciled} unless on(id) {assoc_as_lan})"
    # Suppressing an infra client node without also suppressing the edges that
    # point at it would leave a dangling target and crash the panel.
    reconciled = f"({reconciled} unless on(target) {infra_as('target')})"
    return overlay_traffic(reconciled, f"({traffic_as('lan')} or {traffic_as('assoc')})")


NODES_EXPR = nodes_expr()
EDGES_EXPR = edges_expr()

# Raw, un-reconciled series for the Data Quality tab: the point of those panels
# is to show what the exporters actually emitted, including anything the
# reconciliation above is hiding.
NODES_RAW = selector(NODE_METRIC)
EDGES_RAW = selector(EDGE_METRIC)


def availability_expr(metric_name: str, collector: str = "") -> str:
    if collector:
        return (
            f"(max({selector(metric_name)}) "
            f'* max(node_scrape_collector_success{{{PROM_FILTER}, collector="{collector}"}})) or vector(0)'
        )
    return f"max({selector(metric_name)}) or vector(0)"


def count_nodes(extra: str) -> str:
    """count() over first-hand node series matching an extra label selector."""
    return "count(" + selector(NODE_METRIC, extra + ', authority="1"') + ") or vector(0)"


# Data links turn the graph into a jumping-off point instead of a picture.
# They hang off the `id` field, which every node has, and appear in the node's
# context menu.
NODE_LINKS = [
    {
        "title": "Client detail for this device",
        "url": (
            f"/d/{CLIENTS_DASHBOARD}/{CLIENTS_DASHBOARD}"
            "?var-router=$router&var-mac=${__data.fields.detail__mac}&${__url_time_range}"
        ),
        "targetBlank": False,
    },
    {
        "title": "Mission Control for this router",
        "url": (
            f"/d/{MISSION_CONTROL_DASHBOARD}/{MISSION_CONTROL_DASHBOARD}"
            "?var-router=$router&${__url_time_range}"
        ),
        "targetBlank": False,
    },
]

# Declaring `arcs` overrides automatic arc__* detection, so every arc field the
# exporter emits is listed here and given a deliberate semantic colour instead
# of a palette colour. Offline is grey rather than red: a phone that left the
# house is not a fault. Fields absent on a given node simply do not draw.
NODE_ARCS = [
    {"field": "arc__online", "color": GREEN},
    {"field": "arc__offline", "color": GRAY},
    {"field": "arc__ok", "color": GREEN},
    {"field": "arc__warn", "color": YELLOW},
]


def nodegraph(pid: int, title: str, desc: str) -> tuple[str, dict[str, Any]]:
    return panel(
        pid,
        title,
        "nodeGraph",
        [
            prom_query(EDGES_EXPR, ref="edges", fmt="table", instant=True),
            prom_query(NODES_EXPR, ref="nodes", fmt="table", instant=True),
        ],
        "Bps",
        desc,
        options={
            "zoomMode": "cooperative",
            # `layered` is what makes the hierarchy readable at all; the
            # default force layout renders this graph as crossing spaghetti.
            # It is documented as slow above ~500 nodes, which is far beyond
            # this deployment's ~40.
            "layoutAlgorithm": "layered",
            "nodes": {"mainStatUnit": "Bps", "arcs": NODE_ARCS},
            "edges": {"mainStatUnit": "Bps"},
        },
        field_defaults={"thresholds": THRESHOLDS["neutral"]},
        overrides=[field_override("id", [{"id": "links", "value": NODE_LINKS}])],
        transformations=[organize(exclude=list(NOISE_COLUMNS), rename={"Value": "mainstat"})],
    )


def variables() -> list[dict[str, Any]]:
    return [
        datasource_var("DS_PROMETHEUS", "Prometheus", "prometheus", "prometheus"),
        query_var("router", "Router", 'label_values(node_load1{job="openwrt"}, router)', "openwrt", include_all=True, multi=True),
    ]


def build_dashboard() -> dict[str, Any]:
    b = DashboardBuilder()
    tabs: list[dict[str, Any]] = []

    overview: list[dict[str, Any]] = []
    b.add(overview, text(1, "", "## Network topology\ninternet -> modem -> gateway -> switch port / AP -> BSSID -> client, built from the same identity data as the clients dashboard. Node and edge frames come from the same scrape (topology.lua), so a client that disappears never leaves a dangling edge. Every exporter contributes only what it can see first-hand: a downstream AP never invents an uplink, and only the gateway names clients, because only the gateway runs DHCP."), 0, 0, 24, 3)
    b.add(overview, stat(2, "Topology Collector", availability_expr("openwrt_topology_collector_available", "topology"), "none", "Collector availability gated by both the topology flag and the Lua exporter's per-collector scrape success.", mappings=AVAILABILITY_MAPPINGS, thresholds_key="unavailable", color_mode="value"), 0, 3, 6, 4)
    b.add(overview, stat(3, "Nodes", f"count({NODES_EXPR}) or vector(0)", "none", "Nodes actually rendered, after cross-router reconciliation: placeholder duplicates and routers appearing as each other's clients are already removed.", graph=True, color_mode="value"), 6, 3, 6, 4)
    b.add(overview, stat(4, "Edges", f"count({EDGES_EXPR}) or vector(0)", "none", "Edges actually rendered (wan/nat/ap/uplink/link/radio/assoc/lan), after the wired-vs-wifi conflict is resolved.", graph=True, color_mode="value"), 12, 3, 6, 4)
    b.add(overview, stat(5, "Online Clients", f'count({selector(NODE_METRIC, 'id=~"client:.+", arc__online="1", authority="1"')}) or vector(0)', "none", "Client nodes currently reporting arc__online=1, counted from first-hand series only so a placeholder never double-counts.", graph=True, color_mode="value"), 18, 3, 6, 4)
    b.add(overview, stat(6, "Access Points", count_nodes('id=~"ap:.+"'), "none", "Boxes broadcasting at least one BSS, including the gateway when it also serves wifi.", graph=True, color_mode="value"), 0, 7, 6, 4)
    b.add(overview, stat(7, "Broadcast BSSIDs", count_nodes('id=~"bss:.+"'), "none", "Distinct basic service sets. Two APs broadcasting one SSID are two BSSIDs on two channels, which is why they are separate nodes.", graph=True, color_mode="value"), 6, 7, 6, 4)
    weak_links = "count(" + selector(EDGE_METRIC, 'id=~"assoc:.+", color="red"') + ") or vector(0)"
    b.add(overview, stat(8, "Weak Wi-Fi Links", weak_links, "none", "Associations below -72 dBm. The collector colours each association edge from the signal it reads in the same scrape, so this counts exactly what the graph draws red.", graph=True, color_mode="value", thresholds_key="ok_bad"), 12, 7, 6, 4)
    b.add(overview, stat(9, "Offline Clients", f'count({selector(NODE_METRIC, 'id=~"client:.+", arc__offline="1", authority="1"')}) or vector(0)', "none", "Known clients not currently reachable. Grey rather than red on the graph: a device that has gone home is not a fault.", graph=True, color_mode="value"), 18, 7, 6, 4)
    tabs.append(b.tab("Overview", overview))

    topology: list[dict[str, Any]] = []
    b.add(topology, nodegraph(100, "Network Topology", "The live network graph. Node and edge values are current throughput in bytes/sec, joined from the per-device traffic counters onto the topology frames; anything with no traffic reads a truthful 0 B/s rather than disappearing, and routers without the traffic profile contribute a real 0 rather than a gap. The ring around each node is its health arc: green online/ok, grey offline, yellow warning. Association edges are coloured by signal - green above -60 dBm, orange to -72, red below - and a wired client sits under the switch port its MAC was learned on. Click any node for its client or router dashboard."), 0, 0, 24, 20)
    b.add(topology, text(101, "", "**Reading the graph.** `internet` -> `modem:` (only when the WAN nexthop is a private address, i.e. double NAT) -> `router:<lan-ip>` -> `port:` switch ports and `ap:` access points -> `bss:<bssid>` -> `client:<mac>`. Nodes are keyed so that every router names the same thing identically: the gateway by its LAN IP, each radio by its BSSID. An isolated client node means its connection state is genuinely unknown (no association list available) - the collector never guesses a wired link."), 0, 20, 24, 4)
    tabs.append(b.tab("Topology", topology))

    quality: list[dict[str, Any]] = []
    b.add(quality, text(200, "", "## Data quality\nRaw, un-reconciled node and edge series -- what the exporters actually emitted, including anything the Topology tab is hiding. The three checks below are the invariants that decide whether the panel renders at all: a dangling edge endpoint crashes Grafana's node graph rather than degrading, and two boxes claiming the same id first-hand means one of them silently overwrites the other."), 0, 0, 24, 4)
    b.add(quality, stat(201, "Colliding Node IDs", f"count(count by (id) ({selector(NODE_METRIC, 'authority=\"1\"')}) > 1) or vector(0)", "none", "Node ids claimed first-hand by more than one router. Must be zero: Grafana keys nodes by id and silently keeps whichever row arrives last. Two gateways on one LAN is the realistic way to trip this.", thresholds_key="ok_bad", color_mode="value"), 0, 4, 8, 4)
    b.add(quality, stat(202, "Dangling Edge Sources", f'count({EDGES_EXPR} unless on(source) label_replace({NODES_EXPR}, "source", "$1", "id", "(.+)")) or vector(0)', "none", "Rendered edges whose source resolves to no rendered node. Must be zero.", thresholds_key="ok_bad", color_mode="value"), 8, 4, 8, 4)
    b.add(quality, stat(203, "Dangling Edge Targets", f'count({EDGES_EXPR} unless on(target) label_replace({NODES_EXPR}, "target", "$1", "id", "(.+)")) or vector(0)', "none", "Rendered edges whose target resolves to no rendered node. Must be zero.", thresholds_key="ok_bad", color_mode="value"), 16, 4, 8, 4)
    b.add(quality, table(204, "Topology Nodes", [
        prom_query(NODES_RAW, "", "A", fmt="table", instant=True),
    ], "One row per emitted openwrt_topology_node series, before reconciliation. `authority` shows which box observed the node first-hand; duplicate ids across routers are expected here and resolved on the Topology tab.", transformations=[
        organize(exclude=["Time", "__name__", "cluster", "endpoint", "namespace", "prometheus", "prometheus_replica", "service"]),
        sort_by("id", desc=False),
    ], sort_col="id", sort_desc=False), 0, 8, 24, 9)
    b.add(quality, table(205, "Topology Edges", [
        prom_query(EDGES_RAW, "", "A", fmt="table", instant=True),
    ], "One row per emitted openwrt_topology_edge series, before reconciliation. Every source/target must match an id in the nodes table above.", transformations=[
        organize(exclude=["Time", "__name__", "cluster", "endpoint", "namespace", "prometheus", "prometheus_replica", "service"]),
        sort_by("id", desc=False),
    ], sort_col="id", sort_desc=False), 0, 17, 24, 9)
    b.add(quality, table(206, "Infrastructure MACs", [
        prom_query(selector(INFRA_METRIC), "", "A", fmt="table", instant=True),
    ], "Each exporter's own interface MACs. These are why an access point no longer renders as a laptop hanging off the gateway: the matching client node and every edge pointing at it are suppressed on the Topology tab.", transformations=[
        organize(exclude=["Time", "__name__", "cluster", "endpoint", "namespace", "prometheus", "prometheus_replica", "service"]),
        sort_by("mac", desc=False),
    ], sort_col="mac", sort_desc=False), 0, 26, 24, 8)
    tabs.append(b.tab("Data Quality", quality))

    spec: dict[str, Any] = {
        "title": "OpenWrt - Topology",
        "description": "Network topology node graph for OpenWrt: internet, modem, gateway, switch ports, access points, BSSIDs, and client identity, driven by the Prometheus datasource with no plugin install.",
        "tags": ["openwrt", "topology", "router"],
        "cursorSync": "Crosshair",
        "editable": True,
        "preload": False,
        "liveNow": False,
        "annotations": [
            {
                "kind": "AnnotationQuery",
                "spec": {
                    "builtIn": True,
                    "enable": True,
                    "hide": True,
                    "iconColor": "rgba(0, 211, 255, 1)",
                    "name": "Annotations & Alerts",
                    "query": {"kind": "DataQuery", "group": "grafana", "version": "v0", "spec": {}},
                },
            }
        ],
        "links": [],
        "timeSettings": {
            "timezone": "browser",
            "from": "now-3h",
            "to": "now",
            "autoRefresh": "30s",
            "autoRefreshIntervals": ["10s", "30s", "1m", "5m", "15m"],
            "hideTimepicker": False,
            "fiscalYearStartMonth": 0,
        },
        "variables": variables(),
        "elements": b.elements,
        "layout": {"kind": "TabsLayout", "spec": {"tabs": tabs}},
    }
    dashboard = {
        "apiVersion": "dashboard.grafana.app/v2beta1",
        "kind": "Dashboard",
        "metadata": {"name": "openwrt-topology"},
        "spec": spec,
    }
    validate_dashboard(dashboard)
    return dashboard


def layout_refs(layout: Any) -> list[str]:
    refs: list[str] = []
    if isinstance(layout, dict):
        if layout.get("kind") == "ElementReference" and "name" in layout:
            refs.append(layout["name"])
        for value in layout.values():
            refs.extend(layout_refs(value))
    elif isinstance(layout, list):
        for value in layout:
            refs.extend(layout_refs(value))
    return refs


def grid_items_by_tab(layout: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for tab in layout["spec"]["tabs"]:
        title = tab["spec"]["title"]
        items: list[dict[str, Any]] = []
        for row in tab["spec"]["layout"]["spec"]["rows"]:
            items.extend(row["spec"]["layout"]["spec"]["items"])
        result[title] = items
    return result


def validate_dashboard(dash: dict[str, Any]) -> None:
    assert dash["apiVersion"] == "dashboard.grafana.app/v2beta1"
    assert dash["kind"] == "Dashboard"
    assert dash["metadata"]["name"] == "openwrt-topology"
    spec = dash["spec"]
    assert spec["title"] == "OpenWrt - Topology"
    assert spec["layout"]["kind"] == "TabsLayout"
    assert [tab["spec"]["title"] for tab in spec["layout"]["spec"]["tabs"]] == [
        "Overview",
        "Topology",
        "Data Quality",
    ]

    elements = spec["elements"]
    refs = layout_refs(spec["layout"])
    assert set(refs) == set(elements), f"orphan/missing refs: refs={len(refs)} elements={len(elements)}"
    duplicate_refs = sorted({ref for ref in refs if refs.count(ref) > 1})
    assert not duplicate_refs, f"duplicate layout refs: {duplicate_refs}"

    panel_ids: list[int] = []
    node_graph_seen = False
    for key, element in elements.items():
        assert key.startswith("panel-")
        panel_spec = element["spec"]
        panel_ids.append(panel_spec["id"])
        assert key == f"panel-{panel_spec['id']}"
        viz = panel_spec["vizConfig"]["group"]
        if viz != "text":
            defaults = panel_spec["vizConfig"]["spec"]["fieldConfig"]["defaults"]
            assert "unit" in defaults, f"missing unit: {key} {panel_spec['title']}"
            assert defaults["unit"] != "short", f"generic short unit: {key} {panel_spec['title']}"
        # noValue belongs in fieldConfig.defaults, where Grafana reads it.
        assert "noValue" not in panel_spec["vizConfig"]["spec"]["options"], f"noValue in panel options: {key}"
        assert "pluginVersion" not in json.dumps(element)
        if viz == "nodeGraph":
            node_graph_seen = True
            queries = panel_spec["data"]["spec"]["queries"]
            ref_ids = {q["spec"]["refId"] for q in queries}
            assert ref_ids == {"nodes", "edges"}, f"node graph must use refId nodes/edges: {ref_ids}"
            for q in queries:
                qspec = q["spec"]["query"]["spec"]
                assert qspec.get("format") == "table", "node graph queries must be format=table"
                assert qspec.get("instant") is True, "node graph queries must be instant"
            options = panel_spec["vizConfig"]["spec"]["options"]
            # Declaring `arcs` overrides automatic arc__* detection, so the
            # declaration must stay in step with what topology.lua emits --
            # a field missing here silently loses its ring segment.
            declared_arcs = {arc["field"] for arc in options["nodes"]["arcs"]}
            assert declared_arcs == {"arc__online", "arc__offline", "arc__ok", "arc__warn"}, (
                f"arc vocabulary drifted from the collector: {sorted(declared_arcs)}"
            )
            assert options["layoutAlgorithm"] == "layered", "the force layout is unreadable at this size"
            assert options["nodes"]["mainStatUnit"] == "Bps"
            assert options["edges"]["mainStatUnit"] == "Bps"
            # Both would need a joinByField, which in v2beta1 applies to every
            # frame in the panel and would merge nodes into edges. See the
            # module docstring.
            transformations = panel_spec["data"]["spec"]["transformations"]
            kinds = {t["kind"] for t in transformations}
            assert "joinByField" not in kinds, "joinByField would merge the nodes and edges frames"
            assert "secondarystat" not in json.dumps(element)
    assert node_graph_seen, "no nodeGraph panel found"
    assert len(panel_ids) == len(set(panel_ids)), "duplicate panel ids"

    for tab_title, items in grid_items_by_tab(spec["layout"]).items():
        rects: list[tuple[int, int, int, int, str]] = []
        for item in items:
            item_spec = item["spec"]
            x, y, width, height = item_spec["x"], item_spec["y"], item_spec["width"], item_spec["height"]
            name = item_spec["element"]["name"]
            assert 0 <= x <= 23, f"{tab_title} {name} invalid x={x}"
            assert y >= 0, f"{tab_title} {name} invalid y={y}"
            assert 1 <= width <= 24 and height > 0, f"{tab_title} {name} invalid size {width}x{height}"
            assert x + width <= 24, f"{tab_title} {name} overflows 24-col grid"
            rect = (x, y, x + width, y + height, name)
            for other in rects:
                if rect[0] < other[2] and other[0] < rect[2] and rect[1] < other[3] and other[1] < rect[3]:
                    raise AssertionError(f"{tab_title} overlap: {name} with {other[4]}")
            rects.append(rect)

    defined_vars = {variable["spec"]["name"] for variable in spec["variables"]}
    globals_allowed = {
        "__rate_interval",
        "__range",
        "__from",
        "__to",
        "__all",
        "__value",
        "__interval",
        "__auto",
        # Data-link globals: ${__data.fields.<name>} and the time-range
        # passthrough. The variable pattern below stops at the first dot, so
        # allowing __data covers every field reference.
        "__data",
        "__url_time_range",
    }
    referenced_vars: set[str] = set()
    var_pattern = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?")
    for string_value in iter_strings(spec):
        referenced_vars.update(match.group(1) for match in var_pattern.finditer(string_value))
    undefined = referenced_vars - defined_vars - globals_allowed
    assert not undefined, f"undefined variables: {sorted(undefined)}"
    unused = defined_vars - referenced_vars - {"DS_PROMETHEUS"}
    assert not unused, f"unused variables: {sorted(unused)}"

    strings = iter_strings(spec)
    text_blob = json.dumps(dash, sort_keys=True)
    assert "schemaVersion" not in text_blob
    assert "pluginVersion" not in text_blob
    assert "uid" not in dash["metadata"]
    assert PROM_DS in text_blob
    assert "${DS_LOKI}" not in text_blob
    assert any('router=~"$router"' in value for value in strings)
    assert any('job="openwrt"' in value for value in strings)
    query_exprs: list[str] = []
    for element in spec["elements"].values():
        queries = element["spec"]["data"]["spec"]["queries"]
        for query in queries:
            query_exprs.append(query["spec"]["query"]["spec"].get("expr", ""))
    assert not any("node_nat_traffic" in expr for expr in query_exprs)
    # The reconciliation is the point of this dashboard; losing it silently
    # would put the old wrong graph back without changing anything visible in
    # the generator's output shape.
    node_graph_exprs = [expr for expr in query_exprs if "openwrt_topology_node" in expr and "unless on(id)" in expr]
    assert node_graph_exprs, "node query lost its infra-MAC suppression"
    assert any('authority="1"' in expr for expr in query_exprs), "authority preference lost"
    assert any("unless on(target)" in expr for expr in query_exprs), "edge infra suppression lost"


def main() -> None:
    dashboard = build_dashboard()
    rendered = stable_json(dashboard)
    assert json.loads(rendered) == dashboard
    assert stable_json(build_dashboard()) == rendered, "non-deterministic output"
    digest = hashlib.sha256(rendered.encode("utf-8")).hexdigest()

    for output in OUTS:
        output.parent.mkdir(parents=True, exist_ok=True)
        old = output.read_text(encoding="utf-8") if output.exists() else None
        output.write_text(rendered, encoding="utf-8")
        assert output.read_text(encoding="utf-8") == rendered
        state = "updated" if old != rendered else "unchanged"
        print(f"{output}: {state}, panels={len(dashboard['spec']['elements'])}, sha256={digest}")


if __name__ == "__main__":
    main()
