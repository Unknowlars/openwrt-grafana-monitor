#!/usr/bin/env python3
"""Build the Grafana v2beta1 dashboard for OpenWrt clients.

The generated JSON files are artifacts. This script is the source of truth.
It writes both the manual import export and the provisioned dashboard copy.
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
    LOKI_DS,
    ORANGE,
    RED,
    THRESHOLDS,
    DashboardBuilder,
    bargauge,
    datasource_var,
    iter_strings,
    loki_query,
    loki_stat,
    logs_panel,
    organize,
    panel,
    prom_query,
    query_var,
    sort_by,
    stable_json,
    stat,
    table,
    text,
    thresholds,
    timeseries,
    tf,
)


OUTS = [
    Path("grafana-dashboard-exports/openwrt-clients-v2.json"),
    Path("grafana/provisioning/dashboards/openwrt-clients-v2.json"),
]

PROM_DS = "${DS_PROMETHEUS}"
LOKI_FILTER = 'job="openwrt-syslog", router=~"$router"'
PROM_FILTER = 'job="openwrt", router=~"$router"'
CLIENT_FILTER = f'{PROM_FILTER}, connection=~"$connection", network=~"$network"'
WIFI_CLIENT_FILTER = f'{PROM_FILTER}, connection="wifi", network=~"$network"'


STATUS_MAPPINGS = [
    {"type": "value", "options": {"1": {"text": "Online", "color": "green", "index": 0}}},
    {"type": "value", "options": {"0": {"text": "Offline", "color": "gray", "index": 1}}},
    {"type": "special", "options": {"match": "null", "result": {"text": "Unavailable", "color": "gray", "index": 2}}},
]

TRUNCATION_MAPPINGS = [
    {"type": "value", "options": {"0": {"text": "Complete", "color": "green", "index": 0}}},
    {"type": "value", "options": {"1": {"text": "Truncated", "color": "red", "index": 1}}},
    {"type": "special", "options": {"match": "null", "result": {"text": "Not collected", "color": "gray", "index": 2}}},
]

OFFLOAD_MAPPINGS = [
    {"type": "value", "options": {"0": {"text": "Accounting OK", "color": "green", "index": 0}}},
    {"type": "value", "options": {"1": {"text": "Accounting unreliable", "color": "red", "index": 1}}},
    {"type": "special", "options": {"match": "null", "result": {"text": "Unavailable", "color": "gray", "index": 2}}},
]

TRAFFIC_STATE_MAPPINGS = [
    {"type": "value", "options": {"0": {"text": "Not collected", "color": "gray", "index": 0}}},
    {"type": "value", "options": {"1": {"text": "Accounting active", "color": "green", "index": 1}}},
    {"type": "value", "options": {"2": {"text": "Accounting unreliable - flow offload enabled", "color": "red", "index": 2}}},
    {"type": "special", "options": {"match": "null", "result": {"text": "Not collected", "color": "gray", "index": 3}}},
]

CONNTRACK_STATE_MAPPINGS = [
    {"type": "value", "options": {"0": {"text": "Not collected", "color": "gray", "index": 0}}},
    {"type": "value", "options": {"1": {"text": "Current table measured", "color": "green", "index": 1}}},
    {"type": "value", "options": {"2": {"text": "Unavailable - flow offload semantics unverified", "color": "red", "index": 2}}},
]

IPV6_MAPPINGS = [
    {"type": "value", "options": {"0": {"text": "No", "color": "gray", "index": 0}}},
    {"type": "value", "options": {"1": {"text": "Yes", "color": "blue", "index": 1}}},
]

LOCAL_MAC_MAPPINGS = [
    {"type": "value", "options": {"0": {"text": "Global", "color": "green", "index": 0}}},
    {"type": "value", "options": {"1": {"text": "Randomized/local", "color": "orange", "index": 1}}},
]


def variables() -> list[dict[str, Any]]:
    return [
        datasource_var("DS_PROMETHEUS", "Prometheus", "prometheus", "prometheus"),
        datasource_var("DS_LOKI", "Loki", "loki", "loki"),
        query_var("router", "Router", 'label_values(node_load1{job="openwrt"}, router)', "openwrt", include_all=True, multi=True),
        query_var("connection", "Connection", f'label_values(openwrt_client_info{{{PROM_FILTER}}}, connection)', "All", include_all=True, multi=True),
        query_var("network", "Network", f'label_values(openwrt_client_info{{{PROM_FILTER}}}, network)', "All", include_all=True, multi=True),
    ]


def lowercase_mac_label(expr: str, label: str = "mac") -> str:
    """Lowercase A-F in a MAC label using PromQL label_replace only."""
    lowered = expr
    for upper, lower in (("A", "a"), ("B", "b"), ("C", "c"), ("D", "d"), ("E", "e"), ("F", "f")):
        for _ in range(12):
            lowered = f'label_replace({lowered}, "{label}", "${{1}}{lower}${{2}}", "{label}", "([^{upper}]*){upper}(.*)")'
    return lowered


HOSTAPD_SIGNAL = lowercase_mac_label(
    f'label_replace(hostapd_station_signal_dbm{{{PROM_FILTER}}}, "mac", "$1", "station", "(.+)")'
)
WIFI_SIGNAL = lowercase_mac_label(f'wifi_station_signal_dbm{{{PROM_FILTER}}}')
WIFI_SIGNAL_RAW = f"{HOSTAPD_SIGNAL} or ({WIFI_SIGNAL} unless on(job, router) {HOSTAPD_SIGNAL})"
WIFI_SIGNAL_BY_MAC = f"max by(mac) ({WIFI_SIGNAL_RAW})"

FLOW_OFFLOAD_ENABLED = f'(max(openwrt_flow_offload_enabled{{{PROM_FILTER}}}) == 1)'
TRAFFIC_AVAILABLE = f'(max(openwrt_client_traffic_collector_available{{{PROM_FILTER}}}) or vector(0))'
TRAFFIC_ACCOUNTING_STATE = (
    f'((({TRAFFIC_AVAILABLE} == 1) unless on() {FLOW_OFFLOAD_ENABLED}) * 0 + 1) '
    f'or ({FLOW_OFFLOAD_ENABLED} * 0 + 2) or vector(0)'
)
CONNTRACK_AVAILABLE = f'(max(openwrt_client_conntrack_collector_available{{{PROM_FILTER}}}) or vector(0))'
CONNTRACK_STATE = (
    f'((({CONNTRACK_AVAILABLE} == 1) unless on() {FLOW_OFFLOAD_ENABLED}) * 0 + 1) '
    f'or ({FLOW_OFFLOAD_ENABLED} * 0 + 2) or vector(0)'
)
ASSOC_EVENTS_AVAILABLE = f'(max(openwrt_wifi_assoc_events_collector_available{{{PROM_FILTER}}}) or vector(0))'


def trusted_traffic(expr: str) -> str:
    """Never render conntrack-derived traffic while flow offload is enabled."""
    return f'({expr}) unless on() {FLOW_OFFLOAD_ENABLED}'


def trusted_conntrack(expr: str) -> str:
    """Fail closed until this router's offload entry semantics are measured."""
    return f'({expr}) unless on() {FLOW_OFFLOAD_ENABLED}'


TRAFFIC_IN = trusted_traffic(
    f'sum by(mac) (rate(openwrt_client_bytes_total{{{PROM_FILTER}, direction="in"}}[$__rate_interval]))'
)
TRAFFIC_OUT = trusted_traffic(
    f'sum by(mac) (rate(openwrt_client_bytes_total{{{PROM_FILTER}, direction="out"}}[$__rate_interval]))'
)
TRAFFIC_SERVICE = trusted_traffic(
    f'sum by(service, direction) (rate(openwrt_client_bytes_total{{{PROM_FILTER}}}[$__rate_interval]))'
)


def field_override(name: str, properties: list[dict[str, Any]]) -> dict[str, Any]:
    return {"matcher": {"id": "byName", "options": name}, "properties": properties}


def regexp_override(pattern: str, properties: list[dict[str, Any]]) -> dict[str, Any]:
    return {"matcher": {"id": "byRegexp", "options": pattern}, "properties": properties}


def availability_expr(metric_name: str, collector: str = "") -> str:
    if collector:
        return (
            f'(max({metric_name}{{{PROM_FILTER}}}) '
            f'* max(node_scrape_collector_success{{{PROM_FILTER}, collector="{collector}"}})) or vector(0)'
        )
    return f"max({metric_name}{{{PROM_FILTER}}}) or vector(0)"


def build_dashboard() -> dict[str, Any]:
    b = DashboardBuilder()
    tabs: list[dict[str, Any]] = []

    overview: list[dict[str, Any]] = []
    b.add(overview, text(1, "", "## Client inventory\nUnified client identity from the OpenWrt clients profile. Missing collectors and unavailable joins are rendered explicitly instead of as zeros."), 0, 0, 24, 3)
    b.add(overview, stat(2, "Inventory Collector", availability_expr("openwrt_client_inventory_collector_available", "client_inventory"), "none", "Collector availability gated by both the client inventory flag and the Lua exporter's per-collector scrape success.", mappings=AVAILABILITY_MAPPINGS, thresholds_key="unavailable", color_mode="value"), 0, 3, 5, 4)
    b.add(overview, stat(3, "Clients", f'count(openwrt_client_info{{{CLIENT_FILTER}}}) or vector(0)', "none", "One current identity series per discovered client.", graph=True, color_mode="value"), 5, 3, 5, 4)
    b.add(overview, stat(4, "WiFi Clients", f'count(openwrt_client_info{{{WIFI_CLIENT_FILTER}}}) or vector(0)', "none", "Clients currently associated to a WiFi interface according to the clients profile.", graph=True, color_mode="value"), 10, 3, 5, 4)
    b.add(overview, stat(5, "Inventory Truncation", f'max(openwrt_client_inventory_truncated{{{PROM_FILTER}}}) or vector(0)', "none", "If this is truncated, the router hit CLIENT_INVENTORY_MAX and client rows are incomplete.", mappings=TRUNCATION_MAPPINGS, thresholds_key="ok_bad", color_mode="value"), 15, 3, 4, 4)
    b.add(overview, stat(6, "Flow Offload", f'max(openwrt_flow_offload_enabled{{{PROM_FILTER}}}) or vector(0)', "none", "When software or hardware flow offload is enabled, nftables and conntrack traffic accounting can under-report or read near zero.", mappings=OFFLOAD_MAPPINGS, thresholds_key="ok_bad", color_mode="value"), 19, 3, 5, 4)
    b.add(overview, stat(11, "Conntrack Entries", CONNTRACK_STATE, "none", "Current per-client conntrack-table occupancy is suppressed while flow-offload entry semantics remain unverified on this router. This is a state count, not conntrack byte accounting.", mappings=CONNTRACK_STATE_MAPPINGS, thresholds_key="unavailable", color_mode="value"), 0, 14, 6, 4)
    b.add(overview, bargauge(7, "Connection Split", [prom_query(f'count by(connection) (openwrt_client_info{{{CLIENT_FILTER}}}) or vector(0)', "{{connection}}", "A")], "none", "Client count by connection type. Unknown is an explicit state from the collector, not a dashboard error.", transformations=[tf("labelsToFields")]), 0, 7, 8, 7)
    b.add(overview, bargauge(8, "Clients by Network", [prom_query(f'count by(network) (openwrt_client_info{{{CLIENT_FILTER}}}) or vector(0)', "{{network}}", "A")], "none", "Client count by logical OpenWrt network label from the clients profile.", transformations=[tf("labelsToFields")]), 8, 7, 8, 7)
    b.add(overview, bargauge(9, "Clients by SSID", [prom_query(f'topk(10, count by(ssid, band) (openwrt_client_info{{{CLIENT_FILTER}, ssid!=""}}))', "{{ssid}} {{band}}", "A")], "none", "Associated WiFi clients by SSID and band. Wired clients intentionally do not appear here.", transformations=[tf("labelsToFields"), tf("limit", {"limitField": 10})]), 16, 7, 8, 7)
    b.add(overview, text(10, "", "## Traffic accounting\nPer-client traffic is keyed directly by MAC from nlbwmon. When software or hardware flow offload is enabled, every nlbwmon traffic panel suppresses its values and the accounting state reports unreliable rather than displaying plausible zeros."), 6, 14, 18, 4)
    tabs.append(b.tab("Overview", overview))

    clients: list[dict[str, Any]] = []
    b.add(clients, table(100, "Client Inventory", [
        prom_query(f'openwrt_client_info{{{CLIENT_FILTER}}}', "", "A", fmt="table", instant=True),
        prom_query(f'openwrt_client_up{{{PROM_FILTER}}}', "", "B", fmt="table", instant=True),
        prom_query(WIFI_SIGNAL_BY_MAC, "", "C", fmt="table", instant=True),
        prom_query(f'openwrt_client_lease_expiry_seconds{{{PROM_FILTER}}} - time()', "", "D", fmt="table", instant=True),
        prom_query(f'openwrt_client_ipv6_addresses{{{PROM_FILTER}}}', "", "E", fmt="table", instant=True),
        prom_query(TRAFFIC_IN, "", "F", fmt="table", instant=True),
        prom_query(TRAFFIC_OUT, "", "G", fmt="table", instant=True),
        prom_query(f'openwrt_client_info{{{CLIENT_FILTER}}} * 0 + on() group_left() ({TRAFFIC_ACCOUNTING_STATE})', "", "H", fmt="table", instant=True),
        prom_query(trusted_conntrack(f'openwrt_client_conntrack_entries{{{PROM_FILTER}}}'), "", "I", fmt="table", instant=True),
    ], "One row per openwrt_client_info client. Traffic is nlbwmon per-MAC accounting; the state column is explicit when collection is absent or flow offload makes conntrack accounting unreliable.", transformations=[
        tf("joinByField", {"byField": "mac", "mode": "outer"}),
        organize(
            exclude=[
                "Time",
                "__name__",
                "Value #A",
                "cluster",
                "endpoint",
                "instance",
                "job",
                "namespace",
                "prometheus",
                "prometheus_replica",
                "service",
            ],
            rename={
                "hostname": "Hostname",
                "mac": "MAC",
                "ip": "IP",
                "router": "Router",
                "ap": "AP",
                "ssid": "SSID",
                "band": "Band",
                "connection": "Connection",
                "network": "Network",
                "mac_type": "MAC Type",
                "has_ipv6": "IPv6",
                "ifname": "Interface",
                "static": "Static",
                "Value #B": "Status",
                "Value #C": "Signal dBm",
                "Value #D": "Lease Remaining",
                "Value #E": "IPv6 Count",
                "Value #F": "Inbound",
                "Value #G": "Outbound",
                "Value #H": "Traffic Accounting",
                "Value #I": "Conntrack Entries",
            },
            index={
                "Hostname": 0,
                "MAC": 1,
                "IP": 2,
                "Router": 3,
                "AP": 4,
                "SSID": 5,
                "Band": 6,
                "Connection": 7,
                "Network": 8,
                "MAC Type": 9,
                "IPv6": 10,
                "IPv6 Count": 11,
                "Status": 12,
                "Signal dBm": 13,
                "Lease Remaining": 14,
                "Inbound": 15,
                "Outbound": 16,
                "Traffic Accounting": 17,
                "Conntrack Entries": 18,
                "Interface": 19,
                "Static": 20,
            },
        ),
        sort_by("Hostname", desc=False),
    ], overrides=[
        field_override("Status", [{"id": "custom.cellOptions", "value": {"type": "color-background"}}, {"id": "mappings", "value": STATUS_MAPPINGS}]),
        field_override("Signal dBm", [{"id": "unit", "value": "dBm"}, {"id": "custom.cellOptions", "value": {"type": "color-background"}}, {"id": "thresholds", "value": thresholds(("red", -90), ("yellow", -70), ("green", -60))}, {"id": "mappings", "value": [{"type": "special", "options": {"match": "null", "result": {"text": "Unavailable", "color": "gray", "index": 0}}}]}]),
        field_override("Lease Remaining", [{"id": "unit", "value": "s"}, {"id": "mappings", "value": [{"type": "special", "options": {"match": "null", "result": {"text": "No lease", "color": "gray", "index": 0}}}]}]),
        regexp_override("/Inbound|Outbound/", [{"id": "unit", "value": "Bps"}, {"id": "mappings", "value": [{"type": "special", "options": {"match": "null", "result": {"text": "Unavailable or unreliable", "color": "gray", "index": 0}}}]}]),
        field_override("Traffic Accounting", [{"id": "custom.cellOptions", "value": {"type": "color-background"}}, {"id": "mappings", "value": TRAFFIC_STATE_MAPPINGS}]),
        field_override("IPv6", [{"id": "mappings", "value": IPV6_MAPPINGS}]),
    ], sort_col="Hostname", sort_desc=False), 0, 0, 24, 14)
    b.add(clients, timeseries(101, "WiFi Signal by Client", [prom_query(f'{WIFI_SIGNAL_BY_MAC} * on(mac) group_left(hostname, ssid, band) openwrt_client_info{{{WIFI_CLIENT_FILTER}}}', "{{hostname}} {{ssid}} {{band}}", "A")], "dBm", "WiFi client RSSI joined to client identity after normalizing upstream station MAC labels to lowercase. Empty means no WiFi signal collector sample exists for the selected client.", thresholds_value=thresholds(("red", -90), ("yellow", -70), ("green", -60)), legend_calcs=["lastNotNull", "min"]), 0, 14, 12, 8)
    b.add(clients, bargauge(102, "Weakest Associated Clients", [prom_query(f'bottomk(10, {WIFI_SIGNAL_BY_MAC} * on(mac) group_left(hostname, ssid, band) openwrt_client_info{{{WIFI_CLIENT_FILTER}}})', "{{hostname}} {{ssid}} {{band}}", "A")], "dBm", "Bottom-10 current WiFi RSSI values. Less negative is better.", thresholds_key="neutral", color_mode="continuous-blues"), 12, 14, 12, 8)
    b.add(clients, timeseries(103, "Client Presence", [prom_query(f'sum(openwrt_client_up{{{PROM_FILTER}}} * on(mac) group_left(connection, network) openwrt_client_info{{{CLIENT_FILTER}}})', "Online", "A"), prom_query(f'count(openwrt_client_info{{{CLIENT_FILTER}}}) - sum(openwrt_client_up{{{PROM_FILTER}}} * on(mac) group_left(connection, network) openwrt_client_info{{{CLIENT_FILTER}}})', "Offline", "B")], "none", "Online and offline client inventory trend. Offline clients still appear in the inventory when the router knows about them.", overrides=[field_override("Online", [{"id": "color", "value": {"mode": "fixed", "fixedColor": GREEN}}]), field_override("Offline", [{"id": "color", "value": {"mode": "fixed", "fixedColor": GRAY}}])]), 0, 22, 12, 7)
    b.add(clients, bargauge(104, "Randomized / Local MACs", [prom_query(f'count by(mac_type) (openwrt_client_info{{{CLIENT_FILTER}}}) or vector(0)', "{{mac_type}}", "A")], "none", "Locally administered MACs are privacy/randomized or synthetic addresses; the dashboard surfaces them without trying to fingerprint a physical device.", transformations=[tf("labelsToFields")]), 12, 22, 12, 7)
    b.add(clients, timeseries(105, "Highest Conntrack Occupancy", [prom_query(trusted_conntrack(f'topk(12, openwrt_client_conntrack_entries{{{PROM_FILTER}}} * on(mac) group_left(hostname, network) openwrt_client_info{{{CLIENT_FILTER}}})'), "{{hostname}} {{network}}", "A")], "none", "Current conntrack rows containing each known client IPv4 address. This is a bounded per-client state count; it is suppressed while flow-offload entry semantics remain unverified.", legend_calcs=["lastNotNull", "max"]), 0, 29, 24, 8)
    tabs.append(b.tab("Clients", clients))

    traffic: list[dict[str, Any]] = []
    b.add(traffic, text(200, "", "## Per-client traffic\nnlbwmon accounts traffic by MAC and a deliberately small service set. The counters reset at nlbwmon accounting-period rollover, which `rate()` and `increase()` handle normally. Flow offload is a hard correctness guard: when enabled, all traffic values below are suppressed and the accounting state is red."), 0, 0, 24, 4)
    b.add(traffic, stat(201, "Traffic Accounting State", TRAFFIC_ACCOUNTING_STATE, "none", "Not collected means nlbwmon is unavailable. Accounting unreliable means software or hardware flow offload is enabled, so all M6 traffic panels suppress values rather than showing wrong zeros.", mappings=TRAFFIC_STATE_MAPPINGS, thresholds_key="ok_bad", color_mode="value"), 0, 4, 8, 4)
    b.add(traffic, stat(202, "Traffic Collector", TRAFFIC_AVAILABLE, "none", "Availability of the nlbwmon textfile collector. A failed schema or unsupported service is fail-closed.", mappings=AVAILABILITY_MAPPINGS, thresholds_key="unavailable", color_mode="value"), 8, 4, 8, 4)
    b.add(traffic, stat(203, "Flow Offload", f'max(openwrt_flow_offload_enabled{{{PROM_FILTER}}}) or vector(0)', "none", "Software or hardware flow offload makes conntrack-derived nlbwmon accounting unreliable.", mappings=OFFLOAD_MAPPINGS, thresholds_key="ok_bad", color_mode="value"), 16, 4, 8, 4)
    b.add(traffic, timeseries(205, "Inbound by Client", [prom_query(f'topk(12, {TRAFFIC_IN})', "{{mac}}", "A")], "Bps", "Per-MAC nlbwmon inbound rate. Suppressed when flow offload is enabled.", overrides=[field_override("Inbound", [{"id": "color", "value": {"mode": "fixed", "fixedColor": GREEN}}])]), 0, 8, 12, 8)
    b.add(traffic, timeseries(206, "Outbound by Client", [prom_query(f'topk(12, {TRAFFIC_OUT})', "{{mac}}", "A")], "Bps", "Per-MAC nlbwmon outbound rate. Suppressed when flow offload is enabled.", overrides=[field_override("Outbound", [{"id": "color", "value": {"mode": "fixed", "fixedColor": BLUE}}])]), 12, 8, 12, 8)
    b.add(traffic, timeseries(207, "Traffic by Service", [prom_query(TRAFFIC_SERVICE, "{{service}} {{direction}}", "A")], "Bps", "Bounded service buckets from the installed nlbwmon protocol file. Suppressed when flow offload is enabled."), 0, 16, 24, 10)
    tabs.append(b.tab("Traffic", traffic))

    quality: list[dict[str, Any]] = []
    b.add(quality, text(300, "", "## Data quality\nCollector state, joinability, and identity gaps are surfaced as first-class values. A missing join or collector is not treated as a healthy zero."), 0, 0, 24, 3)
    b.add(quality, table(301, "Collector Availability", [
        prom_query(f'openwrt_client_inventory_collector_available{{{PROM_FILTER}}}', "", "A", fmt="table", instant=True),
        prom_query(f'node_scrape_collector_success{{{PROM_FILTER}, collector="client_inventory"}}', "", "B", fmt="table", instant=True),
        prom_query(f'openwrt_client_inventory_truncated{{{PROM_FILTER}}}', "", "C", fmt="table", instant=True),
        prom_query(f'openwrt_flow_offload_enabled{{{PROM_FILTER}}}', "", "D", fmt="table", instant=True),
    ], "Raw availability and offload-state series needed to trust or reject the client dashboard values.", transformations=[tf("merge"), organize(exclude=["Time", "__name__", "cluster", "endpoint", "instance", "job", "namespace", "prometheus", "prometheus_replica", "service"], rename={"mode": "Offload Mode", "collector": "Collector", "router": "Router", "Value #A": "Inventory Available", "Value #B": "Scrape Success", "Value #C": "Truncated", "Value #D": "Offload Enabled"}, index={"Router": 0, "Collector": 1, "Offload Mode": 2, "Inventory Available": 3, "Scrape Success": 4, "Truncated": 5, "Offload Enabled": 6})], overrides=[regexp_override("/Inventory Available|Scrape Success/", [{"id": "custom.cellOptions", "value": {"type": "color-background"}}, {"id": "mappings", "value": AVAILABILITY_MAPPINGS}]), field_override("Truncated", [{"id": "custom.cellOptions", "value": {"type": "color-background"}}, {"id": "mappings", "value": TRUNCATION_MAPPINGS}]), field_override("Offload Enabled", [{"id": "custom.cellOptions", "value": {"type": "color-background"}}, {"id": "mappings", "value": OFFLOAD_MAPPINGS}])], sort_col="Router", sort_desc=False), 0, 3, 24, 8)
    b.add(quality, stat(302, "No Hostname", f'count(openwrt_client_info{{{CLIENT_FILTER}, hostname=~"unknown_.*"}}) or vector(0)', "none", "Clients without a usable hostname remain visible in the inventory and retain MAC-keyed nlbwmon traffic accounting.", graph=True, color_mode="value"), 0, 11, 4, 4)
    b.add(quality, stat(303, "Randomized / Local", f'count(openwrt_client_info{{{CLIENT_FILTER}, mac_type="local"}}) or vector(0)', "none", "Clients using a locally administered MAC address. This includes privacy-randomized and synthetic addresses.", graph=True, color_mode="value"), 4, 11, 4, 4)
    b.add(quality, stat(304, "Missing IP", f'count(openwrt_client_info{{{CLIENT_FILTER}, ip=""}}) or vector(0)', "none", "Inventory rows without an IPv4 address. This should remain low and explicit.", graph=True, color_mode="value"), 8, 11, 4, 4)
    b.add(quality, stat(305, "WiFi Missing SSID", f'count(openwrt_client_info{{{WIFI_CLIENT_FILTER}, ssid=""}}) or vector(0)', "none", "WiFi clients whose association data is missing an SSID.", graph=True, color_mode="value"), 12, 11, 4, 4)
    b.add(quality, stat(306, "WiFi Missing Band", f'count(openwrt_client_info{{{WIFI_CLIENT_FILTER}, band=""}}) or vector(0)', "none", "WiFi clients whose association data is missing a radio band.", graph=True, color_mode="value"), 16, 11, 4, 4)
    b.add(quality, stat(307, "No Signal Join", f'count(openwrt_client_info{{{WIFI_CLIENT_FILTER}}} unless on(mac) {WIFI_SIGNAL_BY_MAC}) or vector(0)', "none", "Associated WiFi clients without a matching signal sample after MAC-case normalization.", graph=True, color_mode="value"), 20, 11, 4, 4)
    b.add(quality, bargauge(308, "IPv6 Address Count", [prom_query(f'openwrt_client_ipv6_addresses{{{PROM_FILTER}}} * on(mac) group_left(hostname) openwrt_client_info{{{CLIENT_FILTER}}}', "{{hostname}}", "A")], "none", "IPv6 presence is counted, not exposed as address labels, avoiding privacy-address churn.", transformations=[tf("limit", {"limitField": 25})]), 0, 15, 12, 7)
    b.add(quality, bargauge(309, "Local MAC Clients", [prom_query(f'openwrt_client_info{{{CLIENT_FILTER}, mac_type="local"}}', "{{hostname}}", "A")], "none", "Clients flagged by mac_type=local. This replaces OUI/vendor lookup and avoids fingerprinting randomised addresses.", mappings=LOCAL_MAC_MAPPINGS, transformations=[tf("limit", {"limitField": 25})]), 12, 15, 12, 7)
    tabs.append(b.tab("Data Quality", quality))

    roaming: list[dict[str, Any]] = []
    b.add(roaming, text(400, "", "## WiFi roaming\nRaw hostapd association and disconnection lines are kept in Loki for per-client investigation. Prometheus receives only the bounded aggregate counter by AP, SSID, and event; it never carries a MAC, remote IP, domain, port, IPv6 address, or vendor label."), 0, 0, 24, 4)
    b.add(roaming, stat(401, "Association Event Collector", ASSOC_EVENTS_AVAILABLE, "none", "Availability of the bounded AP/SSID/event counter. Loki raw-event panels remain useful even when this aggregate counter is unavailable.", mappings=CONNTRACK_STATE_MAPPINGS, thresholds_key="unavailable", color_mode="value"), 0, 4, 8, 4)
    b.add(roaming, loki_stat(402, "Roam Events (1h)", f'sum(count_over_time({{{LOKI_FILTER}}} |~ "AP-STA-(CONNECTED|DISCONNECTED)" [1h])) or vector(0)', "Hostapd association/disconnection lines in Loki. A zero is a valid no-events state on a single AP; an empty Loki result is rendered as zero, not an error."), 8, 4, 8, 4)
    b.add(roaming, timeseries(403, "Association Events by AP / SSID", [prom_query(f'sum by(ap, ssid, event) (increase(openwrt_wifi_assoc_events_total{{{PROM_FILTER}}}[$__rate_interval]))', "{{ap}} {{ssid}} {{event}}", "A")], "none", "Bounded aggregate association events. The label budget is AP x SSID x {connected, disconnected}; no MAC label is emitted."), 16, 4, 8, 4)
    b.add(roaming, logs_panel(404, "Roaming Timeline", f'{{{LOKI_FILTER}}} |~ "AP-STA-(CONNECTED|DISCONNECTED)"', "Raw hostapd events, including the station MAC, live only in Loki. No events is an expected, clean empty timeline on a single-AP installation."), 0, 8, 24, 14)
    tabs.append(b.tab("Roaming", roaming))

    spec: dict[str, Any] = {
        "title": "OpenWrt - Clients",
        "description": "Client inventory dashboard for OpenWrt identity, bounded conntrack occupancy, MAC-keyed nlbwmon traffic accounting, and Loki-first WiFi roaming detail.",
        "tags": ["openwrt", "clients", "router"],
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
        "metadata": {"name": "openwrt-clients"},
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
    assert dash["metadata"]["name"] == "openwrt-clients"
    spec = dash["spec"]
    assert spec["title"] == "OpenWrt - Clients"
    assert spec["layout"]["kind"] == "TabsLayout"
    assert [tab["spec"]["title"] for tab in spec["layout"]["spec"]["tabs"]] == [
        "Overview",
        "Clients",
        "Traffic",
        "Data Quality",
        "Roaming",
    ]

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
        if viz != "text":
            defaults = panel_spec["vizConfig"]["spec"]["fieldConfig"]["defaults"]
            assert "unit" in defaults, f"missing unit: {key} {panel_spec['title']}"
            assert defaults["unit"] != "short", f"generic short unit: {key} {panel_spec['title']}"
        # noValue belongs in fieldConfig.defaults; under options it is silently
        # ignored by Grafana. Regression check for the 2026-07-23 fix -- see
        # docs/client-topology-and-netflow-plan.md gap #4.
        assert "noValue" not in panel_spec["vizConfig"]["spec"]["options"], f"noValue in panel options: {key}"
        assert "pluginVersion" not in json.dumps(element)
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
    unused = defined_vars - referenced_vars - {"DS_PROMETHEUS", "DS_LOKI"}
    assert not unused, f"unused variables: {sorted(unused)}"

    strings = iter_strings(spec)
    text_blob = json.dumps(dash, sort_keys=True)
    assert "schemaVersion" not in text_blob
    assert "pluginVersion" not in text_blob
    assert "uid" not in dash["metadata"]
    assert PROM_DS in text_blob
    assert LOKI_DS in text_blob
    assert any('router=~"$router"' in value for value in strings)
    assert any('job="openwrt"' in value for value in strings)
    query_exprs: list[str] = []
    for element in spec["elements"].values():
        queries = element["spec"]["data"]["spec"]["queries"]
        for query in queries:
            query_exprs.append(query["spec"]["query"]["spec"].get("expr", ""))
    assert any("openwrt_client_bytes_total" in expr for expr in query_exprs)
    assert any("openwrt_client_conntrack_entries" in expr for expr in query_exprs)
    assert any("openwrt_wifi_assoc_events_total" in expr for expr in query_exprs)
    assert any("AP-STA-(CONNECTED|DISCONNECTED)" in expr for expr in query_exprs)
    assert not any("openwrt_device_traffic_bytes_total" in expr for expr in query_exprs)
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
