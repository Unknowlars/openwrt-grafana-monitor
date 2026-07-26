#!/usr/bin/env python3
"""Build the Grafana v2beta1 "OpenWrt - Mission Control" dashboard.

The generated JSON files are artifacts. This script is the source of truth.
It writes both the manual-import export and the provisioned dashboard copy.

Why this dashboard exists
-------------------------
It replaces eight dashboards (four classic v1, four v2beta1) totalling ~298
panels with one tabbed dashboard of 78 panels. The reduction is not cosmetic:
"Overview" existed five separate times, "Data Quality" three times, and the
client/traffic story was split across three dashboards. Every query here was
verified against the live VictoriaMetrics instance before it was written; the
metric families the old dashboards leaned on that no longer exist at all
(hostapd_station_*, wifi_usteer_*, openwrt_dpi_application_*, nft_counter_*,
node_thermal_zone_temp, openwrt_firewall_drop_packets_total, ...) are simply
gone rather than carried forward behind `or` fallback chains.

Deliberate deviations from the four older generators in this repo
-----------------------------------------------------------------
1. Local THRESHOLDS with a `None` base step. The older generators emit
   `{"color": "green", "value": 0}` as the first step, which leaves negative
   values (signal dBm, noise dBm, node_network_speed_bytes) with no matching
   step and therefore uncoloured. The Base step means -inf and must be null.
2. Every panel sets `queryOptions.interval` to the 30s scrape interval and a
   deliberate `maxDataPoints`. Verified against the v2beta1 QueryOptionsSpec
   (timeFrom/maxDataPoints/timeShift/queryCachingTTL/interval/cacheTimeout/
   hideTimeOverride/timeCompare).
3. Every `node_*` query carries `job="openwrt"`. Unfiltered, those metric
   names also match this homelab's Kubernetes node-exporters -- which is how
   the old "CPU Temperature" panel came to read an NVMe sensor at 79 C.
4. State-timeline value mappings live in `fieldConfig.defaults.mappings`,
   never in a `byType` override (those silently fail on state timelines).

Known data limitations, encoded honestly rather than papered over
-----------------------------------------------------------------
* Metric retention is ~2 days. No week-over-week `offset 1w`, no 30-day error
  budget, no monthly per-device data cap. The availability SLI window is 24h.
* MAC addresses arrive uniformly lowercase. Upstream collectors
  (`wifi_station_*`, `hostapd_station_*`, `dhcp_lease`, `uci_dhcp_host`) emit
  them UPPERCASE and this repository's own collectors emit them lowercase;
  PromQL has no lowercase function, so `alloy/config.alloy` normalises the
  `mac`, `station` and `bssid` labels at ingest instead. Joins across the two
  families are therefore possible now -- but only for data ingested after that
  rule landed, so historical series keep their original case.
* There is no firewall drop/reject counter -- the chains are FORWARD, INPUT,
  OUTPUT, PRE/POSTROUTING and mwan3_*. Firewall drops come from syslog only.
* The node graph carries `mainstat` on both frames but no `secondarystat`,
  `thickness`, `color` or `highlighted`: a Prometheus table query yields
  exactly one numeric column per frame, and Grafana's transformation chain is
  linear across every frame in the panel, so a second numeric column cannot be
  added to one frame without corrupting the other. See NODES_EXPR below.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .build_openwrt_operations_dashboard import (
    BLUE,
    GRAY,
    GREEN,
    ORANGE,
    PURPLE,
    RED,
    YELLOW,
    DashboardBuilder,
    datasource_var,
    deep,
    iter_strings,
    limit,
    loki_query,
    organize,
    prom_query,
    query_var,
    sort_by,
    stable_json,
    tf,
)


ROOT = Path(__file__).resolve().parents[1]
OUTS = [
    ROOT / "grafana-dashboard-exports/openwrt-mission-control.json",
    ROOT / "grafana/provisioning/dashboards/openwrt-mission-control.json",
]

DASHBOARD_NAME = "openwrt-mission-control"
DASHBOARD_TITLE = "OpenWrt - Mission Control"

PROM_DS = "${DS_PROMETHEUS}"
LOKI_DS = "${DS_LOKI}"
PROM_FILTER = 'job="openwrt", router=~"$router"'
LOKI_FILTER = 'job="openwrt-syslog", router=~"$router"'
ALLOY_FILTER = 'job="alloy-openwrt-metrics"'

# SCRAPE_INTERVAL in .env defaults to 30s. Min interval must never ask the
# datasource for finer resolution than actually exists.
MIN_INTERVAL = "30s"

# BusyBox cron logs every command start at notice/info level. It is the single
# noisiest line class on an idle router and drowns real events.
CRON_CMD_FILTER = '!~ "^USER [^ ]+ pid [0-9]+ cmd "'

TAB_TITLES = [
    "🏠 Overview",
    "🌐 WAN & Internet",
    "📶 WiFi & Clients",
    "🔀 LAN, Devices & Traffic",
    "🧭 DNS & DHCP",
    "❤️ Router Health",
    "🕸 Topology",
    "📜 Logs & Security",
    "🔬 Diagnostics",
]


# ---------------------------------------------------------------------------
# Thresholds, mappings, overrides
# ---------------------------------------------------------------------------


def thresholds(*steps: tuple[str, float | None]) -> dict[str, Any]:
    """Build a threshold set. The first step MUST use None (the Base step)."""
    assert steps and steps[0][1] is None, "base threshold step must be None (-inf)"
    return {
        "mode": "absolute",
        "steps": [{"color": color, "value": value} for color, value in steps],
    }


THRESHOLDS = {
    # Neutral/informational: no value is "bad".
    "neutral": thresholds((BLUE, None)),
    "info": thresholds((PURPLE, None)),
    # 1 = healthy, 0 = broken.
    "ok_bad": thresholds((RED, None), (GREEN, 1)),
    # Counts where any non-zero value warrants attention.
    "zero_good": thresholds((GREEN, None), (ORANGE, 1)),
    "zero_good_hard": thresholds((GREEN, None), (RED, 1)),
    # 0-100 percent utilisation: high is pressure.
    "capacity_percent": thresholds((GREEN, None), (YELLOW, 70), (RED, 90)),
    # Availability percent: high is good, so the ramp runs the other way.
    "availability": thresholds((RED, None), (YELLOW, 99), (GREEN, 99.9)),
    # Round-trip latency in milliseconds on a domestic uplink.
    "latency_ms": thresholds((GREEN, None), (YELLOW, 50), (RED, 150)),
    # Packet loss percent.
    "loss_percent": thresholds((GREEN, None), (YELLOW, 1), (RED, 5)),
    # WiFi RSSI: -67 dBm is the usual "good for voice/video" line, -75 the
    # usual "expect problems" line. Ascending steps, so red is the base.
    "signal_dbm": thresholds((RED, None), (YELLOW, -75), (GREEN, -67)),
    # MT7621 SoC and its radios. 85 C is where throttling becomes likely.
    "temperature": thresholds((GREEN, None), (YELLOW, 75), (RED, 85)),
    # mwan3 tracking score, 0..10.
    "score": thresholds((RED, None), (YELLOW, 5), (GREEN, 9)),
    # Textfile helper freshness. The slowest helpers run on a 10 minute cron,
    # so anything under one full slow cycle plus jitter is healthy.
    "freshness": thresholds((GREEN, None), (YELLOW, 660), (RED, 900)),
    # Scrape duration for a router exporter.
    "scrape_seconds": thresholds((GREEN, None), (YELLOW, 5), (RED, 15)),
}

STATUS_MAPPINGS = [
    {"type": "value", "options": {"1": {"text": "🟢 Healthy", "color": GREEN, "index": 0}}},
    {"type": "value", "options": {"0": {"text": "🔴 Down", "color": RED, "index": 1}}},
    {"type": "special", "options": {"match": "null", "result": {"text": "No data", "color": GRAY, "index": 2}}},
    {"type": "special", "options": {"match": "nan", "result": {"text": "n/a", "color": GRAY, "index": 3}}},
]

ONLINE_MAPPINGS = [
    {"type": "value", "options": {"1": {"text": "Online", "color": GREEN, "index": 0}}},
    {"type": "value", "options": {"0": {"text": "Offline", "color": GRAY, "index": 1}}},
    {"type": "special", "options": {"match": "null", "result": {"text": "Unknown", "color": GRAY, "index": 2}}},
]

RUNNING_MAPPINGS = [
    {"type": "value", "options": {"1": {"text": "Running", "color": GREEN, "index": 0}}},
    {"type": "value", "options": {"0": {"text": "Stopped", "color": RED, "index": 1}}},
    {"type": "special", "options": {"match": "null", "result": {"text": "Unknown", "color": GRAY, "index": 2}}},
]

REACHABLE_MAPPINGS = [
    {"type": "value", "options": {"1": {"text": "Reachable", "color": GREEN, "index": 0}}},
    {"type": "value", "options": {"0": {"text": "Unreachable", "color": RED, "index": 1}}},
    {"type": "special", "options": {"match": "null", "result": {"text": "Not probed", "color": GRAY, "index": 2}}},
]

# "Enabled at boot" is a configuration fact, not a health signal: a disabled
# optional daemon is not a fault, so it gets neutral grey, never red.
ENABLED_MAPPINGS = [
    {"type": "value", "options": {"1": {"text": "Yes", "color": GREEN, "index": 0}}},
    {"type": "value", "options": {"0": {"text": "No", "color": GRAY, "index": 1}}},
]

COLLECTOR_MAPPINGS = [
    {"type": "value", "options": {"1": {"text": "Collected", "color": GREEN, "index": 0}}},
    {"type": "value", "options": {"0": {"text": "Not collected", "color": GRAY, "index": 1}}},
    {"type": "special", "options": {"match": "null", "result": {"text": "Not collected", "color": GRAY, "index": 2}}},
]

LINK_UP_MAPPINGS = [
    {"type": "value", "options": {"1": {"text": "Up", "color": GREEN, "index": 0}}},
    {"type": "value", "options": {"0": {"text": "Down", "color": GRAY, "index": 1}}},
]

DUPLEX_MAPPINGS = [
    {"type": "value", "options": {"2": {"text": "Full", "color": GREEN, "index": 0}}},
    {"type": "value", "options": {"1": {"text": "Half", "color": YELLOW, "index": 1}}},
    {"type": "value", "options": {"0": {"text": "Unknown", "color": GRAY, "index": 2}}},
]

# One severity palette for the whole dashboard.
SEVERITY_COLOURS = {
    "emergency": RED,
    "alert": RED,
    "critical": RED,
    "error": ORANGE,
    "warning": YELLOW,
    "notice": BLUE,
    "informational": GREEN,
    "debug": GRAY,
}


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


def name_override(name: str, properties: list[dict[str, Any]]) -> dict[str, Any]:
    return {"matcher": {"id": "byName", "options": name}, "properties": properties}


def regexp_override(pattern: str, properties: list[dict[str, Any]]) -> dict[str, Any]:
    return {"matcher": {"id": "byRegexp", "options": pattern}, "properties": properties}


# Applied to every timeseries. Order matters -- Grafana applies overrides in
# sequence and the last match wins, so alarm colours come after direction
# colours: an "RX errors" series matches both and must resolve red. Note the
# word boundary on \bdown\b, without which every "Download" series turns red.
COMMON_OVERRIDES = [
    regexp_color_override("/RX|Download|Received|Inbound|Local|Online|Up\\b/i", GREEN),
    regexp_color_override("/TX|Upload|Transmit|Outbound|Forwarded/i", BLUE),
    regexp_color_override(r"/error|\bdrop|fail|\bdown\b|offline|critical|reject/i", RED),
    regexp_color_override("/warn|loss|retrans|evict|changed/i", ORANGE),
    regexp_color_override("/limit|maximum|capacity/i", YELLOW),
]

SEVERITY_OVERRIDES = [color_override(name, colour) for name, colour in SEVERITY_COLOURS.items()]


# ---------------------------------------------------------------------------
# Panel primitives
# ---------------------------------------------------------------------------


def query_options(max_data_points: int, interval: str = MIN_INTERVAL) -> dict[str, Any]:
    return {"maxDataPoints": max_data_points, "interval": interval}


def data_group(
    queries: list[dict[str, Any]],
    transformations: list[dict[str, Any]] | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "kind": "QueryGroup",
        "spec": {
            "queries": queries,
            "transformations": transformations or [],
            "queryOptions": options if options is not None else query_options(500),
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
    query_opts: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    assert desc.strip(), f"panel {pid} ({title}) has no description"
    defaults: dict[str, Any] = {
        "unit": unit,
        "mappings": [],
        "thresholds": deep(THRESHOLDS["neutral"]),
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
            "data": data_group(queries, transformations, query_opts),
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
    no_value: str = "No data",
) -> tuple[str, dict[str, Any]]:
    # noValue is a *field config* property (FieldConfigProperty.NoValue), not a
    # panel option. Placed under `options` it is silently ignored and the tile
    # falls back to the generic "No data", which is exactly the dishonest blank
    # this text exists to prevent.
    defaults: dict[str, Any] = {
        "thresholds": deep(THRESHOLDS[thresholds_key]),
        "mappings": deep(mappings) if mappings else [],
        "color": {"mode": "thresholds"},
        "noValue": no_value,
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
            # "auto" and never "value_and_name": with an empty legendFormat the
            # field name is the raw PromQL expression, which then renders
            # inside the tile in tiny letters.
            "textMode": "auto",
            "wideLayout": True,
        },
        field_defaults=defaults,
        query_opts=query_options(200),
    )


def loki_stat(
    pid: int,
    title: str,
    expr: str,
    desc: str,
    thresholds_key: str = "neutral",
    no_value: str = "0",
    unit: str = "none",
) -> tuple[str, dict[str, Any]]:
    return panel(
        pid,
        title,
        "stat",
        [loki_query(expr, query_type="instant")],
        unit,
        desc,
        options={
            "colorMode": "background_solid",
            "graphMode": "none",
            "justifyMode": "center",
            "orientation": "auto",
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "textMode": "auto",
            "wideLayout": True,
        },
        field_defaults={
            "thresholds": deep(THRESHOLDS[thresholds_key]),
            "color": {"mode": "thresholds"},
            "noValue": no_value,
        },
        query_opts=query_options(200),
    )


def timeseries(
    pid: int,
    title: str,
    queries: list[dict[str, Any]],
    unit: str,
    desc: str,
    stacked: bool = False,
    fill: int = 18,
    line_interpolation: str = "linear",
    overrides: list[dict[str, Any]] | None = None,
    thresholds_value: dict[str, Any] | None = None,
    legend_calcs: list[str] | None = None,
    show_legend: bool = True,
    soft_min: float | None = 0,
    color_mode: str = "palette-classic-by-name",
    max_data_points: int = 800,
) -> tuple[str, dict[str, Any]]:
    custom: dict[str, Any] = {
        "drawStyle": "line",
        "lineInterpolation": line_interpolation,
        "lineWidth": 2,
        "fillOpacity": 60 if stacked else fill,
        "gradientMode": "none" if stacked else "opacity",
        "showPoints": "never",
        "spanNulls": False,
        "stacking": {"group": "A", "mode": "normal" if stacked else "none"},
        "thresholdsStyle": {"mode": "dashed+area" if thresholds_value else "off"},
        "scaleDistribution": {"type": "linear"},
        "hideFrom": {"legend": False, "tooltip": False, "viz": False},
    }
    if soft_min is not None:
        custom["axisSoftMin"] = soft_min
    defaults = {
        "color": {"mode": color_mode},
        "custom": custom,
        "thresholds": deep(thresholds_value) if thresholds_value else deep(THRESHOLDS["neutral"]),
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
                "calcs": legend_calcs if legend_calcs is not None else ["lastNotNull", "max"],
                "displayMode": "table" if show_legend else "list",
                "placement": "bottom",
                "showLegend": show_legend,
            },
            "tooltip": {"hideZeros": False, "mode": "multi", "sort": "desc"},
        },
        field_defaults=defaults,
        overrides=(overrides or []) + COMMON_OVERRIDES,
        query_opts=query_options(max_data_points),
    )


def bargauge(
    pid: int,
    title: str,
    queries: list[dict[str, Any]],
    unit: str,
    desc: str,
    thresholds_key: str = "neutral",
    mappings: list[dict[str, Any]] | None = None,
    min_value: float | None = 0,
    max_value: float | None = None,
    # Not a continuous-* scheme: those map small values to the dark end of
    # the ramp, so on the dark theme the tail of a ranking goes dark-blue-on-
    # dark-grey and, because valueMode is "color", takes the numbers with it.
    # "thresholds" with the neutral single-step palette is the same blue,
    # uniformly and readably.
    color_mode: str = "thresholds",
    transformations: list[dict[str, Any]] | None = None,
    decimals: int | None = None,
) -> tuple[str, dict[str, Any]]:
    defaults: dict[str, Any] = {
        "color": {"mode": color_mode},
        "mappings": deep(mappings) if mappings else [],
        "thresholds": deep(THRESHOLDS[thresholds_key]),
    }
    if min_value is not None:
        defaults["min"] = min_value
    if max_value is not None:
        defaults["max"] = max_value
    if decimals is not None:
        defaults["decimals"] = decimals
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
        query_opts=query_options(200),
    )


def gauge(
    pid: int,
    title: str,
    expr: str,
    unit: str,
    desc: str,
    thresholds_key: str = "capacity_percent",
    min_value: float = 0,
    max_value: float = 100,
    decimals: int | None = 1,
    no_value: str = "No data",
) -> tuple[str, dict[str, Any]]:
    defaults: dict[str, Any] = {
        "min": min_value,
        "max": max_value,
        "thresholds": deep(THRESHOLDS[thresholds_key]),
        "color": {"mode": "thresholds"},
        "noValue": no_value,
    }
    if decimals is not None:
        defaults["decimals"] = decimals
    return panel(
        pid,
        title,
        "gauge",
        [prom_query(expr)],
        unit,
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
        field_defaults=defaults,
        query_opts=query_options(200),
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
    no_value: str = "No rows",
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
            "thresholds": deep(THRESHOLDS["neutral"]),
            "noValue": no_value,
        },
        overrides=overrides or [],
        transformations=transformations,
        query_opts=query_options(200),
    )


def piechart(
    pid: int,
    title: str,
    queries: list[dict[str, Any]],
    unit: str,
    desc: str,
    transformations: list[dict[str, Any]] | None = None,
    overrides: list[dict[str, Any]] | None = None,
    no_value: str = "No data",
) -> tuple[str, dict[str, Any]]:
    return panel(
        pid,
        title,
        "piechart",
        queries,
        unit,
        desc,
        options={
            "pieType": "donut",
            "displayLabels": ["percent"],
            "legend": {
                "displayMode": "table",
                "placement": "right",
                "showLegend": True,
                "values": ["value", "percent"],
            },
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "tooltip": {"mode": "single", "sort": "desc", "hideZeros": True},
        },
        field_defaults={"color": {"mode": "palette-classic"}, "noValue": no_value},
        overrides=overrides or [],
        transformations=transformations,
        query_opts=query_options(200),
    )


def state_timeline(
    pid: int,
    title: str,
    queries: list[dict[str, Any]],
    desc: str,
    mappings: list[dict[str, Any]],
    unit: str = "none",
    overrides: list[dict[str, Any]] | None = None,
    transformations: list[dict[str, Any]] | None = None,
    show_value: str = "never",
    row_height: float = 0.85,
    max_data_points: int = 300,
) -> tuple[str, dict[str, Any]]:
    """State timeline.

    Value mappings go in `fieldConfig.defaults.mappings`. A `byType` override
    carrying mappings silently fails to apply on this panel type and leaves the
    raw `-inf - +inf` bracket text on screen.
    """
    return panel(
        pid,
        title,
        "state-timeline",
        queries,
        unit,
        desc,
        options={
            "alignValue": "left",
            "mergeValues": True,
            "rowHeight": row_height,
            "showValue": show_value,
            "perPage": 20,
            "legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
            "tooltip": {"hideZeros": False, "mode": "single", "sort": "none"},
        },
        field_defaults={
            "color": {"mode": "thresholds"},
            "custom": {
                "fillOpacity": 80,
                "lineWidth": 0,
                "hideFrom": {"legend": False, "tooltip": False, "viz": False},
            },
            "mappings": deep(mappings),
            "thresholds": deep(THRESHOLDS["ok_bad"]),
        },
        overrides=overrides or [],
        transformations=transformations,
        query_opts=query_options(max_data_points),
    )


def histogram(
    pid: int,
    title: str,
    queries: list[dict[str, Any]],
    unit: str,
    desc: str,
    bucket_size: int | str = "auto",
    combine: bool = True,
) -> tuple[str, dict[str, Any]]:
    return panel(
        pid,
        title,
        "histogram",
        queries,
        unit,
        desc,
        options={
            "bucketSize": bucket_size,
            "combine": combine,
            "fillOpacity": 80,
            "gradientMode": "none",
            "legend": {"displayMode": "list", "placement": "bottom", "showLegend": False},
            "tooltip": {"hideZeros": False, "mode": "single", "sort": "none"},
        },
        field_defaults={
            "color": {"mode": "thresholds"},
            "custom": {
                "fillOpacity": 80,
                "gradientMode": "none",
                "lineWidth": 1,
                "hideFrom": {"legend": False, "tooltip": False, "viz": False},
            },
            "thresholds": deep(THRESHOLDS["signal_dbm"]),
        },
        query_opts=query_options(200),
    )


def logs_panel(
    pid: int,
    title: str,
    expr: str,
    desc: str,
    max_lines: int = 300,
) -> tuple[str, dict[str, Any]]:
    """Logs panel. `maxDataPoints` is the only way to bound the line count."""
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
        field_defaults={"thresholds": deep(THRESHOLDS["neutral"])},
        query_opts=query_options(max_lines, MIN_INTERVAL),
    )


def text(pid: int, content: str, title: str = "") -> tuple[str, dict[str, Any]]:
    key = f"panel-{pid}"
    return key, {
        "kind": "Panel",
        "spec": {
            "id": pid,
            "title": title,
            "description": "",
            "links": [],
            "data": data_group([], None, {}),
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


# Prometheus/scrape plumbing that means nothing in a table or node graph.
NOISE_COLUMNS = [
    "Time",
    "__name__",
    "cluster",
    "endpoint",
    "instance",
    "job",
    "namespace",
    "prometheus",
    "prometheus_replica",
    "service",
]


def clean(rename: dict[str, str], extra_exclude: list[str] | None = None, keep: list[str] | None = None) -> dict[str, Any]:
    return organize(
        exclude=NOISE_COLUMNS + (extra_exclude or []),
        rename=rename,
        index={name: i for i, name in enumerate(keep)} if keep else {},
    )


# ---------------------------------------------------------------------------
# Shared expressions (every one verified against live VictoriaMetrics)
# ---------------------------------------------------------------------------

F = PROM_FILTER

# Per-device traffic. openwrt_device_traffic_bytes_total is the only per-client
# byte counter that actually covers the LAN; openwrt_client_bytes_total exists
# but is conntrack-derived and currently sees only two MACs.
DEV_DL = f'sum by (device) (rate(openwrt_device_traffic_bytes_total{{{F}, device=~"$device", direction="download"}}[$__rate_interval]))'
DEV_UL = f'sum by (device) (rate(openwrt_device_traffic_bytes_total{{{F}, device=~"$device", direction="upload"}}[$__rate_interval]))'
DEV_TOTAL = f'sum by (device) (rate(openwrt_device_traffic_bytes_total{{{F}, device=~"$device"}}[$__rate_interval]))'

# Device name -> MAC, so per-device traffic can be keyed by the MAC that the
# topology frames use. Rate first, then join (never join inside rate()).
TRAFFIC_BY_MAC = (
    f'sum by (mac) (rate(openwrt_device_traffic_bytes_total{{{F}}}[$__rate_interval]) '
    f'* on(device) group_left(mac) openwrt_device_info{{{F}}})'
)


# Cross-router reconciliation. Kept identical in intent to
# build_openwrt_topology_dashboard.py -- the two node graphs read the same two
# metrics and had already drifted once, so any change here belongs there too.
# The reasoning for each step is documented in that builder's module docstring;
# briefly: prefer first-hand `authority="1"` series over the placeholders an AP
# emits so its edges resolve, drop routers that appear as each other's DHCP
# clients along with every edge pointing at them (dropping the node alone
# leaves a dangling target, which crashes the panel), and drop the gateway's
# guessed `lan:` edge whenever some AP reports a real association for that MAC.
def prefer_authority(metric: str) -> str:
    first = f'{metric}{{{F}, authority="1"}}'
    fallback = f'{metric}{{{F}, authority="0"}}'
    return f"({first} or ({fallback} unless on(id) {first}))"


def infra_as(label: str) -> str:
    return f'label_replace(openwrt_topology_infra_mac{{{F}}}, "{label}", "client:$1", "mac", "(.+)")'


def with_traffic(base: str, traffic: str) -> str:
    """Attach live throughput to a topology frame as its single numeric column.

    `base * 0` zeroes the presence value, `+ on(id) group_left()` overlays
    throughput for the ids that have any, and the trailing `or` puts back every
    node/edge that had no traffic -- at a truthful 0 B/s rather than vanishing.
    """
    zeroed = f"(({base}) * 0)"
    return f"(({zeroed} + on(id) group_left() ({traffic})) or {zeroed})"


def traffic_as(prefix: str) -> str:
    return f'sum by (id) (label_replace({TRAFFIC_BY_MAC}, "id", "{prefix}:$1", "mac", "(.+)"))'


def _nodes_expr() -> str:
    reconciled = f"({prefer_authority('openwrt_topology_node')} unless on(id) {infra_as('id')})"
    return with_traffic(reconciled, traffic_as("client"))


def _edges_expr() -> str:
    assoc_as_lan = (
        f'label_replace(openwrt_topology_edge{{{F}, id=~"assoc:.+"}}, "id", "lan:$1", "id", "assoc:(.+)")'
    )
    reconciled = f"({prefer_authority('openwrt_topology_edge')} unless on(id) {assoc_as_lan})"
    reconciled = f"({reconciled} unless on(target) {infra_as('target')})"
    return with_traffic(reconciled, f"({traffic_as('lan')} or {traffic_as('assoc')})")


NODES_EXPR = _nodes_expr()
EDGES_EXPR = _edges_expr()

# WiFi station signal with a friendly name. Both sides are lowercase now that
# alloy/config.alloy normalises MAC-valued labels at ingest; before that they
# joined only because both happened to be uppercase.
WIFI_SIGNAL_NAMED = (
    f'max by (mac, ifname, hostname) (wifi_station_signal_dbm{{{F}}} '
    f'* on(mac) group_left(hostname) max by (mac, hostname) (dhcp_lease{{{F}}}))'
)
WIFI_SIGNAL_PLAIN = f'max by (mac, ifname) (wifi_station_signal_dbm{{{F}}})'


# ---------------------------------------------------------------------------
# Variables
# ---------------------------------------------------------------------------


def variables() -> list[dict[str, Any]]:
    return [
        datasource_var("DS_PROMETHEUS", "Prometheus", "prometheus", "prometheus"),
        datasource_var("DS_LOKI", "Loki", "loki", "loki"),
        query_var(
            "router",
            "Router",
            'label_values(up{job="openwrt"}, router)',
            "All",
            include_all=True,
            multi=True,
        ),
        query_var(
            "wan_netdev",
            "WAN interface",
            'label_values(node_network_info{job="openwrt", router=~"$router"}, device)',
            "wan",
        ),
        query_var(
            "device",
            "Client device",
            'label_values(openwrt_device_info{job="openwrt", router=~"$router"}, device)',
            "All",
            include_all=True,
            multi=True,
        ),
        query_var(
            "netdev",
            "Network interface",
            'label_values(node_network_info{job="openwrt", router=~"$router"}, device)',
            "All",
            include_all=True,
            multi=True,
        ),
    ]


# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------


def tab_overview(b: DashboardBuilder) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    b.add(
        items,
        text(
            101,
            "# 🛰 OpenWrt Mission Control\n"
            "**Is the network healthy?** The tiles below answer that in five seconds; the tabs above answer *why*.\n\n"
            "🌐 uplink SLI · 📶 RF and clients · 🔀 who is using bandwidth · 🧭 DNS/DHCP · "
            "❤️ the box itself · 🕸 what is connected · 📜 what it is saying · 🔬 do I trust this data\n\n"
            "> Scope everything with **Router**; the LAN tab also honours **Client device**. "
            "Grey never means healthy on this dashboard - it means *not reported*.",
        ),
        0,
        0,
        24,
        4,
    )
    b.add(
        items,
        stat(
            102,
            "Network Status",
            f'(min(up{{{F}}}) * min(openwrt_wan_probe_success{{{F}, target=~"internet|resolver"}})) or vector(0)',
            "none",
            "Green only when every selected router is being scraped AND its internet and resolver probes both succeed. "
            "Red means either the exporter is unreachable or the uplink is failing - check the WAN tab first, then Diagnostics.",
            thresholds_key="ok_bad",
            mappings=STATUS_MAPPINGS,
        ),
        0,
        4,
        4,
        4,
    )
    b.add(
        items,
        stat(
            103,
            "Internet Latency",
            f'max(openwrt_wan_probe_latency_milliseconds{{{F}, target="internet"}})',
            "ms",
            "Worst current ICMP round-trip to the internet probe targets (1.1.1.1 / 8.8.8.8). "
            "Yellow above 50 ms, red above 150 ms - at red, expect video calls to stutter. Check WAN → Probe Latency for which target and when.",
            thresholds_key="latency_ms",
            graph=True,
            decimals=1,
        ),
        4,
        4,
        4,
        4,
    )
    b.add(
        items,
        stat(
            104,
            "WAN Availability (24h)",
            f'min(avg_over_time(openwrt_wan_probe_success{{{F}, target="internet"}}[24h])) * 100',
            "percent",
            "Fraction of the last 24 hours the internet probe succeeded, worst target. 99.9% is roughly 1.5 minutes of downtime per day. "
            "Metric retention here is about two days, so a longer error-budget window is not available.",
            thresholds_key="availability",
            decimals=3,
        ),
        8,
        4,
        4,
        4,
    )
    b.add(
        items,
        stat(
            105,
            "Online Clients",
            f'sum(openwrt_client_up{{{F}}}) or vector(0)',
            "none",
            "Clients the router currently considers present (wired plus associated wireless). Zero is truthful when nothing is connected, "
            "so the query pins it to 0 rather than letting an empty result render as a healthy blank tile.",
            graph=True,
            color_mode="value",
        ),
        12,
        4,
        4,
        4,
    )
    b.add(
        items,
        stat(
            106,
            "WAN Throughput",
            f'(sum(rate(node_network_receive_bytes_total{{{F}, device=~"$wan_netdev"}}[$__rate_interval])) '
            f'+ sum(rate(node_network_transmit_bytes_total{{{F}, device=~"$wan_netdev"}}[$__rate_interval]))) or vector(0)',
            "Bps",
            "Combined download plus upload rate on the WAN interface right now. Split by direction on the chart below. "
            "If this is high and the network feels slow, go to LAN → Top Talkers.",
            graph=True,
            color_mode="value",
        ),
        16,
        4,
        4,
        4,
    )
    b.add(
        items,
        stat(
            107,
            "Firing Alerts",
            'count(ALERTS{job="openwrt", alertstate="firing"}) or vector(0)',
            "none",
            "Prometheus alert rules currently firing for the OpenWrt job. Zero is genuinely good here. "
            "Pending alerts are excluded on purpose - they have not yet met their `for` duration.",
            thresholds_key="zero_good_hard",
        ),
        20,
        4,
        4,
        4,
    )
    b.add(
        items,
        timeseries(
            108,
            "WAN Throughput",
            [
                prom_query(
                    f'sum(rate(node_network_receive_bytes_total{{{F}, device=~"$wan_netdev"}}[$__rate_interval]))',
                    "Download",
                ),
                prom_query(
                    f'sum(rate(node_network_transmit_bytes_total{{{F}, device=~"$wan_netdev"}}[$__rate_interval]))',
                    "Upload",
                    ref="B",
                ),
            ],
            "Bps",
            "Uplink throughput by direction, from the WAN interface counters. Download green, upload blue, consistently across this dashboard. "
            "A flat line at zero during a period when clients were active points at the exporter, not the ISP - confirm on Diagnostics.",
        ),
        0,
        8,
        12,
        9,
    )
    b.add(
        items,
        timeseries(
            109,
            "Connected Clients by Link Type",
            [
                prom_query(f'count by (band) (openwrt_client_info{{{F}, connection="wifi"}})', "WiFi {{band}}"),
                prom_query(f'count(openwrt_client_info{{{F}, connection="wired"}}) or vector(0)', "Wired", ref="B"),
            ],
            "none",
            "How many clients are attached and over what. A sudden drop on one band without a matching rise on the other is a radio problem; "
            "a drop on both is usually a reboot - confirm with Router Health → Uptime.",
            stacked=True,
        ),
        12,
        8,
        12,
        9,
    )
    return b.tab(TAB_TITLES[0], items)


def tab_wan(b: DashboardBuilder) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    b.add(
        items,
        timeseries(
            201,
            "Probe Latency by Target",
            [
                prom_query(
                    f'openwrt_wan_probe_latency_milliseconds{{{F}}}',
                    "{{target}} · {{address}}",
                )
            ],
            "ms",
            "ICMP round-trip per probe target. `gateway` is your ISP's first hop, `internet` is a public anycast address, `resolver` is DNS. "
            "Gateway bad but internet fine means a local link problem; all three bad together means the uplink is down. The dashed line marks 150 ms.",
            thresholds_value=thresholds(("transparent", None), (RED, 150)),
        ),
        0,
        0,
        12,
        9,
    )
    b.add(
        items,
        timeseries(
            202,
            "Probe Jitter by Target",
            [prom_query(f'openwrt_wan_probe_jitter_milliseconds{{{F}}}', "{{target}} · {{address}}")],
            "ms",
            "Variation between consecutive round-trips. High jitter with normal latency is what actually breaks voice and video calls. "
            "Sustained jitter above ~30 ms on the internet target is worth raising with the ISP.",
        ),
        12,
        0,
        12,
        9,
    )
    b.add(
        items,
        timeseries(
            203,
            "Packet Loss by Target",
            [prom_query(f'openwrt_wan_probe_packet_loss_percent{{{F}}}', "{{target}} · {{address}}")],
            "percent",
            "Percentage of probe packets lost. Anything sustained above 1% degrades TCP throughput noticeably; above 5% the link is effectively broken. "
            "The dashed band marks those two lines.",
            thresholds_value=thresholds(("transparent", None), (YELLOW, 1), (RED, 5)),
        ),
        0,
        9,
        12,
        9,
    )
    b.add(
        items,
        state_timeline(
            204,
            "Probe Success Over Time",
            [prom_query(f'openwrt_wan_probe_success{{{F}}}', "{{target}} · {{address}}")],
            "One row per probe target, coloured by whether it answered. This is the panel that tells you *when* an outage started and how long it lasted. "
            "Grey gaps mean the exporter itself was not reporting, which is a different failure from a red band.",
            mappings=REACHABLE_MAPPINGS,
        ),
        12,
        9,
        12,
        9,
    )
    b.add(
        items,
        timeseries(
            205,
            "WAN Interface Throughput",
            [
                prom_query(
                    f'sum(rate(node_network_receive_bytes_total{{{F}, device=~"$wan_netdev"}}[$__rate_interval]))',
                    "Download",
                ),
                prom_query(
                    f'sum(rate(node_network_transmit_bytes_total{{{F}, device=~"$wan_netdev"}}[$__rate_interval]))',
                    "Upload",
                    ref="B",
                ),
                prom_query(
                    f'sum(rate(node_network_receive_drop_total{{{F}, device=~"$wan_netdev"}}[$__rate_interval]) '
                    f'+ rate(node_network_transmit_drop_total{{{F}, device=~"$wan_netdev"}}[$__rate_interval]))',
                    "Dropped packets/s",
                    ref="C",
                ),
            ],
            "Bps",
            "Uplink bytes per second with dropped packets overlaid. Drops climbing while throughput is at its ceiling means the link is saturated; "
            "drops with low throughput means a driver or cabling fault. The drop series is packets/s, shown on the right axis.",
            overrides=[
                name_override(
                    "Dropped packets/s",
                    [
                        {"id": "unit", "value": "pps"},
                        {"id": "custom.axisPlacement", "value": "right"},
                        {"id": "custom.drawStyle", "value": "bars"},
                    ],
                )
            ],
        ),
        0,
        18,
        12,
        9,
    )
    b.add(
        items,
        bargauge(
            206,
            "mwan3 Uplink Score",
            [prom_query(f'max by (interface) (mwan3_interface_score{{{F}}})', "{{interface}}")],
            "none",
            "mwan3's own tracking score per uplink, 0 to 10. 10 means every tracking IP answered; 0 means the interface is considered dead and traffic "
            "has been steered away from it. Green at 9+, red below 5. Empty means mwan3 is not installed.",
            thresholds_key="score",
            color_mode="thresholds",
            max_value=10,
        ),
        12,
        18,
        6,
        9,
    )
    b.add(
        items,
        stat(
            207,
            "WAN Uptime",
            f'max(mwan3_interface_uptime{{{F}, interface="wan"}})',
            "s",
            "How long mwan3 has considered the primary WAN interface continuously up. A reset to near zero is a reconnect - correlate with "
            "Logs & Security and with WAN Identity below to see whether the public IP changed at the same moment.",
            color_mode="value",
            no_value="mwan3 not collected",
        ),
        18,
        18,
        6,
        4,
    )
    b.add(
        items,
        table(
            208,
            "WAN Identity",
            [
                prom_query(f'wan_info{{{F}}}', "", "A", fmt="table", instant=True),
                prom_query(f'wan_public_ip_changed{{{F}}}', "", "B", fmt="table", instant=True),
            ],
            "Current public and WAN-side addresses per router, plus whether the public IP changed since the last check. "
            "A changed public IP explains sudden failures of anything with an IP allowlist or dynamic DNS.",
            transformations=[
                tf("merge"),
                clean(
                    rename={
                        "router": "Router",
                        "hostname": "Hostname",
                        "publicip": "Public IP",
                        "wanip": "WAN IP",
                        "Value #A": "Present",
                        "Value #B": "IP Changed",
                    },
                    extra_exclude=["Present"],
                    keep=["Router", "Hostname", "Public IP", "WAN IP", "IP Changed"],
                ),
            ],
            overrides=[
                name_override(
                    "IP Changed",
                    [
                        {"id": "custom.cellOptions", "value": {"type": "color-background"}},
                        {
                            "id": "mappings",
                            "value": [
                                {
                                    "type": "value",
                                    "options": {
                                        "0": {"text": "No", "color": GREEN, "index": 0},
                                        "1": {"text": "Yes", "color": YELLOW, "index": 1},
                                    },
                                }
                            ],
                        },
                    ],
                )
            ],
            sort_col="Router",
            sort_desc=False,
        ),
        18,
        22,
        6,
        5,
    )
    return b.tab(TAB_TITLES[1], items)


def tab_wifi(b: DashboardBuilder) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    b.add(
        items,
        stat(
            301,
            "WiFi Clients",
            f'sum(wifi_stations{{{F}}}) or vector(0)',
            "none",
            "Stations currently associated across every radio, straight from the AP's association list.",
            graph=True,
            color_mode="value",
        ),
        0,
        0,
        4,
        4,
    )
    b.add(
        items,
        stat(
            302,
            "5 GHz Share",
            f'(count(openwrt_client_info{{{F}, connection="wifi", band="5g"}}) '
            f'/ (count(openwrt_client_info{{{F}, connection="wifi"}}) > 0)) * 100',
            "percent",
            "Share of wireless clients sitting on 5 GHz rather than 2.4 GHz - the practical measure of whether band steering is working. "
            "A capable device parked on 2.4 GHz next to the AP is wasting most of its available throughput. The denominator is guarded against zero clients.",
            decimals=0,
            color_mode="value",
            no_value="No WiFi clients",
        ),
        4,
        0,
        4,
        4,
    )
    b.add(
        items,
        stat(
            303,
            "Weakest Signal",
            f'min(wifi_station_signal_dbm{{{F}}})',
            "dBm",
            "The worst RSSI among currently associated stations. Above -67 dBm is comfortable for video, below -75 dBm expect retries and low rates. "
            "Identify the station in the bar gauge to the right.",
            thresholds_key="signal_dbm",
            no_value="No stations",
        ),
        8,
        0,
        4,
        4,
    )
    b.add(
        items,
        bargauge(
            304,
            "Signal by Station",
            [prom_query(WIFI_SIGNAL_NAMED, "{{hostname}} · {{ifname}}")],
            "dBm",
            "RSSI per associated station, named from its DHCP lease. Stations without a current lease fall out of this join and appear in the table below by MAC instead "
            "(the WiFi and DHCP metric families are the only two that share MAC casing, so this is the one name join that is possible).",
            thresholds_key="signal_dbm",
            color_mode="thresholds",
            min_value=-90,
            max_value=-20,
        ),
        12,
        0,
        12,
        8,
    )
    b.add(
        items,
        timeseries(
            305,
            "Clients by Band",
            [prom_query(f'count by (band) (openwrt_client_info{{{F}, connection="wifi"}})', "{{band}}")],
            "none",
            "Band membership over time. Watch this after changing steering settings or transmit power: an effective change moves the 5 GHz line up "
            "and keeps it there rather than producing a sawtooth of clients bouncing between bands.",
            stacked=True,
        ),
        0,
        8,
        12,
        8,
    )
    b.add(
        items,
        histogram(
            306,
            "Signal Distribution",
            [prom_query(WIFI_SIGNAL_PLAIN, "{{ifname}}")],
            "dBm",
            "Distribution of station RSSI right now, all radios combined. An average hides the one client at the edge of coverage; this does not. "
            "A bimodal shape usually means one room is poorly covered.",
        ),
        12,
        8,
        12,
        8,
    )
    b.add(
        items,
        timeseries(
            307,
            "Noise Floor by Radio",
            [prom_query(f'openwrt_wifi_noise_dbm{{{F}}}', "{{ifname}}")],
            "dBm",
            "Background RF noise per radio. A rising noise floor at constant signal is interference - a neighbouring AP, a microwave, a badly shielded PSU - "
            "and it costs throughput even though every client still shows as connected. Compare against the signal levels above: what matters is the gap between them.",
            soft_min=None,
        ),
        0,
        16,
        12,
        8,
    )
    b.add(
        items,
        timeseries(
            308,
            "Link Quality by Radio",
            [prom_query(f'avg by (ifname, ssid) (wifi_network_quality{{{F}}})', "{{ssid}} · {{ifname}}")],
            "percent",
            "hostapd's own link-quality percentage per BSS. This is a quality score, not airtime utilisation - the exporter emits no channel-utilisation or "
            "airtime metric, so co-channel interference can only be inferred here and from the noise floor.",
        ),
        12,
        16,
        12,
        8,
    )
    b.add(
        items,
        state_timeline(
            309,
            "Station Association",
            [prom_query(f'{WIFI_SIGNAL_PLAIN} >= -100', "{{mac}} · {{ifname}}")],
            "One row per station, filled while it is associated and blank while it is not. This is how you spot a device that keeps dropping off, "
            "or one that roams between the two APs. The `>= -100` guard keeps NaN samples from rendering as a healthy band.",
            mappings=[
                {
                    "type": "range",
                    "options": {"from": -100, "to": -20, "result": {"text": "Associated", "color": GREEN, "index": 0}},
                },
                {"type": "special", "options": {"match": "null", "result": {"text": "Not associated", "color": GRAY, "index": 1}}},
            ],
            unit="dBm",
        ),
        0,
        24,
        12,
        8,
    )
    b.add(
        items,
        table(
            310,
            "WiFi Stations",
            [
                prom_query(WIFI_SIGNAL_PLAIN, "", "A", fmt="table", instant=True),
                prom_query(
                    f'max by (mac, ifname) (wifi_station_receive_kilobits_per_second{{{F}}}) * 1000',
                    "",
                    "B",
                    fmt="table",
                    instant=True,
                ),
                prom_query(
                    f'max by (mac, ifname) (wifi_station_transmit_kilobits_per_second{{{F}}}) * 1000',
                    "",
                    "C",
                    fmt="table",
                    instant=True,
                ),
                prom_query(
                    f'max by (mac, ifname) (wifi_station_expected_throughput_kilobits_per_second{{{F}}}) * 1000',
                    "",
                    "D",
                    fmt="table",
                    instant=True,
                ),
                prom_query(
                    f'max by (mac, ifname) (wifi_station_inactive_milliseconds{{{F}}}) / 1000',
                    "",
                    "E",
                    fmt="table",
                    instant=True,
                ),
                prom_query(
                    f'max by (mac, ifname) (label_replace(label_replace(openwrt_wifi_station_connected_seconds{{{F}}}, '
                    f'"mac", "$1", "station", "(.+)"), "ifname", "$1", "vif", "(.+)"))',
                    "",
                    "G",
                    fmt="table",
                    instant=True,
                ),
            ],
            "One row per associated station. Negotiated RX/TX rates are the PHY link rates, not achieved throughput; **Expected** is the driver's own estimate of "
            "usable throughput and is the honest number to compare against. A station whose expected throughput is far below its negotiated rate is retrying heavily. "
            "**Idle** is time since the last frame. Keyed by MAC because these metrics use uppercase MACs while the client-inventory metrics use lowercase.",
            transformations=[
                tf("merge"),
                clean(
                    rename={
                        "mac": "MAC",
                        "ifname": "Radio",
                        "Value #A": "Signal",
                        "Value #B": "RX Rate",
                        "Value #C": "TX Rate",
                        "Value #D": "Expected",
                        "Value #E": "Idle",
                        "Value #G": "Connected",
                    },
                    extra_exclude=["router"],
                    keep=["MAC", "Radio", "Signal", "RX Rate", "TX Rate", "Expected", "Idle", "Connected"],
                ),
                sort_by("Signal", desc=False),
            ],
            overrides=[
                name_override(
                    "Signal",
                    [
                        {"id": "unit", "value": "dBm"},
                        {"id": "custom.cellOptions", "value": {"type": "color-background"}},
                        {"id": "thresholds", "value": deep(THRESHOLDS["signal_dbm"])},
                    ],
                ),
                regexp_override(
                    "/RX Rate|TX Rate|Expected/",
                    [{"id": "unit", "value": "bps"}, {"id": "decimals", "value": 0}],
                ),
                regexp_override("/Idle|Connected/", [{"id": "unit", "value": "s"}, {"id": "decimals", "value": 0}]),
            ],
            sort_col="Signal",
            sort_desc=False,
            no_value="No stations associated",
        ),
        12,
        24,
        12,
        8,
    )
    b.add(
        items,
        table(
            311,
            "Radio Configuration",
            [
                prom_query(f'wifi_radio_channel{{{F}}}', "", "A", fmt="table", instant=True),
                prom_query(f'wifi_radio_frequency_mhz{{{F}}}', "", "B", fmt="table", instant=True),
                prom_query(f'wifi_radio_txpower_dbm{{{F}}}', "", "C", fmt="table", instant=True),
                prom_query(f'wifi_stations{{{F}}}', "", "D", fmt="table", instant=True),
            ],
            "Channel, frequency, transmit power and client count per BSS. Two APs sharing a channel on 2.4 GHz halve each other's airtime - "
            "check this before blaming the ISP for slow WiFi. Channel numbers use unit `none` so they are never scaled to \"1.5 K\".",
            transformations=[
                tf("merge"),
                clean(
                    rename={
                        "router": "Router",
                        "device": "Radio",
                        "ifname": "Interface",
                        "ssid": "SSID",
                        "Value #A": "Channel",
                        "Value #B": "Frequency",
                        "Value #C": "TX Power",
                        "Value #D": "Stations",
                    },
                    keep=["Router", "Radio", "Interface", "SSID", "Channel", "Frequency", "TX Power", "Stations"],
                ),
            ],
            overrides=[
                name_override("Channel", [{"id": "unit", "value": "none"}, {"id": "decimals", "value": 0}]),
                # Grafana's Megahertz unit id is `rotmhz` (rotational-speed category);
                # a bare "MHz" is not a real unit id and renders unformatted.
                name_override("Frequency", [{"id": "unit", "value": "rotmhz"}, {"id": "decimals", "value": 0}]),
                name_override("TX Power", [{"id": "unit", "value": "dBm"}, {"id": "decimals", "value": 0}]),
                name_override("Stations", [{"id": "unit", "value": "none"}, {"id": "decimals", "value": 0}]),
            ],
            sort_col="Interface",
            sort_desc=False,
        ),
        0,
        32,
        24,
        7,
    )
    return b.tab(TAB_TITLES[2], items)


def tab_lan(b: DashboardBuilder) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    b.add(
        items,
        stat(
            401,
            "LAN Throughput",
            f'sum(rate(openwrt_device_traffic_bytes_total{{{F}}}[$__rate_interval])) or vector(0)',
            "Bps",
            "Total per-device traffic the router is accounting for, both directions. Compare with WAN throughput on the Overview tab: "
            "a large gap means most traffic is staying inside the LAN, which is normal for backups and media streaming.",
            graph=True,
            color_mode="value",
        ),
        0,
        0,
        5,
        5,
    )
    b.add(
        items,
        gauge(
            402,
            "Conntrack Utilisation",
            f'(sum(node_nf_conntrack_entries{{{F}}}) / (sum(node_nf_conntrack_entries_limit{{{F}}}) > 0)) * 100',
            "percent",
            "Connection-tracking table fill against the kernel's real limit (nf_conntrack_max), not an invented maximum. "
            "Above 90% the router starts dropping new connections and everything feels broken at once - raise nf_conntrack_max or find the device opening the connections.",
            thresholds_key="capacity_percent",
        ),
        5,
        0,
        5,
        5,
    )
    b.add(
        items,
        piechart(
            403,
            "Bandwidth Share",
            [prom_query(DEV_TOTAL, "{{device}}", fmt="time_series")],
            "Bps",
            "Each client's share of current total throughput. Answers \"who is using the internet right now\" in one glance. "
            "Devices named `ip_x_x_x_x` have no DHCP hostname - give them a static lease if you want to recognise them.",
        ),
        10,
        0,
        14,
        5,
    )
    b.add(
        items,
        bargauge(
            404,
            "Top Talkers - Download",
            [prom_query(f'topk(10, {DEV_DL})', "{{device}}")],
            "Bps",
            "Ten busiest clients by download rate. Coloured on a neutral blue scale on purpose: being the biggest downloader is not a fault, "
            "so a red bar here would be misleading.",
        ),
        0,
        5,
        12,
        9,
    )
    b.add(
        items,
        bargauge(
            405,
            "Top Talkers - Upload",
            [prom_query(f'topk(10, {DEV_UL})', "{{device}}")],
            "Bps",
            "Ten busiest clients by upload rate. Sustained heavy upload from one client is the usual cause of poor latency for everyone else on an asymmetric line - "
            "check the WAN tab's jitter panel at the same time.",
        ),
        12,
        5,
        12,
        9,
    )
    b.add(
        items,
        timeseries(
            406,
            "Per-Device Throughput",
            [
                prom_query(DEV_DL, "{{device}} download"),
                prom_query(DEV_UL, "{{device}} upload", ref="B"),
            ],
            "Bps",
            "Traffic over time for the clients selected in the **Client device** variable. Leave it on All for the whole picture, "
            "or pick one device to see whether that 2 a.m. spike was really it.",
        ),
        0,
        14,
        24,
        9,
    )
    b.add(
        items,
        table(
            407,
            "Client Traffic",
            [
                prom_query(DEV_DL, "", "A", fmt="table", instant=True),
                prom_query(DEV_UL, "", "B", fmt="table", instant=True),
                prom_query(f'openwrt_device_info{{{F}, device=~"$device"}}', "", "C", fmt="table", instant=True),
            ],
            "One row per client: download, upload, and the derived total, joined to identity. Clients with no current traffic still appear via the identity query "
            "rather than silently vanishing. Sorted by total, with in-cell gauges so the distribution is readable without a chart.",
            transformations=[
                tf("joinByField", {"byField": "device", "mode": "outer"}),
                {
                    "kind": "calculateField",
                    "spec": {
                        "id": "calculateField",
                        "options": {
                            "mode": "binary",
                            "alias": "Total",
                            "binary": {"left": "Value #A", "right": "Value #B", "operator": "+"},
                            "replace": False,
                        },
                    },
                },
                clean(
                    rename={
                        "device": "Client",
                        "ip": "IP",
                        "mac": "MAC",
                        "Value #A": "Download",
                        "Value #B": "Upload",
                    },
                    extra_exclude=["Value #C", "interface", "router"],
                    keep=["Client", "IP", "MAC", "Download", "Upload", "Total"],
                ),
                sort_by("Total", desc=True),
                limit(60),
            ],
            overrides=[
                regexp_override(
                    "/Download|Upload|Total/",
                    [
                        {"id": "unit", "value": "Bps"},
                        {"id": "custom.cellOptions", "value": {"type": "gauge", "mode": "gradient"}},
                        {"id": "min", "value": 0},
                        {"id": "color", "value": {"mode": "continuous-blues"}},
                    ],
                )
            ],
            sort_col="Total",
            sort_desc=True,
        ),
        0,
        23,
        14,
        10,
    )
    b.add(
        items,
        timeseries(
            408,
            "Interface Errors & Drops",
            [
                prom_query(
                    f'sum by (device) (rate(node_network_receive_drop_total{{{F}, device=~"$netdev"}}[$__rate_interval]))',
                    "{{device}} rx drops",
                ),
                prom_query(
                    f'sum by (device) (rate(node_network_transmit_drop_total{{{F}, device=~"$netdev"}}[$__rate_interval]))',
                    "{{device}} tx drops",
                    ref="B",
                ),
                prom_query(
                    f'sum by (device) (rate(node_network_receive_errs_total{{{F}, device=~"$netdev"}}[$__rate_interval]) '
                    f'+ rate(node_network_transmit_errs_total{{{F}, device=~"$netdev"}}[$__rate_interval]))',
                    "{{device}} errors",
                    ref="C",
                ),
            ],
            "pps",
            "Dropped and errored packets per interface. Errors are almost always physical - cable, port, duplex mismatch. Drops are usually queue pressure. "
            "Both flat at zero is the expected state; any sustained non-zero line deserves investigating even if throughput looks fine.",
        ),
        14,
        23,
        10,
        10,
    )
    b.add(
        items,
        table(
            409,
            "Link Status",
            [
                prom_query(f'openwrt_link_up{{{F}}}', "", "A", fmt="table", instant=True),
                prom_query(f'openwrt_link_speed_bits_per_second{{{F}}}', "", "B", fmt="table", instant=True),
                prom_query(f'openwrt_link_duplex{{{F}}}', "", "C", fmt="table", instant=True),
                prom_query(f'openwrt_link_carrier_changes_total{{{F}}}', "", "D", fmt="table", instant=True),
            ],
            "Physical link state per interface. **Carrier changes** is the flap counter - a port whose count keeps climbing has a failing cable, "
            "a failing port, or a device power-cycling behind it. A gigabit port negotiated at 100 Mbit or half duplex is a cabling fault, not a setting.",
            transformations=[
                tf("merge"),
                clean(
                    rename={
                        "router": "Router",
                        "device": "Interface",
                        "Value #A": "Link",
                        "Value #B": "Speed",
                        "Value #C": "Duplex",
                        "Value #D": "Carrier Changes",
                    },
                    keep=["Router", "Interface", "Link", "Speed", "Duplex", "Carrier Changes"],
                ),
                sort_by("Carrier Changes", desc=True),
            ],
            overrides=[
                name_override(
                    "Link",
                    [
                        {"id": "custom.cellOptions", "value": {"type": "color-background"}},
                        {"id": "mappings", "value": deep(LINK_UP_MAPPINGS)},
                    ],
                ),
                name_override("Speed", [{"id": "unit", "value": "bps"}, {"id": "decimals", "value": 0}]),
                name_override(
                    "Duplex",
                    [
                        {"id": "custom.cellOptions", "value": {"type": "color-background"}},
                        {"id": "mappings", "value": deep(DUPLEX_MAPPINGS)},
                    ],
                ),
                name_override("Carrier Changes", [{"id": "unit", "value": "none"}, {"id": "decimals", "value": 0}]),
            ],
            sort_col="Carrier Changes",
            sort_desc=True,
        ),
        0,
        33,
        24,
        8,
    )
    return b.tab(TAB_TITLES[3], items)


def tab_dns_dhcp(b: DashboardBuilder) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    dns_local = f'sum(rate(dnsmasq_dns_local_answered{{{F}}}[$__rate_interval]))'
    dns_fwd = f'sum(rate(dnsmasq_dns_queries_forwarded{{{F}}}[$__rate_interval]))'
    b.add(
        items,
        stat(
            501,
            "DNS Query Rate",
            f'({dns_local} + {dns_fwd}) or vector(0)',
            "reqps",
            "Queries per second dnsmasq is answering, local plus forwarded. These are counters without a `_total` suffix, so they are rated here - "
            "the raw metric is a lifetime total and would be meaningless as a current value.",
            graph=True,
            color_mode="value",
            decimals=1,
        ),
        0,
        0,
        5,
        5,
    )
    b.add(
        items,
        stat(
            502,
            "Cache Hit Ratio",
            f'({dns_local} / (({dns_local} + {dns_fwd}) > 0)) * 100',
            "percent",
            "Share of DNS queries answered locally from cache or hosts rather than forwarded upstream. Higher is faster browsing and less upstream dependency. "
            "The denominator is guarded so an idle router shows no data instead of +Inf.",
            decimals=0,
            color_mode="value",
            no_value="No queries",
        ),
        5,
        0,
        5,
        5,
    )
    b.add(
        items,
        gauge(
            503,
            "DHCP Pool Utilisation",
            f'max(openwrt_dhcp_pool_utilization_percent{{{F}}})',
            "percent",
            "Active leases as a percentage of the configured pool. The absolute numbers behind this percentage are in the bar gauge to the right - "
            "a percentage without its denominator is not actionable.",
            thresholds_key="capacity_percent",
        ),
        10,
        0,
        5,
        5,
    )
    b.add(
        items,
        bargauge(
            504,
            "DHCP Pool",
            [
                prom_query(f'sum(openwrt_dhcp_leases_used{{{F}}}) or vector(0)', "Leases in use"),
                prom_query(f'sum(openwrt_dhcp_static_hosts_total{{{F}}}) or vector(0)', "Static reservations", ref="B"),
                prom_query(f'sum(openwrt_dhcp_pool_size_total{{{F}}}) or vector(0)', "Pool size", ref="C"),
            ],
            "none",
            "The raw numbers behind the utilisation gauge: leases currently held, statically reserved hosts, and the total configured pool size. "
            "Running out of pool shows up as clients that associate to WiFi but never get an address.",
            decimals=0,
        ),
        15,
        0,
        9,
        5,
    )
    b.add(
        items,
        timeseries(
            505,
            "DNS Answers: Local vs Forwarded",
            [
                prom_query(dns_local, "Local (cache/hosts)"),
                prom_query(dns_fwd, "Forwarded upstream", ref="B"),
                prom_query(f'sum(rate(dnsmasq_dns_unanswered{{{F}}}[$__rate_interval]))', "Unanswered", ref="C"),
            ],
            "reqps",
            "Where DNS answers come from, stacked so the total is the query rate. A rising *forwarded* share means the cache is being evicted or TTLs are short; "
            "any sustained *unanswered* line means upstream resolvers are failing and browsing will feel broken long before anything else alerts.",
            stacked=True,
        ),
        0,
        5,
        12,
        8,
    )
    b.add(
        items,
        timeseries(
            506,
            "DNS Cache Activity",
            [
                prom_query(f'sum(rate(dnsmasq_dns_cache_inserted{{{F}}}[$__rate_interval]))', "Inserted"),
                prom_query(f'sum(rate(dnsmasq_dns_cache_live_freed{{{F}}}[$__rate_interval]))', "Evicted while live", ref="B"),
            ],
            "reqps",
            "Cache inserts against entries evicted while still valid. A persistent eviction rate means the cache is too small for this network's query mix - "
            "raise dnsmasq's `cachesize`. Zero evictions means the cache is comfortably sized.",
        ),
        12,
        5,
        12,
        8,
    )
    b.add(
        items,
        timeseries(
            507,
            "DHCP Transactions",
            [
                prom_query(f'sum(rate(dnsmasq_dhcp_discover{{{F}}}[$__rate_interval]))', "Discover"),
                prom_query(f'sum(rate(dnsmasq_dhcp_offer{{{F}}}[$__rate_interval]))', "Offer", ref="B"),
                prom_query(f'sum(rate(dnsmasq_dhcp_request{{{F}}}[$__rate_interval]))', "Request", ref="C"),
                prom_query(f'sum(rate(dnsmasq_dhcp_ack{{{F}}}[$__rate_interval]))', "Ack", ref="D"),
                prom_query(f'sum(rate(dnsmasq_dhcp_nak{{{F}}}[$__rate_interval]))', "Nak", ref="E"),
                prom_query(f'sum(rate(dnsmasq_dhcp_decline{{{F}}}[$__rate_interval]))', "Decline", ref="G"),
            ],
            "reqps",
            "The DHCP handshake, per message type. Discover without Offer means the pool is exhausted or the server is not listening on that interface. "
            "Any Nak or Decline at all is worth reading the logs over - it usually means two servers or a duplicate address.",
        ),
        0,
        13,
        12,
        8,
    )
    b.add(
        items,
        table(
            508,
            "DHCP Leases",
            [
                prom_query(f'(dhcp_lease{{{F}}} > 0) * 1000', "", "A", fmt="table", instant=True),
                prom_query(f'clamp_min((dhcp_lease{{{F}}} > 0) - time(), 0)', "", "B", fmt="table", instant=True),
            ],
            "Current DHCP leases with absolute expiry and remaining time. Leases exported as 0 (static or infinite) are filtered out rather than rendered "
            "as impossible negative durations. Sorted by soonest expiry, which is where a lease-renewal problem shows up first.",
            transformations=[
                tf("merge"),
                clean(
                    rename={
                        "hostname": "Hostname",
                        "ip": "IP",
                        "mac": "MAC",
                        "Value #A": "Expires",
                        "Value #B": "Remaining",
                    },
                    extra_exclude=["dnsmasq", "router"],
                    keep=["Hostname", "IP", "MAC", "Expires", "Remaining"],
                ),
                sort_by("Remaining", desc=False),
            ],
            overrides=[
                name_override("Expires", [{"id": "unit", "value": "dateTimeAsIso"}]),
                name_override("Remaining", [{"id": "unit", "value": "s"}, {"id": "decimals", "value": 0}]),
            ],
            sort_col="Remaining",
            sort_desc=False,
            no_value="No expiring leases",
        ),
        12,
        13,
        12,
        8,
    )
    b.add(
        items,
        timeseries(
            509,
            "DHCP Lease Churn",
            [
                prom_query(f'sum(rate(dnsmasq_leases_allocated_4{{{F}}}[$__rate_interval]))', "Allocated"),
                prom_query(f'sum(rate(dnsmasq_leases_pruned_4{{{F}}}[$__rate_interval]))', "Pruned", ref="B"),
            ],
            "cps",
            "How often the DHCP pool is handing out or reclaiming leases. A sustained high allocation rate with matching prunes is normal churn "
            "(phones sleeping and waking); allocations climbing with prunes flat means the pool is filling up faster than leases expire, worth "
            "checking against DHCP Pool Utilisation above before it runs out.",
        ),
        0,
        21,
        24,
        7,
    )
    return b.tab(TAB_TITLES[4], items)


def tab_health(b: DashboardBuilder) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    b.add(
        items,
        stat(
            601,
            "CPU Busy",
            f'(1 - avg(rate(node_cpu_seconds_total{{{F}, mode="idle"}}[$__rate_interval]))) * 100',
            "percent",
            "Average non-idle CPU across all four MT7621 cores. This SoC routes at line rate only while it has headroom, so sustained high CPU "
            "usually shows up as reduced WAN throughput before anything else. Break it down by mode on the chart below.",
            thresholds_key="capacity_percent",
            decimals=0,
        ),
        0,
        0,
        4,
        4,
    )
    b.add(
        items,
        stat(
            602,
            "Memory Used",
            f'((sum(node_memory_MemTotal_bytes{{{F}}}) - sum(node_memory_MemAvailable_bytes{{{F}}})) '
            f'/ (sum(node_memory_MemTotal_bytes{{{F}}}) > 0)) * 100',
            "percent",
            "Memory in use against MemAvailable, which correctly counts reclaimable cache as free. On a 256 MB router, sustained pressure here "
            "ends in the OOM killer taking out dnsmasq or hostapd - which looks like a network outage, not a memory problem.",
            thresholds_key="capacity_percent",
            decimals=0,
        ),
        4,
        0,
        4,
        4,
    )
    b.add(
        items,
        stat(
            603,
            "Uptime",
            f'max(node_time_seconds{{{F}}} - node_boot_time_seconds{{{F}}})',
            "s",
            "Time since the last boot, longest-running selected router. An unexpectedly small value means it rebooted - the reboot counter next to this "
            "tile tells you how many times within the dashboard window.",
            color_mode="value",
        ),
        8,
        0,
        4,
        4,
    )
    b.add(
        items,
        stat(
            604,
            "Reboots (24h)",
            f'sum(changes(node_boot_time_seconds{{{F}}}[24h])) or vector(0)',
            "none",
            "Number of times the boot timestamp changed in the last 24 hours, i.e. unplanned or planned restarts. Zero is the expected value; "
            "anything else should be explained by the logs or by a power event.",
            thresholds_key="zero_good",
        ),
        12,
        0,
        4,
        4,
    )
    b.add(
        items,
        stat(
            605,
            "Hottest Radio",
            f'max(node_hwmon_temp_celsius{{{F}}})',
            "celsius",
            "Hottest hardware-monitor sensor on the router - on this board these are the two WiFi PHYs. Above 85 C the radios throttle and clients see "
            "sudden rate drops with no other symptom. This query is filtered to `job=\"openwrt\"`; without that it also matches the Kubernetes node exporters.",
            thresholds_key="temperature",
            decimals=0,
            no_value="No sensor",
        ),
        16,
        0,
        4,
        4,
    )
    b.add(
        items,
        stat(
            606,
            "Services Down",
            f'count(openwrt_service_up{{{F}}} == 0) or vector(0)',
            "none",
            "Monitored daemons that are enabled but not running. Zero is truthful and green here. Which service it is, is in the state timeline below.",
            thresholds_key="zero_good_hard",
        ),
        20,
        0,
        4,
        4,
    )
    b.add(
        items,
        timeseries(
            607,
            "CPU by Mode",
            [
                prom_query(
                    f'sum by (mode) (rate(node_cpu_seconds_total{{{F}, mode!="idle"}}[$__rate_interval])) '
                    f'/ scalar(count(count by (cpu) (node_cpu_seconds_total{{{F}}}))) * 100',
                    "{{mode}}",
                )
            ],
            "percent",
            "Non-idle CPU by mode, normalised to a percentage of total capacity across all cores and stacked so the top of the stack is total utilisation. "
            "`softirq` and `sys` dominating is normal for a router forwarding packets; `iowait` climbing means flash trouble.",
            stacked=True,
        ),
        0,
        4,
        8,
        8,
    )
    b.add(
        items,
        timeseries(
            608,
            "Load Average",
            [
                prom_query(f'max(node_load1{{{F}}})', "1 min"),
                prom_query(f'max(node_load5{{{F}}})', "5 min", ref="B"),
                prom_query(f'max(node_load15{{{F}}})', "15 min", ref="C"),
            ],
            "none",
            "Run-queue length. On a four-core SoC, sustained load above 4 means processes are waiting. The 1-minute line spiking while the 15-minute line "
            "stays flat is a transient and not worth chasing.",
            thresholds_value=thresholds(("transparent", None), (ORANGE, 4)),
        ),
        8,
        4,
        8,
        8,
    )
    b.add(
        items,
        timeseries(
            609,
            "Temperature by Sensor",
            [prom_query(f'max by (chip, sensor) (node_hwmon_temp_celsius{{{F}}})', "{{chip}} · {{sensor}}")],
            "celsius",
            "Every hardware-monitor sensor on the router over time. A slow upward drift across all sensors is dust or a blocked vent; "
            "a single sensor climbing under load is that radio working hard. The dashed line marks the 85 C throttling point.",
            thresholds_value=thresholds(("transparent", None), (RED, 85)),
            soft_min=None,
        ),
        16,
        4,
        8,
        8,
    )
    b.add(
        items,
        timeseries(
            610,
            "Memory Breakdown",
            [
                prom_query(
                    f'sum(node_memory_MemTotal_bytes{{{F}}}) - sum(node_memory_MemFree_bytes{{{F}}}) '
                    f'- sum(node_memory_Buffers_bytes{{{F}}}) - sum(node_memory_Cached_bytes{{{F}}})',
                    "Used by processes",
                ),
                prom_query(f'sum(node_memory_Buffers_bytes{{{F}}})', "Buffers", ref="B"),
                prom_query(f'sum(node_memory_Cached_bytes{{{F}}})', "Cached", ref="C"),
                prom_query(f'sum(node_memory_MemFree_bytes{{{F}}})', "Free", ref="D"),
            ],
            "bytes",
            "Where the RAM went, stacked to total installed memory. Cache and buffers are reclaimable and their growth is healthy - "
            "only the *Used by processes* band growing without bound is a leak.",
            stacked=True,
        ),
        0,
        12,
        12,
        8,
    )
    b.add(
        items,
        state_timeline(
            611,
            "Service Health",
            [prom_query(f'openwrt_service_up{{{F}}}', "{{exported_service}}")],
            "One row per monitored daemon, coloured by whether it is running. This is where a dnsmasq or hostapd restart becomes visible as a discrete event "
            "rather than a blip you have to infer from a graph. Mappings live in field defaults - a byType override silently fails on this panel type.",
            mappings=RUNNING_MAPPINGS,
        ),
        12,
        12,
        12,
        8,
    )
    b.add(
        items,
        bargauge(
            612,
            "Filesystem Usage",
            [prom_query(f'max by (router, mount) (openwrt_filesystem_used_percent{{{F}}})', "{{router}} {{mount}}")],
            "percent",
            "Fill level per mount. `/overlay` is the writable flash that holds your configuration and installed packages; filling it bricks "
            "configuration changes and package installs. `/tmp` is RAM. Thresholds are real here: yellow at 70%, red at 90%.",
            thresholds_key="capacity_percent",
            color_mode="thresholds",
            max_value=100,
            decimals=0,
        ),
        0,
        20,
        24,
        6,
    )
    b.add(
        items,
        timeseries(
            613,
            "Network Stack Saturation",
            [
                prom_query(f'sum(rate(openwrt_softnet_dropped_total{{{F}}}[$__rate_interval]))', "Softnet dropped"),
                prom_query(
                    f'sum(rate(openwrt_softnet_times_squeezed_total{{{F}}}[$__rate_interval]))', "Softnet squeezed", ref="B"
                ),
                prom_query(f'sum(rate(openwrt_tcp_listen_drops_total{{{F}}}[$__rate_interval]))', "TCP listen drops", ref="C"),
            ],
            "cps",
            "Kernel packet-processing backlog signals, summed across CPUs. `Softnet dropped` is packets the NIC ring handed over that the kernel had "
            "no room to queue; `squeezed` is the softirq budget running out before the queue was drained - both mean the CPU could not keep up with "
            "incoming traffic for a moment. `TCP listen drops` means a service's accept queue was full. Any of these climbing under load, on hardware "
            "this constrained, shows up before throughput visibly drops - it is the earliest warning this dashboard has for CPU-bound packet loss.",
            thresholds_value=thresholds(("transparent", None), (ORANGE, 1)),
        ),
        0,
        26,
        12,
        8,
    )
    b.add(
        items,
        timeseries(
            614,
            "Scheduler Activity",
            [
                prom_query(f'sum(rate(node_context_switches_total{{{F}}}[$__rate_interval]))', "Context switches/s"),
                prom_query(f'sum(rate(node_intr_total{{{F}}}[$__rate_interval]))', "Interrupts/s", ref="B"),
            ],
            "cps",
            "How hard the four MT7621 cores are being interrupted and rescheduled - context switches and hardware/software interrupts per second, "
            "summed across CPUs. This is the mechanism behind sustained CPU busy: a climb here alongside CPU Busy above explains *why* the router is "
            "busy (packet interrupts, radio IRQs) rather than just confirming that it is.",
        ),
        12,
        26,
        12,
        8,
    )
    return b.tab(TAB_TITLES[5], items)


def tab_topology(b: DashboardBuilder) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    b.add(
        items,
        text(
            701,
            "## 🕸 How to read this graph\n"
            "`Internet → router → access point → SSID → client`, built from one scrape, so a client that disappears never leaves a dangling edge.\n\n"
            "**Node ring** — green arc = online, grey arc = offline (clients); router and AP show an ok/warn ring. "
            "**Node and edge numbers** are live throughput in bytes/sec: nodes show the client's own traffic, edges show the traffic crossing that link. "
            "A node showing 0 B/s is genuinely idle, not missing. **Click a node** for its IP, MAC and band.\n\n"
            "> Isolated client nodes mean their association state is unknown, not that they are wired — the collector never guesses.",
        ),
        0,
        0,
        24,
        5,
    )
    b.add(
        items,
        stat(
            702,
            "Topology Nodes",
            f'count(openwrt_topology_node{{{F}}}) or vector(0)',
            "none",
            "Everything the topology collector currently knows about: internet, routers, APs, SSIDs and clients. Grafana's node graph draws at most "
            "200 nodes before hiding the rest behind a marker, which this network is comfortably below.",
            color_mode="value",
        ),
        0,
        5,
        4,
        4,
    )
    b.add(
        items,
        stat(
            703,
            "Offline Clients",
            f'count(openwrt_topology_node{{{F}, arc__offline="1"}}) or vector(0)',
            "none",
            "Client nodes the router has seen before but that are not present now. A number that never falls is stale lease data rather than "
            "genuinely absent devices - cross-check against the inventory table below.",
            thresholds_key="neutral",
            color_mode="value",
        ),
        4,
        5,
        4,
        4,
    )
    b.add(
        items,
        stat(
            704,
            "New Devices in Window",
            f'count((openwrt_client_first_seen_seconds{{{F}}} > time() - $__range_s)) or vector(0)',
            "none",
            "Clients whose first-seen timestamp falls inside the dashboard's current time range - i.e. devices that appeared for the first time in this window. "
            "Widen the time range and this counts further back. A non-zero value you cannot account for is worth investigating.",
            thresholds_key="zero_good",
            color_mode="value",
        ),
        8,
        5,
        4,
        4,
    )
    b.add(
        items,
        state_timeline(
            705,
            "Client Presence",
            [
                prom_query(
                    f'openwrt_client_up{{{F}}} * on(mac) group_left(hostname) '
                    f'max by (mac, hostname) (openwrt_client_info{{{F}}})',
                    "{{hostname}}",
                )
            ],
            "One row per client, filled while it is present. Read it for patterns: a device dropping off every night is a power-saving setting, "
            "a device flapping every few minutes is a signal or DHCP problem, and a row that ends abruptly is a device that left.",
            mappings=ONLINE_MAPPINGS,
        ),
        12,
        5,
        12,
        13,
    )
    b.add(
        items,
        table(
            706,
            "Client Inventory & State",
            [
                prom_query(
                    f'openwrt_client_up{{{F}}} * on(mac) group_left(hostname, ip, connection, band, ssid, ap) '
                    f'max by (mac, hostname, ip, connection, band, ssid, ap) (openwrt_client_info{{{F}}})',
                    "",
                    "A",
                    fmt="table",
                    instant=True,
                ),
                prom_query(
                    f'max by (mac) (time() - openwrt_client_first_seen_seconds{{{F}}})',
                    "",
                    "B",
                    fmt="table",
                    instant=True,
                ),
            ],
            "Every known client with its current state, how it is attached, and how long ago it was first seen. Sort by **State** to bring offline devices "
            "to the top, or by **First Seen** to find newcomers. Blank band and SSID mean a wired client.",
            transformations=[
                tf("merge"),
                clean(
                    rename={
                        "hostname": "Hostname",
                        "ip": "IP",
                        "mac": "MAC",
                        "connection": "Link",
                        "band": "Band",
                        "ssid": "SSID",
                        "ap": "AP",
                        "router": "Router",
                        "Value #A": "State",
                        "Value #B": "First Seen",
                    },
                    keep=["Hostname", "IP", "MAC", "Link", "Band", "SSID", "AP", "State", "First Seen"],
                ),
                sort_by("State", desc=False),
            ],
            overrides=[
                name_override(
                    "State",
                    [
                        {"id": "custom.cellOptions", "value": {"type": "color-background"}},
                        {"id": "mappings", "value": deep(ONLINE_MAPPINGS)},
                    ],
                ),
                name_override("First Seen", [{"id": "unit", "value": "s"}, {"id": "decimals", "value": 0}]),
            ],
            sort_col="State",
            sort_desc=False,
        ),
        0,
        9,
        12,
        9,
    )
    b.add(
        items,
        panel(
            707,
            "Network Topology",
            "nodeGraph",
            [
                prom_query(EDGES_EXPR, "", "edges", fmt="table", instant=True),
                prom_query(NODES_EXPR, "", "nodes", fmt="table", instant=True),
            ],
            "none",
            "The live network graph. Node and edge values are current throughput in bytes/sec, joined from the per-device traffic counters onto the topology frames; "
            "anything with no traffic reads a truthful 0 B/s rather than disappearing. "
            "The ring around each node is its health arc: green online/ok, grey offline, yellow warning - offline is grey rather than red because a "
            "device that has gone home is not a fault. "
            "Association edges are coloured by signal strength - green above -60 dBm, orange to -72, red below - which the collector computes in the same scrape that "
            "reads the association, so the colour and the link can never disagree. "
            "Only `mainstat` is populated: a Prometheus table query returns exactly one numeric column per frame and Grafana's transformation chain applies to every "
            "frame in the panel, so `secondarystat` and `thickness` cannot be added to one frame without corrupting the other. "
            "Emitting them from the exporter as their own series is the way to unlock them.",
            options={
                "zoomMode": "cooperative",
                "layoutAlgorithm": "layered",
                # Declaring `arcs` overrides automatic arc__* detection, so all
                # four arc fields this exporter emits are listed here and given
                # deliberate semantic colours instead of palette colours. Offline
                # is grey rather than red: a phone that left the house is not a
                # fault. Fields absent on a given node simply do not draw.
                "nodes": {
                    "mainStatUnit": "Bps",
                    "arcs": [
                        {"field": "arc__online", "color": GREEN},
                        {"field": "arc__offline", "color": GRAY},
                        {"field": "arc__ok", "color": GREEN},
                        {"field": "arc__warn", "color": YELLOW},
                    ],
                },
                "edges": {"mainStatUnit": "Bps"},
            },
            field_defaults={"thresholds": deep(THRESHOLDS["neutral"])},
            transformations=[clean(rename={"Value": "mainstat"}, extra_exclude=["router", "authority"])],
            query_opts=query_options(200),
        ),
        0,
        18,
        24,
        17,
    )
    return b.tab(TAB_TITLES[6], items)


def tab_logs(b: DashboardBuilder) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    b.add(
        items,
        loki_stat(
            801,
            "Log Lines (1h)",
            f'sum(count_over_time({{{LOKI_FILTER}}} [1h])) or vector(0)',
            "Everything the router logged in the last hour. A sudden collapse to zero is a syslog pipeline failure, not a quiet router - "
            "confirm on Diagnostics → Syslog Pipeline before concluding all is well.",
        ),
        0,
        0,
        6,
        4,
    )
    b.add(
        items,
        loki_stat(
            802,
            "Errors (1h)",
            f'sum(count_over_time({{{LOKI_FILTER}, message_severity=~"error|err|crit|critical|alert|emergency|emerg"}} {CRON_CMD_FILTER} [1h])) or vector(0)',
            "Syslog lines at error severity or above in the last hour, excluding BusyBox cron's routine command-start records. "
            "Any non-zero value here should be readable in the log panel below; if it is not, the severity mapping needs widening.",
            thresholds_key="zero_good",
        ),
        6,
        0,
        6,
        4,
    )
    b.add(
        items,
        loki_stat(
            803,
            "Warnings (1h)",
            f'sum(count_over_time({{{LOKI_FILTER}, message_severity=~"warning|warn"}} {CRON_CMD_FILTER} [1h])) or vector(0)',
            "Warning-severity lines in the last hour. Warnings are normal in small numbers on a router - hostapd logs a warning every time a client with "
            "a weak signal retries. A step change in the rate is the signal, not the absolute count.",
            thresholds_key="neutral",
        ),
        12,
        0,
        6,
        4,
    )
    b.add(
        items,
        loki_stat(
            804,
            "Auth Failures (24h)",
            f'sum(count_over_time({{{LOKI_FILTER}}} '
            f'|~ "(?i)(failed password|login failed|bad password|invalid user|authentication failure|bad pubkey)" [24h])) or vector(0)',
            "Failed SSH and login attempts in the last 24 hours, from dropbear. A handful is background internet noise if the router is exposed; "
            "a sustained rate means someone is trying, and the router should not be reachable from the WAN at all. Details in the events panel below.",
            thresholds_key="zero_good",
        ),
        18,
        0,
        6,
        4,
    )
    b.add(
        items,
        timeseries(
            805,
            "Log Rate by Severity",
            [
                loki_query(
                    f'sum by (message_severity) (rate({{{LOKI_FILTER}}} {CRON_CMD_FILTER} [$__auto]))',
                )
            ],
            "cps",
            "Syslog volume by severity, stacked, with one colour per severity used consistently across this dashboard: red for critical and above, "
            "orange for error, yellow for warning, blue for notice, green for informational. Cron command-start lines are filtered out - "
            "on an idle router they are the majority of all lines and drown everything else.",
            stacked=True,
            overrides=SEVERITY_OVERRIDES,
        ),
        0,
        4,
        14,
        9,
    )
    b.add(
        items,
        piechart(
            806,
            "Log Volume by Application",
            [loki_query(f'sum by (message_app_name) (count_over_time({{{LOKI_FILTER}}} {CRON_CMD_FILTER} [$__range]))', query_type="instant")],
            "none",
            "Which daemon is doing the talking over the selected time range. A process that suddenly dominates the log is usually the one having trouble - "
            "hostapd for WiFi, odhcpd or dnsmasq for addressing, netifd for interfaces, kernel for hardware.",
        ),
        14,
        4,
        10,
        9,
    )
    b.add(
        items,
        logs_panel(
            807,
            "Errors & Warnings",
            f'{{{LOKI_FILTER}, message_severity=~"error|err|crit|critical|alert|emergency|emerg|warning|warn"}} {CRON_CMD_FILTER}',
            "Warning-and-above lines, newest first. This is the panel to read when any tile above turns orange or red. "
            "Line count is bounded by the panel's Max data points rather than the datasource default.",
        ),
        0,
        13,
        12,
        11,
    )
    b.add(
        items,
        logs_panel(
            808,
            "Authentication & WiFi Association Events",
            f'{{{LOKI_FILTER}}} '
            f'|~ "(?i)(failed password|login failed|bad password|invalid user|authentication failure|bad pubkey|exit \\\\(|AP-STA-(CONNECTED|DISCONNECTED))"',
            "Login attempts and WiFi association/disassociation events in one stream. The AP-STA lines are the only roaming record this network has - "
            "the association-event *metric* collector reports itself unavailable, but hostapd still logs every join and leave, so this is the reliable source.",
        ),
        12,
        13,
        12,
        11,
    )
    return b.tab(TAB_TITLES[7], items)


def tab_diagnostics(b: DashboardBuilder) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    b.add(
        items,
        stat(
            901,
            "Exporter Reachable",
            f'min(up{{{F}}}) or vector(0)',
            "none",
            "Whether Prometheus is successfully scraping every selected router. If this is red, every other panel on this dashboard is stale and "
            "nothing else here should be trusted.",
            thresholds_key="ok_bad",
            mappings=STATUS_MAPPINGS,
        ),
        0,
        0,
        6,
        4,
    )
    b.add(
        items,
        stat(
            902,
            "Scrape Duration",
            f'max(scrape_duration_seconds{{{F}}})',
            "s",
            "How long the slowest router takes to answer a scrape. Climbing duration on an embedded router means the Lua collectors are being starved of CPU; "
            "once it exceeds the scrape interval, samples start being missed and graphs go gappy.",
            thresholds_key="scrape_seconds",
            decimals=2,
        ),
        6,
        0,
        6,
        4,
    )
    b.add(
        items,
        stat(
            903,
            "Series per Scrape",
            f'max(scrape_samples_scraped{{{F}}})',
            "none",
            "Number of samples returned per scrape - the cardinality of this exporter. A sudden jump usually means a collector started emitting a "
            "per-client or per-connection label it should not, which is how a small router quietly overwhelms a metrics backend.",
            color_mode="value",
        ),
        12,
        0,
        6,
        4,
    )
    b.add(
        items,
        stat(
            904,
            "Textfile Freshness",
            f'max(time() - node_textfile_mtime_seconds{{{F}}})',
            "s",
            "Age of the oldest textfile-collector output. Several helpers run on a 10-minute cron, so values up to about 660 seconds are healthy; "
            "beyond 900 seconds a helper has clearly missed a run and the panels it feeds are showing stale data.",
            thresholds_key="freshness",
            decimals=0,
        ),
        18,
        0,
        6,
        4,
    )
    b.add(
        items,
        timeseries(
            905,
            "Scrape Health Over Time",
            [
                prom_query(f'max(scrape_duration_seconds{{{F}}})', "Scrape duration"),
                prom_query(f'max(scrape_samples_scraped{{{F}}})', "Samples scraped", ref="B"),
                prom_query(f'max(scrape_series_added{{{F}}})', "New series added", ref="C"),
            ],
            "s",
            "Scrape duration against sample count and newly added series. Duration and samples rising together is normal growth; "
            "*New series added* staying persistently high is a cardinality leak, because in a stable system almost every series should already exist.",
            overrides=[
                regexp_override(
                    "/Samples scraped|New series added/",
                    [
                        {"id": "unit", "value": "none"},
                        {"id": "custom.axisPlacement", "value": "right"},
                        {"id": "decimals", "value": 0},
                    ],
                )
            ],
        ),
        0,
        4,
        12,
        9,
    )
    b.add(
        items,
        timeseries(
            906,
            "Syslog Pipeline",
            [
                prom_query(
                    f'sum(rate(loki_source_syslog_entries_total{{{ALLOY_FILTER}}}[$__rate_interval]))',
                    "Received from router",
                ),
                prom_query(
                    f'sum(rate(loki_write_sent_entries_total{{{ALLOY_FILTER}}}[$__rate_interval]))',
                    "Written to Loki",
                    ref="B",
                ),
                prom_query(
                    f'sum(rate(loki_write_dropped_entries_total{{{ALLOY_FILTER}}}[$__rate_interval])) or vector(0)',
                    "Dropped",
                    ref="C",
                ),
                prom_query(
                    f'sum(rate(loki_source_syslog_parsing_errors_total{{{ALLOY_FILTER}}}[$__rate_interval])) or vector(0)',
                    "Parse errors",
                    ref="D",
                ),
            ],
            "cps",
            "Health of the log path itself: syslog lines received from the router, lines written to Loki, and anything dropped or unparseable along the way. "
            "If the Logs tab looks suspiciously quiet, look here first - this is measured by the Alloy shipper, not by the router, so it survives the router going away. "
            "Not scoped by the Router variable: the shipper is a single shared process.",
            overrides=[color_override("Dropped", RED), color_override("Parse errors", ORANGE)],
        ),
        12,
        4,
        12,
        9,
    )
    b.add(
        items,
        table(
            907,
            "Collector Status",
            [prom_query(f'node_scrape_collector_success{{{F}}}', "", "A", fmt="table", instant=True)],
            "Per-collector scrape result inside the exporter, sorted so anything failing floats to the top. A failing collector is why an otherwise "
            "healthy-looking dashboard has one empty panel - it fails silently at the exporter, not at Prometheus.",
            transformations=[
                tf("merge"),
                clean(
                    rename={"router": "Router", "collector": "Collector", "Value #A": "Success", "Value": "Success"},
                    keep=["Router", "Collector", "Success"],
                ),
                sort_by("Success", desc=False),
            ],
            overrides=[
                name_override(
                    "Success",
                    [
                        {"id": "custom.cellOptions", "value": {"type": "color-background"}},
                        {
                            "id": "mappings",
                            "value": [
                                {
                                    "type": "value",
                                    "options": {
                                        "1": {"text": "OK", "color": GREEN, "index": 0},
                                        "0": {"text": "Failed", "color": RED, "index": 1},
                                    },
                                }
                            ],
                        },
                    ],
                )
            ],
            sort_col="Success",
            sort_desc=False,
        ),
        0,
        13,
        12,
        10,
    )
    b.add(
        items,
        table(
            908,
            "Optional Collector Availability",
            [
                prom_query(
                    f'{{__name__=~"openwrt_.*_collector_available", {F}}}',
                    "",
                    "A",
                    fmt="table",
                    instant=True,
                )
            ],
            "Which optional collectors this build of the exporter is actually providing. **Not collected is grey, never red** - a collector that is off "
            "because the feature is not installed is a configuration fact, not a fault. Known-empty on this network: the association-event collector "
            "reports unavailable, and the DPI collector reports available but has emitted zero flows and zero devices for the whole retention window.",
            transformations=[
                tf("merge"),
                clean(
                    rename={"router": "Router", "__name__": "Collector", "Value": "State", "Value #A": "State"},
                    keep=["Router", "Collector", "State"],
                ),
                sort_by("Collector", desc=False),
            ],
            overrides=[
                name_override(
                    "State",
                    [
                        {"id": "custom.cellOptions", "value": {"type": "color-background"}},
                        {"id": "mappings", "value": deep(COLLECTOR_MAPPINGS)},
                    ],
                )
            ],
            sort_col="Collector",
            sort_desc=False,
        ),
        12,
        13,
        12,
        10,
    )
    return b.tab(TAB_TITLES[8], items)


# The `__name__` matcher above needs the metric-name column to survive the
# organize step; NOISE_COLUMNS drops it. Patched in build_dashboard().


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def build_dashboard() -> dict[str, Any]:
    b = DashboardBuilder()
    tabs = [
        tab_overview(b),
        tab_wan(b),
        tab_wifi(b),
        tab_lan(b),
        tab_dns_dhcp(b),
        tab_health(b),
        tab_topology(b),
        tab_logs(b),
        tab_diagnostics(b),
    ]

    # Panel 908 selects by __name__ and must keep that column, which the shared
    # NOISE_COLUMNS list otherwise drops. Fix it here rather than forking clean().
    organize_908 = b.elements["panel-908"]["spec"]["data"]["spec"]["transformations"][1]["spec"]["options"]
    organize_908["excludeByName"].pop("__name__", None)

    spec: dict[str, Any] = {
        "title": DASHBOARD_TITLE,
        "description": (
            "Single-pane OpenWrt operations dashboard: uplink SLI, WiFi and clients, per-device traffic, DNS/DHCP, "
            "router health, live topology, syslog and data-quality diagnostics. Replaces eight separate dashboards. "
            "Every query is verified against live data; metrics the exporter does not emit are absent rather than "
            "rendered as empty panels."
        ),
        "tags": ["openwrt", "router", "mission-control", "generated"],
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
            "from": "now-6h",
            "to": "now",
            "autoRefresh": "1m",
            "autoRefreshIntervals": ["30s", "1m", "5m", "15m", "1h"],
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
        "metadata": {"name": DASHBOARD_NAME},
        "spec": spec,
    }
    validate_dashboard(dashboard)
    return dashboard


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


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


BANNED_UNITS = {"short", ""}
PANEL_BUDGET = (60, 90)  # Keep tabs useful without allowing unbounded growth.

# Unit ids verified against grafana-data's valueFormats/categories.ts. Grafana
# silently renders an unknown id as raw numbers, so an invented unit like
# "logs" or "MHz" (the real id is `rotmhz`) fails quietly rather than loudly.
KNOWN_UNITS = {
    "none",
    "percent",
    "percentunit",
    "s",
    "ms",
    "bytes",
    "bps",
    "Bps",
    "pps",
    "cps",
    "reqps",
    "dBm",
    "celsius",
    "rotmhz",
    "dateTimeAsIso",
}


def validate_dashboard(dash: dict[str, Any]) -> None:
    assert dash["apiVersion"] == "dashboard.grafana.app/v2beta1"
    assert dash["kind"] == "Dashboard"
    assert dash["metadata"]["name"] == DASHBOARD_NAME
    assert "uid" not in dash["metadata"]

    spec = dash["spec"]
    assert spec["title"] == DASHBOARD_TITLE
    assert spec["layout"]["kind"] == "TabsLayout"
    actual_tabs = [tab["spec"]["title"] for tab in spec["layout"]["spec"]["tabs"]]
    assert actual_tabs == TAB_TITLES, actual_tabs

    elements = spec["elements"]
    refs = layout_refs(spec["layout"])
    assert set(refs) == set(elements), f"orphan/missing refs: refs={len(refs)} elements={len(elements)}"
    duplicates = sorted({ref for ref in refs if refs.count(ref) > 1})
    assert not duplicates, f"duplicate layout refs: {duplicates}"

    low, high = PANEL_BUDGET
    assert low <= len(elements) <= high, f"panel budget {low}-{high} violated: {len(elements)}"

    panel_ids: list[int] = []
    for key, element in elements.items():
        panel_spec = element["spec"]
        panel_ids.append(panel_spec["id"])
        assert key == f"panel-{panel_spec['id']}", key
        viz = panel_spec["vizConfig"]["group"]
        defaults = panel_spec["vizConfig"]["spec"]["fieldConfig"]["defaults"]

        if viz != "text":
            assert panel_spec["description"].strip(), f"missing description: {key} {panel_spec['title']}"
        if viz not in {"text", "logs", "nodeGraph"}:
            unit = defaults.get("unit")
            assert unit not in BANNED_UNITS, f"generic/absent unit: {key} {panel_spec['title']} -> {unit!r}"
            assert unit in KNOWN_UNITS, f"unknown unit id: {key} {panel_spec['title']} -> {unit!r}"

        for override in panel_spec["vizConfig"]["spec"]["fieldConfig"]["overrides"]:
            for prop in override["properties"]:
                if prop["id"] == "unit":
                    assert prop["value"] in KNOWN_UNITS, f"unknown override unit id: {key} -> {prop['value']!r}"

        # Golden rule 22: the Base threshold step means -inf and must be null.
        for step_holder in _iter_threshold_sets(panel_spec):
            steps = step_holder.get("steps") or []
            if steps:
                assert steps[0].get("value") is None, f"non-null base threshold step in {key}"

        # Golden rule 21: state timelines must carry their mappings in defaults.
        if viz == "state-timeline":
            assert defaults.get("mappings"), f"state timeline without default mappings: {key}"
            for override in panel_spec["vizConfig"]["spec"]["fieldConfig"]["overrides"]:
                if override["matcher"]["id"] == "byType":
                    for prop in override["properties"]:
                        assert prop["id"] != "mappings", f"byType mappings override on state timeline: {key}"

        # Query options are deliberate everywhere except the text panels.
        query_opts = panel_spec["data"]["spec"]["queryOptions"]
        if viz != "text":
            assert query_opts.get("maxDataPoints"), f"no maxDataPoints: {key}"
            assert query_opts.get("interval") == MIN_INTERVAL, f"no/incorrect min interval: {key}"

        # noValue belongs in fieldConfig.defaults; under options it is ignored.
        assert "noValue" not in panel_spec["vizConfig"]["spec"]["options"], f"noValue in panel options: {key}"
        assert "pluginVersion" not in json.dumps(element)

    assert len(panel_ids) == len(set(panel_ids)), "duplicate panel ids"

    # Every rate()/increase() uses $__rate_interval; multi-value variables use =~.
    # Range-window checking is PromQL-only: LogQL line filters contain regex
    # character classes such as [^ ] that a naive bracket scan misreads.
    for group, query_expr in _iter_exprs(spec):
        for func in ("rate(", "increase(", "irate(") if group == "prometheus" else ():
            for match in re.finditer(re.escape(func), query_expr):
                window = query_expr[match.end() : match.end() + 400]
                bracket = re.search(r"\[([^\]]+)\]", window)
                assert bracket, f"range function without window: {query_expr}"
                assert bracket.group(1) in {"$__rate_interval", "$__auto", "$__range", "1h", "24h"}, (
                    f"bad range window {bracket.group(1)!r} in: {query_expr}"
                )
        for var in ("$router", "$device", "$netdev"):
            if var in query_expr:
                assert f'=~"{var}"' in query_expr or f'=~"${{{var[1:]}}}"' in query_expr, (
                    f"multi-value variable {var} used without =~ in: {query_expr}"
                )

    # Grid sanity, per tab.
    for tab_title, items in grid_items_by_tab(spec["layout"]).items():
        rects: list[tuple[int, int, int, int, str]] = []
        for item in items:
            ispec = item["spec"]
            x, y, w, h = ispec["x"], ispec["y"], ispec["width"], ispec["height"]
            name = ispec["element"]["name"]
            assert 0 <= x <= 23, f"{tab_title} {name} invalid x={x}"
            assert y >= 0, f"{tab_title} {name} invalid y={y}"
            assert 1 <= w <= 24 and h > 0, f"{tab_title} {name} invalid size {w}x{h}"
            assert x + w <= 24, f"{tab_title} {name} overflows the 24-column grid"
            rect = (x, y, x + w, y + h, name)
            for other in rects:
                if rect[0] < other[2] and other[0] < rect[2] and rect[1] < other[3] and other[1] < rect[3]:
                    raise AssertionError(f"{tab_title} overlap: {name} with {other[4]}")
            rects.append(rect)

    # Variables.
    defined_vars = {v["spec"]["name"] for v in spec["variables"]}
    globals_allowed = {
        "__rate_interval",
        "__range",
        "__range_s",
        "__from",
        "__to",
        "__all",
        "__value",
        "__data",
        "__interval",
        "__auto",
        "__url_time_range",
    }
    referenced: set[str] = set()
    var_pattern = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?")
    for s in iter_strings(spec):
        for match in var_pattern.finditer(s):
            referenced.add(match.group(1))
    undefined = referenced - defined_vars - globals_allowed
    assert not undefined, f"undefined variables: {sorted(undefined)}"
    unused = defined_vars - referenced - {"DS_PROMETHEUS", "DS_LOKI"}
    assert not unused, f"unused variables: {sorted(unused)}"

    rendered = json.dumps(dash, sort_keys=True)
    assert "schemaVersion" not in rendered
    assert "pluginVersion" not in rendered
    assert PROM_DS in rendered and LOKI_DS in rendered


def _iter_threshold_sets(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if "steps" in value and "mode" in value and isinstance(value.get("steps"), list):
            found.append(value)
        for v in value.values():
            found.extend(_iter_threshold_sets(v))
    elif isinstance(value, list):
        for v in value:
            found.extend(_iter_threshold_sets(v))
    return found


def _iter_exprs(spec: dict[str, Any]) -> list[tuple[str, str]]:
    exprs: list[tuple[str, str]] = []
    for element in spec["elements"].values():
        for query in element["spec"]["data"]["spec"]["queries"]:
            expr = query["spec"]["query"]["spec"].get("expr")
            if expr:
                exprs.append((query["spec"]["query"]["group"], expr))
    return exprs


def main() -> None:
    dashboard = build_dashboard()
    rendered = stable_json(dashboard)
    parsed = json.loads(rendered)
    assert parsed == dashboard

    second = stable_json(build_dashboard())
    assert second == rendered, "non-deterministic output"
    digest = hashlib.sha256(rendered.encode("utf-8")).hexdigest()

    for out in OUTS:
        out.parent.mkdir(parents=True, exist_ok=True)
        old = out.read_text(encoding="utf-8") if out.exists() else None
        out.write_text(rendered, encoding="utf-8")
        assert out.read_text(encoding="utf-8") == rendered
        changed = "updated" if old != rendered else "unchanged"
        print(f"{out}: {changed}, panels={len(dashboard['spec']['elements'])}, sha256={digest}")


if __name__ == "__main__":
    main()
