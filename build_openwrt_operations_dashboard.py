#!/usr/bin/env python3
"""
Build a manually importable Grafana v2beta1 dashboard for OpenWrt operations.

The generated JSON is the artifact. This script is the source of truth.
It intentionally does not touch the existing provisioned dashboards.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any


OUT = Path("grafana-dashboard-exports/openwrt-operations-v2.json")

PROM_DS = "${DS_PROMETHEUS}"
LOKI_DS = "${DS_LOKI}"
PROM_FILTER = 'job="openwrt", router=~"$router"'
LOKI_FILTER = 'job="openwrt-syslog", router=~"$router"'

GREEN = "#1A9E3A"
YELLOW = "#E0B400"
RED = "#F2495C"
ORANGE = "#FF9830"
BLUE = "#5794F2"
GRAY = "#808080"
PURPLE = "#B877D9"


def deep(value: Any) -> Any:
    return copy.deepcopy(value)


def thresholds(*steps: tuple[str, float | None]) -> dict[str, Any]:
    return {
        "mode": "absolute",
        "steps": [{"color": color, "value": value} for color, value in steps],
    }


THRESHOLDS = {
    "ok_bad": thresholds(("green", 0), ("red", 1)),
    "capacity": thresholds(("green", 0), ("yellow", 0.70), ("red", 0.90)),
    "capacity_soft": thresholds(("green", 0), ("yellow", 0.60), ("red", 0.85)),
    "loss_percent": thresholds(("green", 0), ("yellow", 1), ("red", 5)),
    "latency_seconds": thresholds(("green", 0), ("yellow", 1), ("red", 5)),
    "freshness": thresholds(("green", 0), ("yellow", 120), ("red", 300)),
    "neutral": thresholds(("blue", 0)),
    "unavailable": thresholds(("gray", 0), ("blue", 1)),
}


STATUS_MAPPINGS = [
    {"type": "value", "options": {"1": {"text": "Healthy", "color": "green", "index": 0}}},
    {"type": "value", "options": {"0": {"text": "Down", "color": "red", "index": 1}}},
    {"type": "special", "options": {"match": "null", "result": {"text": "No data", "color": "gray", "index": 2}}},
    {"type": "special", "options": {"match": "nan", "result": {"text": "n/a", "color": "gray", "index": 3}}},
]

BOOLEAN_OK_MAPPINGS = [
    {"type": "value", "options": {"1": {"text": "OK", "color": "green", "index": 0}}},
    {"type": "value", "options": {"0": {"text": "Problem", "color": "red", "index": 1}}},
    {"type": "special", "options": {"match": "null", "result": {"text": "Unavailable", "color": "gray", "index": 2}}},
    {"type": "special", "options": {"match": "nan", "result": {"text": "n/a", "color": "gray", "index": 3}}},
]

CHANGE_MAPPINGS = [
    {"type": "value", "options": {"0": {"text": "No change", "color": "green", "index": 0}}},
    {"type": "value", "options": {"1": {"text": "Changed", "color": "yellow", "index": 1}}},
    {"type": "special", "options": {"match": "null", "result": {"text": "Unavailable", "color": "gray", "index": 2}}},
]

AVAILABILITY_MAPPINGS = [
    {"type": "value", "options": {"0": {"text": "Not collected", "color": "gray", "index": 0}}},
    {"type": "value", "options": {"1": {"text": "Collected", "color": "blue", "index": 1}}},
    {"type": "special", "options": {"match": "null", "result": {"text": "Not collected", "color": "gray", "index": 2}}},
]

DEVICE_STATE_MAPPINGS = [
    {"type": "value", "options": {"1": {"text": "Online", "color": "green", "index": 0}}},
    {"type": "value", "options": {"0": {"text": "Offline", "color": "gray", "index": 1}}},
]


def color_override(name: str, color: str) -> dict[str, Any]:
    return {
        "matcher": {"id": "byName", "options": name},
        "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": color}}],
    }


def regexp_color_override(pattern: str, color: str) -> dict[str, Any]:
    return {
        "matcher": {"id": "byRegexp", "options": pattern},
        "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": color}}],
    }


COMMON_OVERRIDES = [
    regexp_color_override("/RX|Download|Received|Online|Healthy|Success|OK/i", GREEN),
    regexp_color_override("/TX|Upload|Transmit/i", BLUE),
    regexp_color_override("/error|drop|fail|down|offline|critical|problem/i", RED),
    regexp_color_override("/warn|changed|loss/i", ORANGE),
    regexp_color_override("/limit|maximum/i", YELLOW),
]


def prom_query(expr: str, legend: str = "", ref: str = "A", fmt: str = "time_series", instant: bool = False) -> dict[str, Any]:
    spec: dict[str, Any] = {"expr": expr, "legendFormat": legend}
    if fmt != "time_series":
        spec["format"] = fmt
    if instant:
        spec["instant"] = True
    return {
        "kind": "PanelQuery",
        "spec": {
            "refId": ref,
            "hidden": False,
            "query": {
                "kind": "DataQuery",
                "group": "prometheus",
                "version": "v0",
                "datasource": {"name": PROM_DS},
                "spec": spec,
            },
        },
    }


def loki_query(expr: str, ref: str = "A", query_type: str = "range") -> dict[str, Any]:
    return {
        "kind": "PanelQuery",
        "spec": {
            "refId": ref,
            "hidden": False,
            "query": {
                "kind": "DataQuery",
                "group": "loki",
                "version": "v0",
                "datasource": {"name": LOKI_DS},
                "spec": {"expr": expr, "queryType": query_type},
            },
        },
    }


def tf(kind: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"kind": kind, "spec": {"id": kind, "options": options or {}}}


def organize(
    exclude: list[str] | None = None,
    rename: dict[str, str] | None = None,
    index: dict[str, int] | None = None,
) -> dict[str, Any]:
    return tf(
        "organize",
        {
            "excludeByName": {name: True for name in (exclude or [])},
            "renameByName": rename or {},
            "indexByName": index or {},
        },
    )


def sort_by(field: str, desc: bool = True) -> dict[str, Any]:
    return tf("sortBy", {"fields": [{"displayName": field, "desc": desc}]})


def limit(n: int) -> dict[str, Any]:
    return tf("limit", {"limitField": n})


def convert_time(field: str) -> dict[str, Any]:
    return tf("convertFieldType", {"conversions": [{"fieldName": field, "destinationType": "time"}]})


def calculate(alias: str, left: str, right: str, operator: str, replace: bool = False) -> dict[str, Any]:
    return tf(
        "calculateField",
        {
            "mode": "binary",
            "alias": alias,
            "binary": {"left": left, "right": right, "operator": operator},
            "replace": replace,
        },
    )


def data_group(queries: list[dict[str, Any]], transformations: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "kind": "QueryGroup",
        "spec": {
            "queries": queries,
            "transformations": transformations or [],
            "queryOptions": {},
        },
    }


def panel(
    pid: int,
    title: str,
    viz: str,
    queries: list[dict[str, Any]],
    unit: str,
    desc: str,
    options: dict[str, Any] | None = None,
    field_defaults: dict[str, Any] | None = None,
    overrides: list[dict[str, Any]] | None = None,
    transformations: list[dict[str, Any]] | None = None,
    links: list[dict[str, Any]] | None = None,
) -> tuple[str, dict[str, Any]]:
    defaults = {
        "unit": unit,
        "mappings": [],
        "thresholds": THRESHOLDS["neutral"],
        "color": {"mode": "thresholds" if viz in {"stat", "gauge", "bargauge"} else "palette-classic-by-name"},
    }
    if field_defaults:
        defaults.update(deep(field_defaults))
    key = f"panel-{pid}"
    element = {
        "kind": "Panel",
        "spec": {
            "id": pid,
            "title": title,
            "description": desc,
            "links": links or [],
            "data": data_group(queries, transformations),
            "vizConfig": {
                "kind": "VizConfig",
                "group": viz,
                "version": "",
                "spec": {
                    "options": options or {},
                    "fieldConfig": {"defaults": defaults, "overrides": overrides or []},
                },
            },
        },
    }
    return key, element


def stat(
    pid: int,
    title: str,
    expr: str,
    unit: str,
    desc: str,
    legend: str = "",
    thresholds_key: str = "neutral",
    mappings: list[dict[str, Any]] | None = None,
    graph: bool = False,
    decimals: int | None = None,
    color_mode: str = "background_solid",
    no_value: str = "Unavailable",
) -> tuple[str, dict[str, Any]]:
    defaults: dict[str, Any] = {
        "thresholds": THRESHOLDS[thresholds_key],
        "mappings": mappings or [],
        "color": {"mode": "thresholds"},
    }
    if decimals is not None:
        defaults["decimals"] = decimals
    return panel(
        pid,
        title,
        "stat",
        [prom_query(expr, legend)],
        unit,
        desc,
        options={
            "colorMode": color_mode,
            "graphMode": "area" if graph else "none",
            "justifyMode": "center",
            "orientation": "auto",
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "textMode": "auto",
            "wideLayout": True,
            "noValue": no_value,
        },
        field_defaults=defaults,
    )


def loki_stat(
    pid: int,
    title: str,
    expr: str,
    desc: str,
    thresholds_key: str = "neutral",
    mappings: list[dict[str, Any]] | None = None,
) -> tuple[str, dict[str, Any]]:
    return panel(
        pid,
        title,
        "stat",
        [loki_query(expr)],
        "none",
        desc,
        options={
            "colorMode": "background_solid",
            "graphMode": "none",
            "justifyMode": "center",
            "orientation": "auto",
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "textMode": "auto",
            "wideLayout": True,
            "noValue": "0",
        },
        field_defaults={"thresholds": THRESHOLDS[thresholds_key], "mappings": mappings or []},
    )


def timeseries(
    pid: int,
    title: str,
    queries: list[dict[str, Any]],
    unit: str,
    desc: str,
    stacked: bool = False,
    fill: int = 15,
    line_interpolation: str = "linear",
    overrides: list[dict[str, Any]] | None = None,
    thresholds_value: dict[str, Any] | None = None,
    legend_calcs: list[str] | None = None,
) -> tuple[str, dict[str, Any]]:
    defaults = {
        "color": {"mode": "palette-classic-by-name"},
        "custom": {
            "drawStyle": "line",
            "lineInterpolation": line_interpolation,
            "lineWidth": 2,
            "fillOpacity": fill,
            "gradientMode": "opacity",
            "showPoints": "never",
            "spanNulls": False,
            "axisSoftMin": 0,
            "stacking": {"group": "A", "mode": "normal" if stacked else "none"},
            "thresholdsStyle": {"mode": "dashed+area" if thresholds_value else "off"},
            "scaleDistribution": {"type": "linear"},
            "hideFrom": {"legend": False, "tooltip": False, "viz": False},
        },
        "thresholds": thresholds_value or THRESHOLDS["neutral"],
    }
    return panel(
        pid,
        title,
        "timeseries",
        queries,
        unit,
        desc,
        options={
            "legend": {
                "calcs": legend_calcs or ["lastNotNull", "max"],
                "displayMode": "table",
                "placement": "bottom",
                "showLegend": True,
            },
            "tooltip": {"hideZeros": False, "mode": "multi", "sort": "desc"},
        },
        field_defaults=defaults,
        overrides=(overrides or []) + COMMON_OVERRIDES,
    )


def bargauge(
    pid: int,
    title: str,
    queries: list[dict[str, Any]],
    unit: str,
    desc: str,
    thresholds_key: str = "neutral",
    mappings: list[dict[str, Any]] | None = None,
    max_value: float | None = None,
    color_mode: str = "continuous-blues",
    transformations: list[dict[str, Any]] | None = None,
) -> tuple[str, dict[str, Any]]:
    defaults: dict[str, Any] = {
        "color": {"mode": color_mode},
        "mappings": mappings or [],
        "thresholds": THRESHOLDS[thresholds_key],
        "min": 0,
    }
    if max_value is not None:
        defaults["max"] = max_value
    return panel(
        pid,
        title,
        "bargauge",
        queries,
        unit,
        desc,
        options={
            "displayMode": "gradient",
            "orientation": "horizontal",
            "namePlacement": "auto",
            "valueMode": "color",
            "showUnfilled": True,
            "sizing": "auto",
            "minVizHeight": 16,
            "maxVizHeight": 300,
            "minVizWidth": 8,
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "legend": {"calcs": [], "displayMode": "list", "placement": "bottom", "showLegend": False},
        },
        field_defaults=defaults,
        transformations=transformations,
    )


def gauge(
    pid: int,
    title: str,
    expr: str,
    desc: str,
    thresholds_key: str = "capacity",
) -> tuple[str, dict[str, Any]]:
    return panel(
        pid,
        title,
        "gauge",
        [prom_query(expr)],
        "percentunit",
        desc,
        options={
            "minVizHeight": 75,
            "minVizWidth": 75,
            "orientation": "auto",
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "showThresholdLabels": False,
            "showThresholdMarkers": True,
            "sizing": "auto",
        },
        field_defaults={"min": 0, "max": 1, "thresholds": THRESHOLDS[thresholds_key]},
    )


def table(
    pid: int,
    title: str,
    queries: list[dict[str, Any]],
    desc: str,
    transformations: list[dict[str, Any]] | None = None,
    overrides: list[dict[str, Any]] | None = None,
    sort_col: str | None = None,
    sort_desc: bool = True,
) -> tuple[str, dict[str, Any]]:
    return panel(
        pid,
        title,
        "table",
        queries,
        "none",
        desc,
        options={
            "cellHeight": "sm",
            "showHeader": True,
            "footer": {"show": False, "reducer": ["sum"], "fields": ""},
            "sortBy": [{"desc": sort_desc, "displayName": sort_col}] if sort_col else [],
        },
        field_defaults={
            "custom": {
                "align": "auto",
                "cellOptions": {"type": "auto"},
                "filterable": True,
                "inspect": False,
            },
            "thresholds": THRESHOLDS["neutral"],
        },
        overrides=overrides or [],
        transformations=transformations,
    )


def logs_panel(pid: int, title: str, expr: str, desc: str) -> tuple[str, dict[str, Any]]:
    return panel(
        pid,
        title,
        "logs",
        [loki_query(expr)],
        "none",
        desc,
        options={
            "dedupStrategy": "none",
            "enableLogDetails": True,
            "prettifyLogMessage": False,
            "showCommonLabels": False,
            "showLabels": False,
            "showTime": True,
            "sortOrder": "Descending",
            "wrapLogMessage": True,
        },
        field_defaults={"thresholds": THRESHOLDS["neutral"]},
    )


def text(pid: int, title: str, content: str) -> tuple[str, dict[str, Any]]:
    key = f"panel-{pid}"
    return key, {
        "kind": "Panel",
        "spec": {
            "id": pid,
            "title": title,
            "description": "",
            "links": [],
            "data": data_group([]),
            "vizConfig": {
                "kind": "VizConfig",
                "group": "text",
                "version": "",
                "spec": {
                    "options": {
                        "mode": "markdown",
                        "content": content,
                        "code": {"language": "plaintext", "showLineNumbers": False, "showMiniMap": False},
                    },
                    "fieldConfig": {"defaults": {}, "overrides": []},
                },
            },
        },
    }


def clean_table_transforms(rename: dict[str, str], keep: list[str]) -> list[dict[str, Any]]:
    noise = [
        "Time",
        "__name__",
        "Value",
        "cluster",
        "job",
        "router",
        "endpoint",
        "instance",
        "namespace",
        "prometheus",
        "prometheus_replica",
        "service",
    ]
    return [
        organize(
            exclude=noise,
            rename=rename,
            index={name: i for i, name in enumerate(keep)},
        )
    ]


def wifi_station_label_expr(expr: str) -> str:
    return f'label_replace(label_replace({expr}, "station", "$1", "mac", "(.+)"), "vif", "$1", "ifname", "(.+)")'


def wifi_station_fallback_expr(hostapd_metric: str, wifi_metric: str) -> str:
    hostapd = f'{hostapd_metric}{{{PROM_FILTER}}}'
    wifi = wifi_station_label_expr(f'{wifi_metric}{{{PROM_FILTER}}}')
    return f'{hostapd} or ({wifi} unless on(job, router) {hostapd})'


def wifi_station_rate_bps_expr(hostapd_metric: str, wifi_bytes_metric: str, wifi_kbits_metric: str) -> str:
    hostapd = f'rate({hostapd_metric}{{{PROM_FILTER}}}[$__rate_interval]) * 8'
    wifi_bytes = wifi_station_label_expr(f'rate({wifi_bytes_metric}{{{PROM_FILTER}}}[$__rate_interval]) * 8')
    wifi_kbits = wifi_station_label_expr(f'{wifi_kbits_metric}{{{PROM_FILTER}}} * 1000')
    byte_sources = f'({hostapd} or {wifi_bytes})'
    return f'{hostapd} or ({wifi_bytes} unless on(job, router) {hostapd}) or ({wifi_kbits} unless on(job, router) {byte_sources})'


WIFI_SIGNAL_RAW = wifi_station_fallback_expr("hostapd_station_signal_dbm", "wifi_station_signal_dbm")
WIFI_SIGNAL = f"max by(station, vif) ({WIFI_SIGNAL_RAW})"
WIFI_RX_BPS_RAW = wifi_station_rate_bps_expr(
    "hostapd_station_receive_bytes_total",
    "wifi_station_receive_bytes_total",
    "wifi_station_receive_kilobits_per_second",
)
WIFI_RX_BPS = f"sum by(station, vif) ({WIFI_RX_BPS_RAW})"
WIFI_TX_BPS_RAW = wifi_station_rate_bps_expr(
    "hostapd_station_transmit_bytes_total",
    "wifi_station_transmit_bytes_total",
    "wifi_station_transmit_kilobits_per_second",
)
WIFI_TX_BPS = f"sum by(station, vif) ({WIFI_TX_BPS_RAW})"
WIFI_INACTIVE_RAW = (
    f'hostapd_station_inactive_seconds{{{PROM_FILTER}}} '
    f'or ({wifi_station_label_expr(f"wifi_station_inactive_milliseconds{{{PROM_FILTER}}} / 1000")} '
    f'unless on(job, router) hostapd_station_inactive_seconds{{{PROM_FILTER}}})'
)
WIFI_INACTIVE = f"max by(station, vif) ({WIFI_INACTIVE_RAW})"
WIFI_CONNECTED_RAW = (
    f'hostapd_station_connected_seconds_total{{{PROM_FILTER}}} '
    f'or (openwrt_wifi_station_connected_seconds{{{PROM_FILTER}}} '
    f'unless on(job, router) hostapd_station_connected_seconds_total{{{PROM_FILTER}}})'
)
WIFI_CONNECTED = f"max by(station, vif) ({WIFI_CONNECTED_RAW})"
WIFI_CLIENT_COUNT = (
    f'sum by(vif) (count by(job, router, vif) (hostapd_station_signal_dbm{{{PROM_FILTER}}}) '
    f'or (label_replace(wifi_stations{{{PROM_FILTER}}}, "vif", "$1", "ifname", "(.+)") '
    f'unless on(job, router) hostapd_station_signal_dbm{{{PROM_FILTER}}}))'
)
CRON_CMD_FILTER = '!~ "^USER [^ ]+ pid [0-9]+ cmd "'


class DashboardBuilder:
    def __init__(self) -> None:
        self.elements: dict[str, dict[str, Any]] = {}

    def add(self, tab: list[dict[str, Any]], item: tuple[str, dict[str, Any]], x: int, y: int, width: int, height: int) -> None:
        key, element = item
        if key in self.elements:
            raise AssertionError(f"duplicate element key: {key}")
        self.elements[key] = element
        tab.append(
            {
                "kind": "GridLayoutItem",
                "spec": {
                    "x": x,
                    "y": y,
                    "width": width,
                    "height": height,
                    "element": {"kind": "ElementReference", "name": key},
                },
            }
        )

    def tab(self, title: str, items: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "kind": "TabsLayoutTab",
            "spec": {
                "title": title,
                "layout": {
                    "kind": "RowsLayout",
                    "spec": {
                        "rows": [
                            {
                                "kind": "RowsLayoutRow",
                                "spec": {
                                    "title": title,
                                    "collapse": False,
                                    "hideHeader": True,
                                    "layout": {"kind": "GridLayout", "spec": {"items": items}},
                                },
                            }
                        ]
                    },
                },
            },
        }


def build_dashboard() -> dict[str, Any]:
    b = DashboardBuilder()
    tabs: list[dict[str, Any]] = []

    # Overview, eight panels maximum.
    overview: list[dict[str, Any]] = []
    b.add(overview, stat(1, "Router Health", f'min(up{{{PROM_FILTER}}}) or vector(0)', "none", "Exporter target reachability. 1 means Prometheus is scraping the OpenWrt router.", mappings=STATUS_MAPPINGS, thresholds_key="ok_bad"), 0, 0, 4, 4)
    b.add(overview, stat(2, "WAN Health", f'(min(dns_probe_success{{{PROM_FILTER}}}) * (max(packet_loss{{{PROM_FILTER}}}) < bool 1)) or vector(0)', "none", "DNS probe must succeed and packet loss must stay below 1%.", mappings=BOOLEAN_OK_MAPPINGS, thresholds_key="ok_bad"), 4, 0, 4, 4)
    b.add(overview, stat(3, "Online Clients", f'sum(router_device_up{{{PROM_FILTER}}}) or vector(0)', "none", "Devices currently marked online by the router textfile collector.", thresholds_key="neutral"), 8, 0, 4, 4)
    b.add(overview, stat(4, "Conntrack Used", f'max(node_nf_conntrack_entries{{{PROM_FILTER}}} / node_nf_conntrack_entries_limit{{{PROM_FILTER}}}) or vector(0)', "percentunit", "Active conntrack sessions as a percentage of router capacity.", thresholds_key="capacity", decimals=1), 12, 0, 4, 4)
    b.add(overview, stat(5, "Resource Pressure", f'max((1 - (node_memory_MemAvailable_bytes{{{PROM_FILTER}}} / node_memory_MemTotal_bytes{{{PROM_FILTER}}})) or (node_nf_conntrack_entries{{{PROM_FILTER}}} / node_nf_conntrack_entries_limit{{{PROM_FILTER}}}) or (overlay_bytes_used{{{PROM_FILTER}}} / overlay_bytes_total{{{PROM_FILTER}}})) or vector(0)', "percentunit", "Worst current utilization across memory, conntrack, and overlay flash.", thresholds_key="capacity", decimals=1), 16, 0, 4, 4)
    b.add(overview, stat(6, "Telemetry Freshness", f'time() - max(node_textfile_mtime_seconds{{{PROM_FILTER}, file=~".*openwrt-grafana-monitor.prom"}}) or vector(999999)', "s", "Age of the custom textfile metrics. Normal cadence is roughly one minute.", thresholds_key="freshness", graph=True), 20, 0, 4, 4)
    b.add(overview, timeseries(7, "WAN Throughput", [prom_query(f'rate(node_network_receive_bytes_total{{{PROM_FILTER}, device="$wan_interface"}}[$__rate_interval])', "Download RX", "A"), prom_query(f'rate(node_network_transmit_bytes_total{{{PROM_FILTER}, device="$wan_interface"}}[$__rate_interval])', "Upload TX", "B")], "Bps", "WAN RX/TX bytes per second. Counters are converted with rate() before display.", overrides=[color_override("Download RX", GREEN), color_override("Upload TX", BLUE)]), 0, 4, 12, 8)
    b.add(overview, timeseries(8, "CPU, Memory, and Conntrack Pressure", [prom_query(f'1 - avg(rate(node_cpu_seconds_total{{{PROM_FILTER}, mode="idle"}}[$__rate_interval]))', "CPU busy", "A"), prom_query(f'1 - (node_memory_MemAvailable_bytes{{{PROM_FILTER}}} / node_memory_MemTotal_bytes{{{PROM_FILTER}}})', "Memory used", "B"), prom_query(f'node_nf_conntrack_entries{{{PROM_FILTER}}} / node_nf_conntrack_entries_limit{{{PROM_FILTER}}}', "Conntrack used", "C")], "percentunit", "The three fastest limits to check during an incident.", thresholds_value=THRESHOLDS["capacity"]), 12, 4, 12, 8)
    tabs.append(b.tab("Overview", overview))

    wan: list[dict[str, Any]] = []
    b.add(wan, text(100, "", "## WAN & Internet\nValidate internet reachability first: throughput, packet rate, interface errors, gateway loss, DNS, and public-IP changes."), 0, 0, 24, 3)
    b.add(wan, timeseries(101, "WAN Throughput RX/TX", [prom_query(f'rate(node_network_receive_bytes_total{{{PROM_FILTER}, device="$wan_interface"}}[$__rate_interval])', "RX download", "A"), prom_query(f'rate(node_network_transmit_bytes_total{{{PROM_FILTER}, device="$wan_interface"}}[$__rate_interval])', "TX upload", "B")], "Bps", "Combined WAN throughput for direct RX/TX comparison.", overrides=[color_override("RX download", GREEN), color_override("TX upload", BLUE)]), 0, 3, 12, 8)
    b.add(wan, timeseries(102, "WAN Packets/sec", [prom_query(f'rate(node_network_receive_packets_total{{{PROM_FILTER}, device="$wan_interface"}}[$__rate_interval])', "RX packets", "A"), prom_query(f'rate(node_network_transmit_packets_total{{{PROM_FILTER}, device="$wan_interface"}}[$__rate_interval])', "TX packets", "B")], "pps", "WAN packet rate. High PPS with low throughput often means small-packet workloads.", overrides=[color_override("RX packets", GREEN), color_override("TX packets", BLUE)]), 12, 3, 12, 8)
    b.add(wan, timeseries(103, "WAN Errors & Drops", [prom_query(f'rate(node_network_receive_errs_total{{{PROM_FILTER}, device="$wan_interface"}}[$__rate_interval])', "RX errors", "A"), prom_query(f'rate(node_network_receive_drop_total{{{PROM_FILTER}, device="$wan_interface"}}[$__rate_interval])', "RX drops", "B"), prom_query(f'rate(node_network_transmit_errs_total{{{PROM_FILTER}, device="$wan_interface"}}[$__rate_interval])', "TX errors", "C"), prom_query(f'rate(node_network_transmit_drop_total{{{PROM_FILTER}, device="$wan_interface"}}[$__rate_interval])', "TX drops", "D")], "pps", "WAN errors and drops. TX drops are included when exported by the node network collector.", line_interpolation="linear", overrides=[regexp_color_override("/errors|drops/i", RED)]), 0, 11, 12, 8)
    b.add(wan, timeseries(104, "Gateway Packet Loss Trend", [prom_query(f'gateway_packet_loss{{{PROM_FILTER}}}', "{{gateway}}", "A")], "percent", "Gateway packet loss as a percent. Kept separate from DNS latency so axes and units stay honest.", line_interpolation="linear", thresholds_value=THRESHOLDS["loss_percent"]), 12, 11, 6, 8)
    b.add(wan, timeseries(109, "DNS Probe Latency", [prom_query(f'dns_probe_duration_seconds{{{PROM_FILTER}}}', "{{host}}", "A")], "s", "DNS probe duration from the router textfile collector.", line_interpolation="linear", thresholds_value=THRESHOLDS["latency_seconds"]), 18, 11, 6, 8)
    b.add(wan, stat(105, "Gateway Packet Loss", f'max(gateway_packet_loss{{{PROM_FILTER}}}) or vector(0)', "percent", "Current packet loss to the IPv4 default gateway.", thresholds_key="loss_percent", graph=True), 0, 19, 6, 5)
    b.add(wan, stat(106, "DNS Probe", f'min(dns_probe_success{{{PROM_FILTER}}}) or vector(0)', "none", "Configured DNS lookup success. All configured probes must be healthy.", mappings=BOOLEAN_OK_MAPPINGS, thresholds_key="ok_bad", graph=True), 6, 19, 6, 5)
    b.add(wan, stat(107, "DNS Probe Duration", f'max(dns_probe_duration_seconds{{{PROM_FILTER}}}) or vector(0)', "s", "Worst current DNS probe latency from the router textfile collector.", thresholds_key="latency_seconds", graph=True), 12, 19, 6, 5)
    b.add(wan, stat(108, "Public IP Changed", f'max(wan_public_ip_changed{{{PROM_FILTER}}}) or vector(0)', "none", "Whether the latest public IP lookup observed a change. A change is an event, not a continuous failure.", mappings=CHANGE_MAPPINGS, thresholds_key="neutral"), 18, 19, 6, 5)
    tabs.append(b.tab("WAN & Internet", wan))

    wifi: list[dict[str, Any]] = []
    b.add(wifi, text(200, "", "## WiFi & Clients\nAP-level panels stay low-cardinality. Client MACs appear only in bounded rankings and tables. For dBm, less negative is better: -45 dBm is stronger than -70 dBm."), 0, 0, 24, 3)
    b.add(wifi, timeseries(201, "AP Throughput by Band", [prom_query(f'rate(node_network_receive_bytes_total{{{PROM_FILTER}, device=~"$wifi_ap"}}[$__rate_interval])', "RX {{device}}", "A"), prom_query(f'rate(node_network_transmit_bytes_total{{{PROM_FILTER}, device=~"$wifi_ap"}}[$__rate_interval])', "TX {{device}}", "B")], "Bps", "Measured AP interface traffic rate, not PHY/link rate.", overrides=[regexp_color_override("/RX/i", GREEN), regexp_color_override("/TX/i", BLUE)]), 0, 3, 24, 8)
    b.add(wifi, timeseries(202, "AP Signal and Noise", [prom_query(f'wifi_network_signal_dbm{{{PROM_FILTER}}}', "signal {{ssid}} {{ifname}}", "A"), prom_query(f'wifi_network_noise_dbm{{{PROM_FILTER}}}', "noise {{ssid}} {{ifname}}", "B")], "dBm", "AP signal/noise from optional WiFi collector. Less negative signal is better.", overrides=[regexp_color_override("/signal/i", GREEN), regexp_color_override("/noise/i", GRAY)]), 0, 11, 8, 7)
    b.add(wifi, timeseries(203, "AP Quality", [prom_query(f'wifi_network_quality{{{PROM_FILTER}}}', "{{ssid}} {{ifname}}", "A")], "percent", "AP quality percentage from optional WiFi collector.", thresholds_value=thresholds(("red", 0), ("yellow", 70), ("green", 85))), 8, 11, 8, 7)
    b.add(wifi, timeseries(204, "AP Bitrate", [prom_query(f'wifi_network_bitrate{{{PROM_FILTER}}} * 1000', "{{ssid}} {{ifname}}", "A")], "bps", "AP PHY bitrate. This is link capability, not measured traffic throughput.", line_interpolation="stepAfter"), 16, 11, 8, 7)
    b.add(wifi, bargauge(205, "Clients by AP", [prom_query(WIFI_CLIENT_COUNT, "{{vif}}", "A")], "none", "Neutral ranking of client counts per AP. Higher is not automatically bad.", thresholds_key="neutral", color_mode="continuous-blues"), 0, 18, 8, 7)
    b.add(wifi, bargauge(206, "Weakest Clients by Signal", [prom_query(f'bottomk(10, {WIFI_SIGNAL})', "{{station}} {{vif}}", "A")], "dBm", "Bounded bottom-10 WiFi client signal ranking. Less negative values are better.", thresholds_key="neutral", color_mode="continuous-blues"), 8, 18, 8, 7)
    b.add(wifi, bargauge(207, "Top Client Measured Traffic", [prom_query(f'topk(10, sum by(station, vif) (({WIFI_RX_BPS}) + ({WIFI_TX_BPS})))', "{{station}} {{vif}}", "A")], "bps", "Top 10 measured client traffic rate from byte counters when available. Fallback link-rate metrics are described separately.", thresholds_key="neutral", color_mode="continuous-blues"), 16, 18, 8, 7)
    b.add(wifi, table(208, "Selected Client Quality Details", [prom_query(WIFI_SIGNAL, "", "A", fmt="table", instant=True), prom_query(WIFI_CONNECTED, "", "B", fmt="table", instant=True), prom_query(WIFI_INACTIVE, "", "C", fmt="table", instant=True)], "Bounded operational table for connected WiFi stations. Collector plumbing fields are hidden.", transformations=[tf("merge"), organize(exclude=["Time", "__name__", "cluster", "job", "router", "endpoint", "instance", "namespace", "prometheus", "prometheus_replica", "service", "mac", "ifname"], rename={"station": "Station", "ssid": "SSID", "vif": "AP", "frequency": "MHz", "channel": "Channel", "Value #A": "Signal dBm", "Value #B": "Connected Seconds", "Value #C": "Inactive Seconds"}), sort_by("Signal dBm", desc=False), limit(25)], sort_col="Signal dBm", sort_desc=False), 0, 25, 24, 10)
    tabs.append(b.tab("WiFi & Clients", wifi))

    lan: list[dict[str, Any]] = []
    b.add(lan, text(300, "", "## LAN, NAT & Devices\nDevice state, DHCP leases, interface state, and NAT usage. Zero offline devices must render as 0, not No data."), 0, 0, 24, 3)
    b.add(lan, stat(301, "Online Devices", f'sum(router_device_up{{{PROM_FILTER}}}) or vector(0)', "none", "Online devices from router_device_up.", thresholds_key="neutral"), 0, 3, 6, 4)
    b.add(lan, stat(302, "Offline Devices", f'sum(1 - router_device_up{{{PROM_FILTER}}}) or vector(0)', "none", "Known devices currently offline. Zero is an explicit healthy zero.", thresholds_key="ok_bad"), 6, 3, 6, 4)
    b.add(lan, stat(303, "DHCP Leases", f'count(dhcp_lease{{{PROM_FILTER}}}) or vector(0)', "none", "Active DHCP lease records.", thresholds_key="neutral"), 12, 3, 6, 4)
    b.add(lan, stat(304, "Conntrack Used", f'max(node_nf_conntrack_entries{{{PROM_FILTER}}} / node_nf_conntrack_entries_limit{{{PROM_FILTER}}}) or vector(0)', "percentunit", "NAT/conntrack table utilization.", thresholds_key="capacity", decimals=1), 18, 3, 6, 4)
    b.add(lan, timeseries(305, "LAN Throughput", [prom_query(f'rate(node_network_receive_bytes_total{{{PROM_FILTER}, device="$lan_interface"}}[$__rate_interval])', "LAN RX", "A"), prom_query(f'rate(node_network_transmit_bytes_total{{{PROM_FILTER}, device="$lan_interface"}}[$__rate_interval])', "LAN TX", "B")], "Bps", "Measured LAN bridge throughput.", overrides=[color_override("LAN RX", GREEN), color_override("LAN TX", BLUE)]), 0, 7, 12, 8)
    b.add(lan, timeseries(306, "Online vs Offline Devices", [prom_query(f'sum(router_device_up{{{PROM_FILTER}}}) or vector(0)', "Online", "A"), prom_query(f'sum(1 - router_device_up{{{PROM_FILTER}}}) or vector(0)', "Offline", "B")], "none", "Device count trend with explicit zero-preserving offline count.", line_interpolation="stepAfter"), 12, 7, 12, 8)
    b.add(lan, bargauge(307, "Top NAT Traffic Snapshot", [prom_query(f'topk(10, sum by(src) (node_nat_traffic{{{PROM_FILTER}, src!~"0\\\\.0\\\\.0\\\\.0|::|0000.*"}}))', "{{src}}", "A")], "bytes", "Top NAT traffic snapshot by source. This is displayed as a bounded neutral ranking, not a health score.", thresholds_key="neutral", color_mode="continuous-blues"), 0, 15, 8, 8)
    b.add(lan, table(308, "Network Interface State", [prom_query(f'node_network_info{{{PROM_FILTER}}}', "", "A", fmt="table", instant=True)], "Current interface facts with MAC/broadcast and scrape plumbing hidden by default.", transformations=[organize(exclude=["Time", "__name__", "Value", "address", "broadcast", "cluster", "endpoint", "instance", "job", "namespace", "prometheus", "prometheus_replica", "router", "service"], rename={"device": "Interface", "operstate": "State", "duplex": "Duplex"}, index={"Interface": 0, "State": 1, "Duplex": 2})], overrides=[{"matcher": {"id": "byName", "options": "State"}, "properties": [{"id": "custom.cellOptions", "value": {"type": "color-background"}}, {"id": "mappings", "value": [{"type": "value", "options": {"up": {"color": GREEN, "text": "up", "index": 0}, "down": {"color": RED, "text": "down", "index": 1}, "unknown": {"color": GRAY, "text": "unknown", "index": 2}, "lowerlayerdown": {"color": GRAY, "text": "no cable", "index": 3}}}]}]}], sort_col="Interface", sort_desc=False), 8, 15, 16, 8)
    b.add(lan, table(309, "All Devices", [prom_query(f'router_device_up{{{PROM_FILTER}}}', "", "A", fmt="table", instant=True)], "All known devices with online state, hostname/IP/MAC, and no collector plumbing.", transformations=clean_table_transforms({"device": "Hostname", "status": "Status", "mac": "MAC", "ip": "IP", "Value": "Online"}, ["Hostname", "Status", "IP", "MAC", "Online"]), overrides=[{"matcher": {"id": "byName", "options": "Online"}, "properties": [{"id": "custom.cellOptions", "value": {"type": "color-background"}}, {"id": "mappings", "value": DEVICE_STATE_MAPPINGS}]}], sort_col="Online", sort_desc=True), 0, 23, 24, 10)
    b.add(lan, table(310, "DHCP Lease Expiry", [prom_query(f'(dhcp_lease{{{PROM_FILTER}}} > 0) * 1000', "", "A", fmt="table", instant=True), prom_query(f'clamp_min((dhcp_lease{{{PROM_FILTER}}} > 0) - time(), 0)', "", "B", fmt="table", instant=True)], "Expiring DHCP leases only. Static/infinite leases exported as 0 are omitted so they do not render as impossible negative durations.", transformations=[tf("merge"), organize(exclude=["Time", "__name__", "cluster", "job", "router", "endpoint", "instance", "namespace", "prometheus", "prometheus_replica", "service", "dnsmasq", "Value"], rename={"hostname": "Hostname", "mac": "MAC", "ip": "IP", "Value #A": "Lease Expires", "Value #B": "Remaining Seconds"}), sort_by("Remaining Seconds", desc=False)], overrides=[{"matcher": {"id": "byName", "options": "Lease Expires"}, "properties": [{"id": "unit", "value": "dateTimeAsIso"}]}, {"matcher": {"id": "byName", "options": "Remaining Seconds"}, "properties": [{"id": "unit", "value": "s"}]}], sort_col="Remaining Seconds", sort_desc=False), 0, 33, 24, 10)
    tabs.append(b.tab("LAN, NAT & Devices", lan))

    health: list[dict[str, Any]] = []
    b.add(health, text(400, "", "## Router Health\nCapacity and exporter health. Thresholds are tied to actual utilization or scrape freshness; no decorative limits."), 0, 0, 24, 3)
    b.add(health, gauge(401, "CPU Busy", f'1 - avg(rate(node_cpu_seconds_total{{{PROM_FILTER}, mode="idle"}}[$__rate_interval]))', "CPU busy fraction across all cores."), 0, 3, 6, 7)
    b.add(health, gauge(402, "Memory Used", f'1 - (node_memory_MemAvailable_bytes{{{PROM_FILTER}}} / node_memory_MemTotal_bytes{{{PROM_FILTER}}})', "Memory utilization using MemAvailable."), 6, 3, 6, 7)
    b.add(health, gauge(403, "Overlay Flash Used", f'overlay_bytes_used{{{PROM_FILTER}}} / overlay_bytes_total{{{PROM_FILTER}}}', "Writable overlay/rootfs_data utilization."), 12, 3, 6, 7)
    b.add(health, gauge(404, "File Descriptors Used", f'node_filefd_allocated{{{PROM_FILTER}}} / node_filefd_maximum{{{PROM_FILTER}}}', "Open file descriptor utilization."), 18, 3, 6, 7)
    b.add(health, timeseries(405, "CPU Usage by Mode", [prom_query(f'sum(rate(node_cpu_seconds_total{{{PROM_FILTER}, mode="user"}}[$__rate_interval]))', "user", "A"), prom_query(f'sum(rate(node_cpu_seconds_total{{{PROM_FILTER}, mode="system"}}[$__rate_interval]))', "system", "B"), prom_query(f'sum(rate(node_cpu_seconds_total{{{PROM_FILTER}, mode="softirq"}}[$__rate_interval]))', "softirq", "C"), prom_query(f'sum(rate(node_cpu_seconds_total{{{PROM_FILTER}, mode="iowait"}}[$__rate_interval]))', "iowait", "D")], "none", "CPU time by mode. softirq is normal router packet processing.", stacked=True, fill=45), 0, 10, 12, 8)
    b.add(health, timeseries(406, "Load Average", [prom_query(f'node_load1{{{PROM_FILTER}}}', "1 min", "A"), prom_query(f'node_load5{{{PROM_FILTER}}}', "5 min", "B"), prom_query(f'node_load15{{{PROM_FILTER}}}', "15 min", "C")], "none", "System load averages. Compare with router CPU thread count for saturation.", line_interpolation="linear"), 12, 10, 12, 8)
    b.add(health, timeseries(407, "Memory Breakdown", [prom_query(f'node_memory_MemTotal_bytes{{{PROM_FILTER}}} - node_memory_MemAvailable_bytes{{{PROM_FILTER}}}', "Used", "A"), prom_query(f'node_memory_MemAvailable_bytes{{{PROM_FILTER}}}', "Available", "B"), prom_query(f'node_memory_Buffers_bytes{{{PROM_FILTER}}} + node_memory_Cached_bytes{{{PROM_FILTER}}}', "Buffers+Cache", "C")], "bytes", "RAM breakdown. Buffers/cache are generally reclaimable.", stacked=True, fill=40), 0, 18, 12, 8)
    b.add(health, timeseries(408, "Conntrack Sessions vs Limit", [prom_query(f'node_nf_conntrack_entries{{{PROM_FILTER}}}', "Active sessions", "A"), prom_query(f'node_nf_conntrack_entries_limit{{{PROM_FILTER}}}', "Limit", "B")], "none", "Conntrack active sessions and configured limit. Utilization percentage is shown in the gauges and hero stats.", overrides=[color_override("Limit", YELLOW)]), 12, 18, 12, 8)
    b.add(health, stat(409, "CPU Temperature", f'max(node_thermal_zone_temp{{{PROM_FILTER}}}) or max(node_hwmon_temp_celsius{{{PROM_FILTER}}})', "celsius", "Temperature from optional thermal/hwmon collectors.", thresholds_key="neutral", graph=True), 0, 26, 6, 5)
    b.add(health, stat(410, "Reboots in Range", f'max(changes(node_boot_time_seconds{{{PROM_FILTER}}}[$__range])) or vector(0)', "none", "Boot-time changes in the selected time range.", thresholds_key="ok_bad"), 6, 26, 6, 5)
    b.add(health, stat(411, "Failed Collectors", f'sum(1 - node_scrape_collector_success{{{PROM_FILTER}}}) or vector(0)', "none", "Exporter collectors reporting failure. Zero is explicit.", thresholds_key="ok_bad"), 12, 26, 6, 5)
    b.add(health, stat(412, "Textfile Age", f'time() - max(node_textfile_mtime_seconds{{{PROM_FILTER}, file=~".*openwrt-grafana-monitor.prom"}}) or vector(999999)', "s", "Age of custom textfile metrics.", thresholds_key="freshness", graph=True), 18, 26, 6, 5)
    b.add(health, table(413, "Router Firmware", [prom_query(f'node_openwrt_info{{{PROM_FILTER}}}', "", "A", fmt="table", instant=True)], "Firmware and hardware identity without scrape plumbing.", transformations=[organize(exclude=["Time", "__name__", "Value", "cluster", "endpoint", "instance", "job", "namespace", "prometheus", "prometheus_replica", "router", "service"], rename={"board_name": "Board", "model": "Model", "release": "OpenWrt Release", "revision": "Revision", "target": "Target", "system": "CPU"}, index={"Board": 0, "Model": 1, "OpenWrt Release": 2, "Revision": 3, "Target": 4, "CPU": 5})]), 0, 31, 12, 6)
    b.add(health, table(414, "WAN Identity", [prom_query(f'wan_info{{{PROM_FILTER}}}', "", "A", fmt="table", instant=True)], "WAN IP, public IP, and hostname from this repo's textfile collector.", transformations=[organize(exclude=["Time", "__name__", "Value", "cluster", "endpoint", "instance", "job", "namespace", "prometheus", "prometheus_replica", "router", "service"], rename={"hostname": "Hostname", "publicip": "Public IP", "wanip": "WAN IP"}, index={"Hostname": 0, "WAN IP": 1, "Public IP": 2})]), 12, 31, 12, 6)
    tabs.append(b.tab("Router Health", health))

    logs: list[dict[str, Any]] = []
    b.add(logs, text(500, "", "## Logs & Security\nSummary stats use scalar LogQL aggregation. BusyBox cron command-start records can carry error severity while the command exits normally; those records are excluded from error/warning summaries and explained here."), 0, 0, 24, 3)
    b.add(logs, loki_stat(501, "Errors (1h)", f'sum(count_over_time({{{LOKI_FILTER}, message_severity=~"error|err|crit|critical|alert|emergency|emerg"}} {CRON_CMD_FILTER} [1h])) or vector(0)', "Syslog severity error/critical lines excluding normal BusyBox cron command-start records.", thresholds_key="ok_bad"), 0, 3, 4, 4)
    b.add(logs, loki_stat(502, "Warnings (1h)", f'sum(count_over_time({{{LOKI_FILTER}, message_severity=~"warning|warn"}} {CRON_CMD_FILTER} [1h])) or vector(0)', "Syslog warning lines excluding cron command-start noise.", thresholds_key="ok_bad"), 4, 3, 4, 4)
    b.add(logs, loki_stat(503, "DHCP Events (1h)", f'sum(count_over_time({{{LOKI_FILTER}}} |~ "DHCP|dhcp|dnsmasq|odhcpd" [1h])) or vector(0)', "DHCP/dnsmasq/odhcpd log events.", thresholds_key="neutral"), 8, 3, 4, 4)
    b.add(logs, loki_stat(504, "Firewall Drops (1h)", f'sum(count_over_time({{{LOKI_FILTER}}} |~ "\\\\b(DROP|REJECT|drop|reject)\\\\b" [1h])) or vector(0)', "Firewall drop/reject events when firewall logging is enabled.", thresholds_key="ok_bad"), 12, 3, 4, 4)
    b.add(logs, loki_stat(505, "SSH Failures (1h)", f'sum(count_over_time({{{LOKI_FILTER}}} |~ "(Failed password|failed password|login failed|Bad password|Invalid user)" [1h])) or vector(0)', "Precise SSH authentication failure patterns; does not match ordinary 'fails: Exited normally' text.", thresholds_key="ok_bad"), 16, 3, 4, 4)
    b.add(logs, loki_stat(506, "Total Log Lines (1h)", f'sum(count_over_time({{{LOKI_FILTER}}} [1h])) or vector(0)', "One aggregate count across the Loki stream.", thresholds_key="neutral"), 20, 3, 4, 4)
    b.add(logs, timeseries(507, "Log Rate by Severity", [loki_query(f'sum by(message_severity) (rate({{{LOKI_FILTER}}} {CRON_CMD_FILTER} [$__auto]))')], "none", "Rate of non-cron syslog lines grouped by Alloy syslog severity label. Loki metric queries use Grafana's $__auto interval.", line_interpolation="linear"), 0, 7, 24, 7)
    b.add(logs, logs_panel(508, "Recent System Logs", f'{{{LOKI_FILTER}}}', "Bounded raw OpenWrt syslog stream. Use Explore for unrestricted investigation."), 0, 14, 24, 10)
    b.add(logs, logs_panel(509, "DHCP / DNSMasq / odhcpd", f'{{{LOKI_FILTER}}} |~ "DHCP|dhcp|dnsmasq|odhcpd"', "Lease assignments, renewals, releases, and DHCPv6/RA activity."), 0, 24, 12, 6)
    b.add(logs, logs_panel(510, "Firewall DROP / REJECT", f'{{{LOKI_FILTER}}} |~ "\\\\b(DROP|REJECT|drop|reject)\\\\b"', "Firewall events. Empty is normal unless logging is enabled."), 12, 24, 12, 6)
    b.add(logs, logs_panel(511, "Kernel / Driver", f'{{{LOKI_FILTER}}} |~ "kernel|Kernel|netifd|device|link is|entered forwarding|left promiscuous"', "Kernel, netifd, and link-state events."), 0, 30, 12, 6)
    b.add(logs, logs_panel(512, "SSH Security Events", f'{{{LOKI_FILTER}}} |~ "(Failed password|failed password|login failed|Bad password|Invalid user|Connection closed by authenticating user)"', "SSH authentication and pre-auth close events. Normal '0 fails: Exited normally' cron text is intentionally not classified as failure."), 12, 30, 12, 6)
    tabs.append(b.tab("Logs & Security", logs))

    opt: list[dict[str, Any]] = []
    b.add(opt, text(600, "", "## Optional & Diagnostics\nOptional collectors live here. Missing metrics render as Not collected/Unavailable instead of green failures or large blank Overview panels."), 0, 0, 24, 3)
    optionals = [
        (601, "mwan3", f'max(present_over_time(mwan3_interface_up{{{PROM_FILTER}}}[$__range])) or vector(0)'),
        (602, "SQM / Cake", f'max(present_over_time(sqm_backlog_bytes{{{PROM_FILTER}}}[$__range])) or vector(0)'),
        (603, "nftables", f'max(present_over_time(nft_counter_packets{{{PROM_FILTER}}}[$__range])) or vector(0)'),
        (604, "IPv6 snmp6", f'max(present_over_time(snmp6_Ip6InReceives{{{PROM_FILTER}}}[$__range])) or vector(0)'),
        (605, "Tailscale", f'max(present_over_time(node_network_receive_bytes_total{{{PROM_FILTER}, device="$vpn_interface"}}[$__range])) or vector(0)'),
        (606, "WiFi Optional", f'max(present_over_time(wifi_network_quality{{{PROM_FILTER}}}[$__range])) or vector(0)'),
    ]
    for i, (pid, title, expr) in enumerate(optionals):
        b.add(opt, stat(pid, title, expr, "none", f"Availability of optional {title} metrics in the selected range.", mappings=AVAILABILITY_MAPPINGS, thresholds_key="unavailable", color_mode="value"), i * 4, 3, 4, 3)
    b.add(opt, table(607, "mwan3 Interface Status", [prom_query(f'mwan3_interface_status{{{PROM_FILTER}}}', "", "A", fmt="table", instant=True), prom_query(f'mwan3_interface_score{{{PROM_FILTER}}}', "", "B", fmt="table", instant=True), prom_query(f'mwan3_interface_up{{{PROM_FILTER}}}', "", "C", fmt="table", instant=True)], "Optional mwan3 status. Empty means mwan3 is not installed or not collected.", transformations=[tf("merge"), organize(exclude=["Time", "__name__", "cluster", "job", "router", "endpoint", "instance", "namespace", "prometheus", "prometheus_replica", "service"], rename={"interface": "Interface", "status": "Status", "Value #A": "Status Value", "Value #B": "Score", "Value #C": "Up"})], sort_col="Interface", sort_desc=False), 0, 6, 12, 7)
    b.add(opt, timeseries(608, "SQM / Cake Backlog", [prom_query(f'sqm_backlog_bytes{{{PROM_FILTER}}}', "{{iface}} {{direction}}", "A")], "bytes", "Optional SQM/cake backlog bytes. Missing data means SQM collection is disabled or unavailable.", line_interpolation="linear"), 12, 6, 6, 7)
    b.add(opt, timeseries(613, "SQM / Cake Drops", [prom_query(f'rate(sqm_dropped_packets_total{{{PROM_FILTER}}}[$__rate_interval])', "{{iface}} {{direction}} drops", "A"), prom_query(f'rate(sqm_overlimits_total{{{PROM_FILTER}}}[$__rate_interval])', "{{iface}} {{direction}} overlimits", "B")], "pps", "Optional SQM/cake drop and overlimit packet rates.", line_interpolation="linear"), 18, 6, 6, 7)
    b.add(opt, timeseries(609, "nftables Named Counter Rate", [prom_query(f'sum by(name) (rate(nft_counter_packets{{{PROM_FILTER}}}[$__rate_interval]))', "{{name}}", "A")], "pps", "Bounded named nftables counters only. Avoid source/destination/port-parameterized counters.", line_interpolation="linear"), 0, 13, 8, 6)
    b.add(opt, timeseries(610, "IPv6 Packets and Discards", [prom_query(f'rate(snmp6_Ip6InReceives{{{PROM_FILTER}}}[$__rate_interval])', "IPv6 in {{device}}", "A"), prom_query(f'rate(snmp6_Ip6OutRequests{{{PROM_FILTER}}}[$__rate_interval])', "IPv6 out {{device}}", "B"), prom_query(f'rate(snmp6_Ip6InDiscards{{{PROM_FILTER}}}[$__rate_interval])', "IPv6 in discards {{device}}", "C"), prom_query(f'rate(snmp6_Ip6OutDiscards{{{PROM_FILTER}}}[$__rate_interval])', "IPv6 out discards {{device}}", "D")], "pps", "Optional snmp6 IPv6 counters. Zero discards are normal.", line_interpolation="linear"), 8, 13, 8, 6)
    b.add(opt, timeseries(611, "Tailscale Interface Throughput", [prom_query(f'rate(node_network_receive_bytes_total{{{PROM_FILTER}, device="$vpn_interface"}}[$__rate_interval])', "VPN RX", "A"), prom_query(f'rate(node_network_transmit_bytes_total{{{PROM_FILTER}, device="$vpn_interface"}}[$__rate_interval])', "VPN TX", "B")], "Bps", "Optional VPN interface traffic. Missing data means the configured interface is absent.", line_interpolation="linear"), 16, 13, 8, 6)
    b.add(opt, table(612, "Metric Inventory", [prom_query(f'count by(__name__) ({{{PROM_FILTER}}})', "", "A", fmt="table", instant=True)], "Bounded Prometheus metric inventory for diagnostics, kept out of Overview.", transformations=[organize(exclude=["Time"], rename={"__name__": "Metric", "Value": "Series Count"}), sort_by("Metric", desc=False), limit(200)], sort_col="Metric", sort_desc=False), 0, 19, 24, 9)
    tabs.append(b.tab("Optional & Diagnostics", opt))

    spec = {
        "title": "OpenWRT — Operations",
        "description": "Single-tabbed OpenWrt operations dashboard for router health, WAN, WiFi, devices, capacity, logs, and optional collectors.",
        "tags": ["openwrt", "operations", "router"],
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
        "metadata": {"name": "openwrt-operations"},
        "spec": spec,
    }
    validate_dashboard(dashboard)
    return dashboard


def datasource_var(name: str, label: str, plugin_id: str, current: str) -> dict[str, Any]:
    return {
        "kind": "DatasourceVariable",
        "spec": {
            "name": name,
            "label": label,
            "pluginId": plugin_id,
            "refresh": "onDashboardLoad",
            "regex": "",
            "current": {"text": current, "value": current},
            "options": [],
            "multi": False,
            "includeAll": False,
            "hide": "dontHide",
            "skipUrlSync": False,
            "allowCustomValue": True,
        },
    }


def query_var(
    name: str,
    label: str,
    query: str,
    current: str,
    include_all: bool = False,
    multi: bool = False,
    all_value: str = ".*",
) -> dict[str, Any]:
    return {
        "kind": "QueryVariable",
        "spec": {
            "name": name,
            "label": label,
            "current": {"text": ["All"], "value": ["$__all"]} if include_all and current == "All" else {"text": current, "value": current},
            "hide": "dontHide",
            "refresh": "onDashboardLoad",
            "skipUrlSync": False,
            "query": {
                "kind": "DataQuery",
                "group": "prometheus",
                "version": "v0",
                "datasource": {"name": PROM_DS},
                "spec": {"query": query, "refId": "Q"},
            },
            "regex": "",
            "sort": "naturalAsc",
            "definition": query,
            "options": [],
            "multi": multi,
            "includeAll": include_all,
            "allValue": all_value if include_all else "",
            "allowCustomValue": True,
        },
    }


def variables() -> list[dict[str, Any]]:
    return [
        datasource_var("DS_PROMETHEUS", "Prometheus", "prometheus", "prometheus"),
        datasource_var("DS_LOKI", "Loki", "loki", "loki"),
        query_var("router", "Router", 'label_values(node_load1{job="openwrt"}, router)', "openwrt", include_all=True, multi=True),
        query_var("wan_interface", "WAN interface", 'label_values(node_network_info{job="openwrt", router=~"$router"}, device)', "wan"),
        query_var("lan_interface", "LAN interface", 'label_values(node_network_info{job="openwrt", router=~"$router"}, device)', "br-lan"),
        query_var("wifi_ap", "WiFi AP interface", 'label_values(node_network_info{job="openwrt", router=~"$router", device=~"phy.*-ap.*|wlan.*"}, device)', "All", include_all=True, multi=True, all_value="phy.*-ap.*|wlan.*"),
        query_var("vpn_interface", "VPN interface", 'label_values(node_network_info{job="openwrt", router=~"$router"}, device)', "tailscale0"),
    ]


def iter_strings(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, str):
        found.append(value)
    elif isinstance(value, dict):
        for v in value.values():
            found.extend(iter_strings(v))
    elif isinstance(value, list):
        for v in value:
            found.extend(iter_strings(v))
    return found


def layout_refs(layout: Any) -> list[str]:
    refs: list[str] = []
    if isinstance(layout, dict):
        if layout.get("kind") == "ElementReference" and "name" in layout:
            refs.append(layout["name"])
        for v in layout.values():
            refs.extend(layout_refs(v))
    elif isinstance(layout, list):
        for v in layout:
            refs.extend(layout_refs(v))
    return refs


def grid_items_by_tab(layout: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for tab in layout["spec"]["tabs"]:
        title = tab["spec"]["title"]
        items: list[dict[str, Any]] = []
        rows = tab["spec"]["layout"]["spec"]["rows"]
        for row in rows:
            items.extend(row["spec"]["layout"]["spec"]["items"])
        result[title] = items
    return result


def validate_dashboard(dash: dict[str, Any]) -> None:
    assert dash["apiVersion"] == "dashboard.grafana.app/v2beta1"
    assert dash["kind"] == "Dashboard"
    assert dash["metadata"]["name"] == "openwrt-operations"
    spec = dash["spec"]
    assert spec["title"] == "OpenWRT — Operations"
    assert spec["layout"]["kind"] == "TabsLayout"
    expected_tabs = [
        "Overview",
        "WAN & Internet",
        "WiFi & Clients",
        "LAN, NAT & Devices",
        "Router Health",
        "Logs & Security",
        "Optional & Diagnostics",
    ]
    actual_tabs = [t["spec"]["title"] for t in spec["layout"]["spec"]["tabs"]]
    assert actual_tabs == expected_tabs, actual_tabs

    elements = spec["elements"]
    refs = layout_refs(spec["layout"])
    assert set(refs) == set(elements), f"orphan/missing refs: refs={len(refs)} elements={len(elements)}"
    duplicate_refs = sorted({ref for ref in refs if refs.count(ref) > 1})
    assert not duplicate_refs, f"duplicate layout refs: {duplicate_refs}"

    panel_ids: list[int] = []
    for key, element in elements.items():
        assert key.startswith("panel-")
        panel_spec = element["spec"]
        panel_ids.append(panel_spec["id"])
        assert key == f"panel-{panel_spec['id']}"
        viz = panel_spec["vizConfig"]["group"]
        if viz not in {"text", "logs"}:
            defaults = panel_spec["vizConfig"]["spec"]["fieldConfig"]["defaults"]
            assert "unit" in defaults, f"missing unit: {key} {panel_spec['title']}"
            assert defaults["unit"] != "short", f"generic short unit: {key} {panel_spec['title']}"
        assert "pluginVersion" not in json.dumps(element)
    assert len(panel_ids) == len(set(panel_ids)), "duplicate panel ids"

    for tab_title, items in grid_items_by_tab(spec["layout"]).items():
        rects: list[tuple[int, int, int, int, str]] = []
        for item in items:
            ispec = item["spec"]
            x, y, w, h = ispec["x"], ispec["y"], ispec["width"], ispec["height"]
            name = ispec["element"]["name"]
            assert 0 <= x <= 23, f"{tab_title} {name} invalid x={x}"
            assert y >= 0, f"{tab_title} {name} invalid y={y}"
            assert 1 <= w <= 24 and h > 0, f"{tab_title} {name} invalid size {w}x{h}"
            assert x + w <= 24, f"{tab_title} {name} overflows 24-col grid"
            rect = (x, y, x + w, y + h, name)
            for other in rects:
                if rect[0] < other[2] and other[0] < rect[2] and rect[1] < other[3] and other[1] < rect[3]:
                    raise AssertionError(f"{tab_title} overlap: {name} with {other[4]}")
            rects.append(rect)

    defined_vars = {v["spec"]["name"] for v in spec["variables"]}
    globals_allowed = {
        "__rate_interval",
        "__range",
        "__from",
        "__to",
        "__all",
        "__value",
        "__interval",
        "__auto",
    }
    referenced_vars: set[str] = set()
    var_pattern = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?")
    for s in iter_strings(spec):
        for match in var_pattern.finditer(s):
            referenced_vars.add(match.group(1))
    undefined = referenced_vars - defined_vars - globals_allowed
    assert not undefined, f"undefined variables: {sorted(undefined)}"

    exempt_unused = {"DS_PROMETHEUS", "DS_LOKI"}
    unused = defined_vars - referenced_vars - exempt_unused
    assert not unused, f"unused variables: {sorted(unused)}"

    text = json.dumps(dash, sort_keys=True)
    assert "schemaVersion" not in text
    assert "pluginVersion" not in text
    assert "uid" not in dash["metadata"]
    assert PROM_DS in text and LOKI_DS in text


def stable_json(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, sort_keys=False, ensure_ascii=False) + "\n"


def main() -> None:
    dashboard = build_dashboard()
    rendered = stable_json(dashboard)
    parsed = json.loads(rendered)
    assert parsed == dashboard

    OUT.parent.mkdir(parents=True, exist_ok=True)
    old = OUT.read_text(encoding="utf-8") if OUT.exists() else None
    OUT.write_text(rendered, encoding="utf-8")
    new = OUT.read_text(encoding="utf-8")
    assert new == rendered
    second = stable_json(build_dashboard())
    assert second == rendered, "non-deterministic output"
    digest = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
    changed = "updated" if old != rendered else "unchanged"
    print(f"{OUT}: {changed}, panels={len(dashboard['spec']['elements'])}, sha256={digest}")


if __name__ == "__main__":
    main()
