#!/usr/bin/env python3
"""Build the Grafana v2beta1 network topology dashboard for OpenWrt.

The generated JSON files are artifacts. This script is the source of truth.
It writes both the manual import export and the provisioned dashboard copy.

See docs/client-topology-and-netflow-plan.md §2.2-§2.4 and §11 (M5) for the
design. The node graph panel is driven directly by the Prometheus datasource
using instant table queries with refId="nodes"/"edges" -- no Infinity plugin,
no GF_INSTALL_PLUGINS, confirmed viable against the bundled Grafana by the M4
spike.

Node-value choice (the "M4 nuance"): the transformation pipeline applies a
single `organize` rename uniformly to every frame it touches by field name,
not by refId, so there is no way to rename `Value` -> `mainstat` on the edges
frame only while leaving the nodes frame's `Value` field alone within one
panel's transformation list. This builder therefore renames `Value` on both
frames, which means node `mainstat` is the node's own metric value (online/
offline for clients, station count for SSIDs, a constant presence indicator
for router/ap/internet -- see topology.lua), not an edge-derived sum. Every
one of those values is real, not fabricated, so this satisfies the plan's
"never ship a plausible wrong value" rule either way.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from build_openwrt_operations_dashboard import (
    AVAILABILITY_MAPPINGS,
    THRESHOLDS,
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


OUTS = [
    Path("grafana-dashboard-exports/openwrt-topology-v2.json"),
    Path("grafana/provisioning/dashboards/openwrt-topology-v2.json"),
]

PROM_DS = "${DS_PROMETHEUS}"
PROM_FILTER = 'job="openwrt", router=~"$router"'

NODES_EXPR = f"openwrt_topology_node{{{PROM_FILTER}}}"
EDGES_EXPR = f"openwrt_topology_edge{{{PROM_FILTER}}}"

# Common to both node-graph frames: strip Prometheus/scrape plumbing columns
# that mean nothing to the node graph panel and would otherwise show up as
# stray fields. `Value` is renamed, not dropped -- see module docstring.
NOISE_COLUMNS = [
    "Time",
    "__name__",
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


def availability_expr(metric_name: str, collector: str = "") -> str:
    if collector:
        return (
            f'(max({metric_name}{{{PROM_FILTER}}}) '
            f'* max(node_scrape_collector_success{{{PROM_FILTER}, collector="{collector}"}})) or vector(0)'
        )
    return f"max({metric_name}{{{PROM_FILTER}}}) or vector(0)"


def nodegraph(
    pid: int,
    title: str,
    desc: str,
) -> tuple[str, dict[str, Any]]:
    return panel(
        pid,
        title,
        "nodeGraph",
        [
            prom_query(EDGES_EXPR, ref="edges", fmt="table", instant=True),
            prom_query(NODES_EXPR, ref="nodes", fmt="table", instant=True),
        ],
        "none",
        desc,
        options={},
        field_defaults={"thresholds": THRESHOLDS["neutral"]},
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
    b.add(overview, text(1, "", "## Network topology\ninternet -> router -> AP -> SSID -> client, built from the same identity data as the clients dashboard. Node and edge frames come from the same scrape (topology.lua), so a client that disappears never leaves a dangling edge. Edge and node values are real presence/association counts, not traffic volume -- per-link throughput lands in a later milestone (M6/M7)."), 0, 0, 24, 3)
    b.add(overview, stat(2, "Topology Collector", availability_expr("openwrt_topology_collector_available", "topology"), "none", "Collector availability gated by both the topology flag and the Lua exporter's per-collector scrape success.", mappings=AVAILABILITY_MAPPINGS, thresholds_key="unavailable", color_mode="value"), 0, 3, 6, 4)
    b.add(overview, stat(3, "Nodes", f'count({NODES_EXPR}) or vector(0)', "none", "Total topology nodes: internet, router, AP, SSIDs, and known clients.", graph=True, color_mode="value"), 6, 3, 6, 4)
    b.add(overview, stat(4, "Edges", f'count({EDGES_EXPR}) or vector(0)', "none", "Total topology edges (wan/ap/radio/assoc/lan links).", graph=True, color_mode="value"), 12, 3, 6, 4)
    b.add(overview, stat(5, "Online Clients", f'count(openwrt_topology_node{{{PROM_FILTER}, arc__online="1"}}) or vector(0)', "none", "Client nodes currently reporting arc__online=1.", graph=True, color_mode="value"), 18, 3, 6, 4)
    tabs.append(b.tab("Overview", overview))

    topology: list[dict[str, Any]] = []
    b.add(topology, nodegraph(100, "Network Topology", "internet -> router -> AP -> SSID -> client. Node mainstat is the node's own metric value (see this builder's module docstring for why edge-derived node stats were not chosen). Isolated client nodes mean connection state is unknown (assoclist unavailable), not that the client is wired -- the collector never guesses."), 0, 0, 24, 16)
    tabs.append(b.tab("Topology", topology))

    quality: list[dict[str, Any]] = []
    b.add(quality, text(200, "", "## Data quality\nRaw node and edge series for verifying every edge endpoint resolves to a node and no data was silently dropped."), 0, 0, 24, 3)
    b.add(quality, table(201, "Topology Nodes", [
        prom_query(NODES_EXPR, "", "A", fmt="table", instant=True),
    ], "One row per current openwrt_topology_node series.", transformations=[
        organize(exclude=["Time", "__name__", "cluster", "endpoint", "namespace", "prometheus", "prometheus_replica", "service"]),
        sort_by("id", desc=False),
    ], sort_col="id", sort_desc=False), 0, 3, 24, 9)
    b.add(quality, table(202, "Topology Edges", [
        prom_query(EDGES_EXPR, "", "A", fmt="table", instant=True),
    ], "One row per current openwrt_topology_edge series. Every source/target must match an id in the nodes table above.", transformations=[
        organize(exclude=["Time", "__name__", "cluster", "endpoint", "namespace", "prometheus", "prometheus_replica", "service"]),
        sort_by("id", desc=False),
    ], sort_col="id", sort_desc=False), 0, 12, 24, 9)
    tabs.append(b.tab("Data Quality", quality))

    spec: dict[str, Any] = {
        "title": "OpenWrt - Topology",
        "description": "Network topology node graph for OpenWrt: internet, router, AP, SSID, and client identity, driven by the Prometheus datasource with no plugin install.",
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
    globals_allowed = {"__rate_interval", "__range", "__from", "__to", "__all", "__value", "__interval", "__auto"}
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
