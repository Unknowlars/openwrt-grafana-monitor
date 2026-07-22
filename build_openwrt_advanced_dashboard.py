#!/usr/bin/env python3
"""Build the optional-profile dashboard for OpenWrt.

This is a separate artifact from the operations dashboard. It is safe to
provision alongside the classic exports and remains useful when traffic,
WiFi-mesh, or DPI profiles are not installed because each profile exposes an
explicit availability metric.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from build_openwrt_operations_dashboard import (
    AVAILABILITY_MAPPINGS,
    BLUE,
    GREEN,
    GRAY,
    RED,
    THRESHOLDS,
    DashboardBuilder,
    bargauge,
    datasource_var,
    limit,
    organize,
    panel,
    prom_query,
    query_var,
    stat,
    stable_json,
    table,
    text,
    timeseries,
    tf,
)


OUTS = [
    Path("grafana-dashboard-exports/openwrt-advanced-v2.json"),
    Path("grafana/provisioning/dashboards/openwrt-advanced-v2.json"),
]

PROM_DS = "${DS_PROMETHEUS}"
PROM_FILTER = 'job="openwrt", router=~"$router"'
DEVICE_FILTER = f'{PROM_FILTER}, device=~"$device"'


def variables() -> list[dict[str, Any]]:
    return [
        datasource_var("DS_PROMETHEUS", "Prometheus", "prometheus", "prometheus"),
        query_var("router", "Router", 'label_values(node_load1{job="openwrt"}, router)', "openwrt", include_all=True, multi=True),
        query_var("device", "Device", f'label_values(openwrt_device_info{{{PROM_FILTER}}}, device)', "All", include_all=True, multi=True),
    ]


def availability(pid: int, title: str, metric_name: str, description: str, collector: str = "") -> tuple[str, dict[str, Any]]:
    """Profile health tile.

    A collector's own availability flag is not sufficient evidence that the
    profile works: on the live router openwrt_device_traffic_collector_available
    read 1 while the collector was aborting mid-scrape and exporting no traffic
    series at all, so this tile was green during a total profile outage. Gate it
    on the exporter's own scrape result for that collector as well, and treat an
    absent collector as unavailable rather than as missing data.
    """
    if collector:
        expr = (
            f'(max({metric_name}{{{PROM_FILTER}}}) '
            f'* max(node_scrape_collector_success{{{PROM_FILTER}, collector="{collector}"}})) or vector(0)'
        )
    else:
        expr = f'max({metric_name}{{{PROM_FILTER}}}) or vector(0)'
    return stat(
        pid,
        title,
        expr,
        "none",
        description,
        mappings=AVAILABILITY_MAPPINGS,
        thresholds_key="unavailable",
        color_mode="value",
    )


def build_dashboard() -> dict[str, Any]:
    builder = DashboardBuilder()
    tabs: list[dict[str, Any]] = []

    overview: list[dict[str, Any]] = []
    builder.add(overview, text(1, "", "## Advanced OpenWrt monitoring\nProfile-aware views for per-device nftables traffic, WiFi mesh signals, and optional Netifyd DPI. Missing profiles are shown as unavailable."), 0, 0, 24, 3)
    builder.add(overview, availability(2, "Traffic Profile", "openwrt_device_traffic_collector_available", "Read-only nftables per-device counters. Green requires both the collector flag and a successful exporter scrape of that collector. Enable the traffic or full router profile.", collector="device_traffic"), 0, 3, 6, 4)
    builder.add(overview, availability(3, "WiFi Mesh Profile", "openwrt_wifi_mesh_collector_available", "Optional radio and usteer mesh metrics. Green requires both the collector flag and a successful exporter scrape. Enable the wifi_mesh or full router profile.", collector="wifi_dethrash"), 6, 3, 6, 4)
    builder.add(overview, availability(4, "DPI Profile", "openwrt_dpi_collector_available", "Optional Netifyd status snapshot metrics. Reads 0 when the snapshot is missing or older than 5 minutes, so a wedged Netifyd is not shown as healthy.", collector="dpi_netifyd"), 12, 3, 6, 4)
    builder.add(overview, stat(5, "Tracked Devices", f'count(openwrt_device_info{{{PROM_FILTER}}}) or vector(0)', "none", "Current device identities discovered from the DHCP lease map and nftables sets."), 18, 3, 6, 4)
    builder.add(overview, timeseries(6, "Tracked Device Traffic", [prom_query(f'sum by(direction) (rate(openwrt_device_traffic_bytes_total{{{DEVICE_FILTER}}}[$__rate_interval]))', "{{direction}}", "A")], "Bps", "Traffic rate from nftables counters. Hostnames are bounded and selected from DHCP leases.", overrides=[{"matcher": {"id": "byName", "options": "upload"}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": BLUE}}]}, {"matcher": {"id": "byName", "options": "download"}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": GREEN}}]}]), 0, 7, 12, 8)
    builder.add(overview, timeseries(7, "WiFi Radio Signal", [prom_query(f'avg by(device, ifname) (wifi_radio_txpower_dbm{{{PROM_FILTER}}})', "{{device}} {{ifname}}", "A")], "dBm", "Radio transmit power. This is an RF configuration signal, not client RSSI."), 12, 7, 12, 8)
    builder.add(overview, bargauge(8, "Top Tracked Devices", [prom_query(f'topk(10, sum by(device) (rate(openwrt_device_traffic_bytes_total{{{DEVICE_FILTER}}}[$__rate_interval])))', "{{device}}", "A")], "Bps", "Bounded top-10 device traffic ranking. It is descriptive, not a health score.", transformations=[limit(10)]), 0, 15, 12, 8)
    builder.add(overview, timeseries(9, "DPI Active Flows", [prom_query(f'openwrt_dpi_active_flows{{{PROM_FILTER}}} or vector(0)', "Active flows", "A"), prom_query(f'openwrt_dpi_devices{{{PROM_FILTER}}} or vector(0)', "DPI devices", "B")], "none", "Netifyd status snapshot values. No data means the DPI profile is not installed or has no readable status snapshot."), 12, 15, 12, 8)
    tabs.append(builder.tab("Advanced Overview", overview))

    traffic: list[dict[str, Any]] = []
    builder.add(traffic, text(100, "", "## Per-device traffic\nThe router installs accept-policy nftables counters before the normal forwarding path. Broadcast and multicast are excluded. Counters are read through `nft -j` on the router; no privileged exporter binary is required."), 0, 0, 24, 4)
    builder.add(traffic, timeseries(101, "Upload and Download by Device", [prom_query(f'sum by(device, direction) (rate(openwrt_device_traffic_bytes_total{{{DEVICE_FILTER}}}[$__rate_interval]))', "{{device}} {{direction}}", "A")], "Bps", "Per-device byte rate. Use the Device variable to focus the chart when the router has many leases."), 0, 4, 24, 9)
    builder.add(traffic, table(102, "Device Identity", [prom_query(f'openwrt_device_info{{{DEVICE_FILTER}}}', "", "A", fmt="table", instant=True)], "Hostname-first identity labels with bounded IP and MAC metadata.", transformations=[organize(exclude=["Time", "__name__", "Value", "cluster", "endpoint", "instance", "job", "namespace", "prometheus", "prometheus_replica", "router", "service"], rename={"device": "Device", "ip": "IP", "mac": "MAC", "interface": "Interface"}, index={"Device": 0, "IP": 1, "MAC": 2, "Interface": 3})], sort_col="Device", sort_desc=False), 0, 13, 12, 9)
    builder.add(traffic, timeseries(103, "Packets by Direction", [prom_query(f'sum by(direction) (rate(openwrt_device_traffic_packets_total{{{DEVICE_FILTER}}}[$__rate_interval]))', "{{direction}}", "A")], "pps", "Packet rate from the same nftables counters. Large gaps can indicate a counter-set reload or lease churn."), 12, 13, 12, 9)
    builder.add(traffic, stat(104, "Traffic Counter Series", f'count(openwrt_device_traffic_bytes_total{{{DEVICE_FILTER}}}) or vector(0)', "none", "Current byte-counter series for the selected router and device filter."), 0, 22, 6, 4)
    builder.add(traffic, stat(105, "Traffic Collector", f'max(openwrt_device_traffic_collector_available{{{PROM_FILTER}}}) or vector(0)', "none", "Explicit collector state. A value of 0 means the profile, JSON dependency, or nftables set is unavailable.", mappings=AVAILABILITY_MAPPINGS, thresholds_key="unavailable", color_mode="value"), 6, 22, 6, 4)
    builder.add(traffic, stat(106, "Traffic Packets", f'sum(rate(openwrt_device_traffic_packets_total{{{DEVICE_FILTER}}}[$__rate_interval])) or vector(0)', "pps", "Aggregate packet rate for the selected device filter."), 12, 22, 6, 4)
    builder.add(traffic, stat(107, "Traffic Bytes", f'sum(rate(openwrt_device_traffic_bytes_total{{{DEVICE_FILTER}}}[$__rate_interval])) or vector(0)', "Bps", "Aggregate byte rate for the selected device filter."), 18, 22, 6, 4)
    tabs.append(builder.tab("Device Traffic", traffic))

    wifi: list[dict[str, Any]] = []
    builder.add(wifi, text(200, "", "## WiFi mesh\nRadio settings use low-cardinality labels. usteer metrics are emitted only when usteer is present and exposes local_info. Client hearing maps remain intentionally outside the default profile to bound MAC cardinality."), 0, 0, 24, 4)
    builder.add(wifi, availability(201, "Mesh Collector", "openwrt_wifi_mesh_collector_available", "Whether the optional Lua mesh collector loaded its ubus and iwinfo dependencies and completed its scrape.", collector="wifi_dethrash"), 0, 4, 6, 4)
    builder.add(wifi, stat(202, "Associated Clients", f'sum(wifi_usteer_associated_clients{{{PROM_FILTER}}}) or vector(0)', "none", "Associated clients reported by usteer local_info. Zero can be a valid empty mesh state."), 6, 4, 6, 4)
    builder.add(wifi, stat(203, "Mesh Load", f'max(wifi_usteer_load{{{PROM_FILTER}}}) or vector(0)', "none", "Local usteer load value when available."), 12, 4, 6, 4)
    builder.add(wifi, stat(204, "Radios", f'count(wifi_radio_channel{{{PROM_FILTER}}}) or vector(0)', "none", "Radios with channel data from iwinfo."), 18, 4, 6, 4)
    builder.add(wifi, timeseries(205, "Radio Channel and Frequency", [prom_query(f'wifi_radio_channel{{{PROM_FILTER}}}', "{{device}} {{ifname}} channel", "A"), prom_query(f'wifi_radio_frequency_mhz{{{PROM_FILTER}}}', "{{device}} {{ifname}} MHz", "B")], "none", "Current channel and center frequency per radio interface."), 0, 8, 12, 8)
    builder.add(wifi, timeseries(206, "Radio TX Power", [prom_query(f'wifi_radio_txpower_dbm{{{PROM_FILTER}}}', "{{device}} {{ifname}}", "A"), prom_query(f'wifi_radio_txpower_offset_dbm{{{PROM_FILTER}}}', "{{device}} {{ifname}} offset", "B")], "dBm", "Configured radio transmit power and calibration offset."), 12, 8, 12, 8)
    builder.add(wifi, table(207, "WiFi Security and Roaming Features", [prom_query(f'wifi_iface_ieee80211r_enabled{{{PROM_FILTER}}}', "", "A", fmt="table", instant=True), prom_query(f'wifi_iface_ieee80211k_enabled{{{PROM_FILTER}}}', "", "B", fmt="table", instant=True), prom_query(f'wifi_iface_ieee80211v_enabled{{{PROM_FILTER}}}', "", "C", fmt="table", instant=True)], "802.11r/k/v settings per configured wireless interface.", transformations=[tf("merge"), organize(exclude=["Time", "__name__", "cluster", "endpoint", "instance", "job", "namespace", "prometheus", "prometheus_replica", "router", "service"], rename={"device": "Radio", "ifname": "Interface", "ssid": "SSID", "Value #A": "11r", "Value #B": "11k", "Value #C": "11v"}), limit(25)], sort_col="Interface", sort_desc=False), 0, 16, 24, 9)
    builder.add(wifi, timeseries(208, "usteer Roam Events", [prom_query(f'wifi_usteer_roam_events_source{{{PROM_FILTER}}}', "{{ap}} source", "A"), prom_query(f'wifi_usteer_roam_events_target{{{PROM_FILTER}}}', "{{ap}} target", "B")], "none", "Cumulative roam event values reported by usteer local_info."), 0, 25, 12, 8)
    builder.add(wifi, bargauge(209, "Associated Clients by AP", [prom_query(f'wifi_usteer_associated_clients{{{PROM_FILTER}}}', "{{ap}}", "A")], "none", "Current associated client count by usteer AP label.", transformations=[limit(25)]), 12, 25, 12, 8)
    tabs.append(builder.tab("WiFi Mesh", wifi))

    dpi: list[dict[str, Any]] = []
    builder.add(dpi, text(300, "", "## DPI and data quality\nThe DPI collector reads `/var/run/netifyd/status.json` and exports only top bounded application/protocol entries. Netifyd does not replace Prometheus traffic counters; it adds application context when installed."), 0, 0, 24, 4)
    builder.add(dpi, availability(301, "DPI Collector", "openwrt_dpi_collector_available", "1 means the router read a fresh Netifyd snapshot and completed the scrape. 0 means missing, stale, or failed - all explicit unavailable states.", collector="dpi_netifyd"), 0, 4, 6, 4)
    builder.add(dpi, stat(302, "Active DPI Flows", f'openwrt_dpi_active_flows{{{PROM_FILTER}}} or vector(0)', "none", "Current active flows from the Netifyd snapshot."), 6, 4, 6, 4)
    builder.add(dpi, stat(303, "DPI Devices", f'openwrt_dpi_devices{{{PROM_FILTER}}} or vector(0)', "none", "Current devices reported in the Netifyd snapshot."), 12, 4, 6, 4)
    builder.add(dpi, stat(304, "Application Series", f'count(openwrt_dpi_application_bytes{{{PROM_FILTER}}}) or vector(0)', "none", "Bounded application series currently exported by the router."), 18, 4, 6, 4)
    builder.add(dpi, bargauge(305, "Top Applications by Bytes", [prom_query(f'topk(15, openwrt_dpi_application_bytes{{{PROM_FILTER}}})', "{{application}}", "A")], "bytes", "Top bounded application byte values from Netifyd. Snapshot values may move down when flows expire.", transformations=[limit(15)]), 0, 8, 12, 9)
    builder.add(dpi, bargauge(306, "Top Protocols by Flows", [prom_query(f'topk(15, openwrt_dpi_protocol_flows{{{PROM_FILTER}}})', "{{protocol}}", "A")], "none", "Top bounded protocol flow counts from Netifyd.", transformations=[limit(15)]), 12, 8, 12, 9)
    builder.add(dpi, timeseries(307, "Collector Availability", [prom_query(f'openwrt_device_traffic_collector_available{{{PROM_FILTER}}} or vector(0)', "nftables traffic", "A"), prom_query(f'openwrt_wifi_mesh_collector_available{{{PROM_FILTER}}} or vector(0)', "WiFi mesh", "B"), prom_query(f'openwrt_dpi_collector_available{{{PROM_FILTER}}} or vector(0)', "DPI", "C"), prom_query(f'openwrt_wifi_radio_collector_available{{{PROM_FILTER}}} or vector(0)', "WiFi radio helper", "D")], "none", "Profile availability over time. This distinguishes a disabled profile from a broken primary scrape."), 0, 17, 12, 8)
    builder.add(dpi, table(308, "Exporter Collector Health", [prom_query(f'node_scrape_collector_success{{{PROM_FILTER}}}', "", "A", fmt="table", instant=True)], "Official Lua exporter collector success state. Investigate this before treating a missing optional metric as a dashboard defect.", transformations=[organize(exclude=["Time", "__name__", "Value", "cluster", "endpoint", "instance", "job", "namespace", "prometheus", "prometheus_replica", "router", "service"], rename={"collector": "Collector", "Value": "Success"}), limit(50)], sort_col="Collector", sort_desc=False), 12, 17, 12, 8)
    builder.add(dpi, stat(309, "Textfile Freshness", f'max(time() - node_textfile_mtime_seconds{{{PROM_FILTER}}}) or vector(999999)', "s", "Age of helper-generated textfile metrics. This is separate from Lua profile availability." , thresholds_key="freshness", graph=True), 0, 25, 6, 5)
    builder.add(dpi, stat(310, "Prometheus Scrape", f'min(up{{{PROM_FILTER}}}) or vector(0)', "none", "Primary router scrape reachability.", mappings=[{"type": "value", "options": {"1": {"text": "Healthy", "color": "green", "index": 0}, "0": {"text": "Down", "color": "red", "index": 1}}}], thresholds_key="ok_bad"), 6, 25, 6, 5)
    builder.add(dpi, stat(311, "DPI Application Flow Series", f'count(openwrt_dpi_application_flows{{{PROM_FILTER}}}) or vector(0)', "none", "Application flow series currently available from the snapshot."), 12, 25, 6, 5)
    builder.add(dpi, stat(312, "Profile Metrics", f'count({{__name__=~"openwrt_(device_traffic|wifi_mesh|dpi|wifi_radio)_.*", {PROM_FILTER}}}) or vector(0)', "none", "Current count of optional profile metric series. Use the panels above to identify which profile is absent."), 18, 25, 6, 5)
    tabs.append(builder.tab("DPI and Data Quality", dpi))

    spec: dict[str, Any] = {
        "title": "OpenWrt - Advanced Monitoring",
        "description": "Optional profile dashboard for OpenWrt nftables traffic, WiFi mesh, Netifyd DPI, and data quality.",
        "tags": ["openwrt", "advanced", "router"],
        "cursorSync": "Crosshair",
        "editable": True,
        "preload": False,
        "liveNow": False,
        "variables": variables(),
        "elements": builder.elements,
        "layout": {"kind": "TabsLayout", "spec": {"tabs": tabs}},
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
    }
    dashboard = {
        "apiVersion": "dashboard.grafana.app/v2beta1",
        "kind": "Dashboard",
        "metadata": {"name": "openwrt-advanced"},
        "spec": spec,
    }
    validate_dashboard(dashboard)
    return dashboard


def layout_refs(layout: Any) -> list[str]:
    if isinstance(layout, dict):
        result = []
        if layout.get("kind") == "ElementReference" and "name" in layout:
            result.append(layout["name"])
        for value in layout.values():
            result.extend(layout_refs(value))
        return result
    if isinstance(layout, list):
        result = []
        for value in layout:
            result.extend(layout_refs(value))
        return result
    return []


def iter_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        result: list[str] = []
        for child in value.values():
            result.extend(iter_strings(child))
        return result
    if isinstance(value, list):
        result = []
        for child in value:
            result.extend(iter_strings(child))
        return result
    return []


def validate_dashboard(dashboard: dict[str, Any]) -> None:
    assert dashboard["apiVersion"] == "dashboard.grafana.app/v2beta1"
    assert dashboard["kind"] == "Dashboard"
    assert dashboard["metadata"]["name"] == "openwrt-advanced"
    spec = dashboard["spec"]
    assert spec["title"] == "OpenWrt - Advanced Monitoring"
    assert [tab["spec"]["title"] for tab in spec["layout"]["spec"]["tabs"]] == [
        "Advanced Overview",
        "Device Traffic",
        "WiFi Mesh",
        "DPI and Data Quality",
    ]
    refs = layout_refs(spec["layout"])
    assert set(refs) == set(spec["elements"])
    assert len(refs) == len(set(refs))
    ids = []
    for key, element in spec["elements"].items():
        assert key.startswith("panel-")
        panel_spec = element["spec"]
        ids.append(panel_spec["id"])
        assert key == f"panel-{panel_spec['id']}"
        if panel_spec["vizConfig"]["group"] not in {"text", "logs"}:
            assert panel_spec["vizConfig"]["spec"]["fieldConfig"]["defaults"].get("unit") != "short"
    assert len(ids) == len(set(ids))
    for tab in spec["layout"]["spec"]["tabs"]:
        items = tab["spec"]["layout"]["spec"]["rows"][0]["spec"]["layout"]["spec"]["items"]
        rectangles = []
        for item in items:
            item_spec = item["spec"]
            x, y, width, height = item_spec["x"], item_spec["y"], item_spec["width"], item_spec["height"]
            assert 0 <= x <= 23 and y >= 0 and 1 <= width <= 24 and height > 0 and x + width <= 24
            rectangle = (x, y, x + width, y + height, item_spec["element"]["name"])
            for other in rectangles:
                assert not (rectangle[0] < other[2] and other[0] < rectangle[2] and rectangle[1] < other[3] and other[1] < rectangle[3]), (tab["spec"]["title"], rectangle, other)
            rectangles.append(rectangle)
    text_blob = json.dumps(dashboard, sort_keys=True)
    assert "schemaVersion" not in text_blob
    assert "pluginVersion" not in text_blob
    assert PROM_DS in text_blob
    defined = {variable["spec"]["name"] for variable in spec["variables"]}
    globals_allowed = {"__rate_interval", "__range", "__from", "__to", "__all", "__value", "__interval", "__auto"}
    referenced: set[str] = set()
    for value in iter_strings(spec):
        referenced.update(re.findall(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?", value))
    assert not (referenced - defined - globals_allowed), referenced - defined - globals_allowed
    assert not (defined - referenced - {"DS_PROMETHEUS"}), defined - referenced - {"DS_PROMETHEUS"}


def main() -> None:
    dashboard = build_dashboard()
    rendered = stable_json(dashboard)
    assert json.loads(rendered) == dashboard
    assert stable_json(build_dashboard()) == rendered
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
