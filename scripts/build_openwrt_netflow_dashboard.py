#!/usr/bin/env python3
"""Build the NetFlow dashboard for OpenWrt.

Unlike every other generator in this repository, most of this dashboard reads
from ClickHouse rather than Prometheus: per-flow records are written there by
Akvorado and never enter Prometheus at all. Only the health tab is PromQL,
because "is the exporter running" is a router metric.

Two consequences shape the SQL throughout:

  - Every byte and packet expression multiplies by `SamplingRate`. Akvorado
    stores the raw counter and the rate it was sampled at; forgetting the
    multiplication silently under-reports by exactly that factor.
  - Per-host and per-port panels query the raw `flows` table, never the
    `flows_1m`/`flows_1h` rollups, which drop SrcAddr/DstAddr/SrcPort/DstPort.
    That bounds those panels to the interval-0 retention window configured in
    akvorado/akvorado.yaml (7 days by default).

The dashboard is safe to provision when the netflow profile is not enabled:
the ClickHouse datasource simply fails to connect and the panels show an error
rather than a plausible zero.

Six tabs, in the order an operator needs them:

  - Flow Overview   what happened, and how much of it left the LAN
  - Applications    which ports, protocols, and packet shapes carried it
  - External        which networks and countries, as ranking, map, and graph
  - Security Signals shapes occasionally worth explaining -- never verdicts
  - Pipeline Health can these numbers be trusted at all (Prometheus)
  - Pipeline Internals where in the collector it is struggling (Prometheus)

Several fields that look useful are deliberately unused, each verified empty
against the live cluster rather than assumed:

  - SrcGeoCity/DstGeoCity are 0% populated. The City database is not loaded
    on purpose -- City and Country together OOM the orchestrator at its
    configured memory limit. There is no city panel; a v2 one was removed.
  - DstASPath, Dst1st/2nd/3rdAS and the community columns need a BGP peering
    source this deployment does not have. All 0%.
  - ICMP type and code are not exported by softflowd: DstPort is a constant 0
    on every ICMP flow. ClickHouse's `icmp` dictionary can decode
    (proto, type, code) but has nothing here to decode, so the Security
    Signals tab shows ICMP volume and says why it shows nothing more.
  - ExporterRole/Site/Region/Tenant and the Src/DstNet* equivalents are
    carrier multi-tenancy fields, empty in a single-router home deployment.

Building panels on any of them would produce a permanent "no data" wall,
which is indistinguishable from a broken pipeline -- the one failure this
dashboard's whole design is meant to prevent.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .build_openwrt_operations_dashboard import (
    AVAILABILITY_MAPPINGS,
    BLUE,
    GRAY,
    GREEN,
    ORANGE,
    PURPLE,
    RED,
    THRESHOLDS,
    YELLOW,
    DashboardBuilder,
    bargauge,
    color_override,
    data_group,
    datasource_var,
    heatmap,
    limit,
    organize,
    panel,
    prom_query,
    piechart,
    query_var,
    sort_by,
    stat,
    stable_json,
    state_timeline,
    table,
    text,
    thresholds,
    timeseries,
)


ROOT = Path(__file__).resolve().parents[1]
OUTS = [
    ROOT / "grafana-dashboard-exports/openwrt-netflow-v2.json",
    ROOT / "grafana/provisioning/dashboards/openwrt-netflow-v2.json",
]

PROM_DS = "${DS_PROMETHEUS}"
CH_DS = "${DS_CLICKHOUSE}"
PROM_FILTER = 'job="openwrt", router=~"$router"'

# Akvorado's ExporterName comes from akvorado/exporters.yaml, which the operator
# writes; Prometheus's `router` label comes from ROUTER_TARGETS in .env. They are
# two independent naming decisions, so this filter only works when they are kept
# in step -- which exporters.yaml.example says explicitly. $__conditionalAll
# collapses to a no-op when the variable is set to All.
ROUTER_SQL = "$__conditionalAll(ExporterName IN (${router:singlequote}), $router)"

# LAN-side vs internet-side, derived from IP address role rather than from
# interface boundary.
#
# InIfBoundary CANNOT be used here. softflowd sets if_index_in and if_index_out
# to the same value -- the ifIndex of its single capture device (netflow9.c:295,
# `dc[0]->if_index_in = dc[0]->if_index_out = htonl(ifidx)`). It has no concept
# of separate ingress and egress interfaces, so InIfBoundary is constant across
# the entire dataset: either every flow looks external or none does. Filtering
# on it yields all rows or zero rows, never the split it appears to promise.
#
# SrcNetRole/DstNetRole come from `clickhouse.networks` in akvorado.yaml, which
# maps the RFC1918 ranges to role "internal". That works correctly because the
# capture happens on the LAN bridge, before SNAT, so both addresses are real.
LOCAL_SRC = "SrcNetRole = 'internal'"
LOCAL_DST = "DstNetRole = 'internal'"
# A flow entering the local network from an internet-side source.
EXTERNAL_SRC = "SrcNetRole != 'internal'"
# A flow leaving the local network.
EXTERNAL_DST = "DstNetRole != 'internal'"
# A flow that crosses the boundary in either direction.
CROSSES_BOUNDARY = "(SrcNetRole != 'internal' OR DstNetRole != 'internal')"

# SrcCountry/DstCountry are ClickHouse FixedString(2). Missing country values
# render as two NUL bytes (`\0\0`) unless stripped before grouping.
SRC_COUNTRY = "replaceRegexpAll(toString(SrcCountry), '\\\\x00', '')"
DST_COUNTRY = "replaceRegexpAll(toString(DstCountry), '\\\\x00', '')"

# EType is the ethertype: 0x0800 IPv4, 0x86DD IPv6. Both are
# populated, roughly 91%/9% of flows.
IPV6 = "EType = 34525"


def ip_display(column: str) -> str:
    """Render an address column the way an operator would write it.

    SrcAddr/DstAddr are ClickHouse `IPv6`, so IPv4 addresses come back in
    their v4-mapped form (`::ffff:192.168.0.1`). Stripping the prefix is
    purely cosmetic but it is what makes a host column scannable, and it
    leaves real IPv6 addresses untouched.
    """
    return f"replaceRegexpAll(IPv6NumToString({column}), '^::ffff:', '')"

# TCPFlags is the cumulative OR of every packet's flags over the flow's
# lifetime, not one packet's flags. That is what makes these combinations
# readable as a flow outcome rather than as a packet type: a flow that carries
# both SYN and FIN was set up and torn down inside the capture window.
#
# Bit values: FIN 1, SYN 2, RST 4, PSH 8, ACK 16, URG 32, ECE 64, CWR 128.
#
# Order matters. RST is tested before the FIN/SYN combinations because a flow
# that was reset after a handshake is a reset, not a clean close, and the
# unanswered-SYN test comes first because it is the one shape that is
# interesting precisely when nothing else was ever set.
#
# TCP shape categories are kept explicit so operators can distinguish completed,
# reset, still-open, and unanswered flows.
TCP_SHAPE = (
    "multiIf("
    "bitAnd(TCPFlags, 2) != 0 AND bitAnd(TCPFlags, 16) = 0, 'SYN, never answered', "
    "bitAnd(TCPFlags, 4) != 0, 'RST - reset or refused', "
    "bitAnd(TCPFlags, 1) != 0 AND bitAnd(TCPFlags, 2) != 0, 'Completed - opened and closed', "
    "bitAnd(TCPFlags, 1) != 0, 'Closed - handshake not captured', "
    "bitAnd(TCPFlags, 2) != 0, 'Open - handshake, still running', "
    "bitAnd(TCPFlags, 16) != 0, 'Established - data only', "
    "'Other')"
)

# Colour contract for the decoded TCP shapes. Green is only for the outcome
# that is unambiguously fine; the two shapes that can mean a service refused
# or never answered get the warning colours; long-lived and mid-capture flows
# are neutral, because on a 60-second maxlife they are the normal case rather
# than a fault.
TCP_SHAPE_COLORS = [
    color_override("Completed - opened and closed", GREEN),
    color_override("RST - reset or refused", ORANGE),
    color_override("SYN, never answered", RED),
    color_override("Open - handshake, still running", BLUE),
    color_override("Established - data only", PURPLE),
    color_override("Closed - handshake not captured", GRAY),
    color_override("Other", GRAY),
]

# Packet-size heatmap buckets, as Prometheus-style upper bounds so Grafana
# reads the column names as bucket edges. Akvorado's own PacketSizeBucket
# column is a LowCardinality(String) with labels like "768-1023", which sorts
# lexically rather than numerically and cannot be read as a bucket bound;
# PacketSize (the per-flow mean, verified populated: min 32, max 11240,
# mean 121) is bucketed here instead so the axis is genuinely numeric.
PACKET_SIZE_EDGES = [64, 128, 256, 512, 1024, 1500]

# One colour per direction bucket, pinned so the absolute and percent-stacked
# throughput panels read as the same three series rather than as two unrelated
# charts with Grafana's palette assigned in whatever order the rows arrived.
# These are identity colours, not severity: nothing here is a fault.
DIRECTION_COLORS = [
    color_override("outbound", BLUE),
    color_override("inbound", PURPLE),
    color_override("local", GRAY),
]

# Port-number buckets with human names. A convenience grouping, not DPI --
# it is only as right as the assumption that a port implies a service, which
# is exactly the assumption the Applications tab intro warns about. Shared by
# the donut and its stacked-over-time companion so the two cannot drift.
SERVICE_CLASS = (
    "multiIf("
    "DstPort IN (53, 853) OR SrcPort IN (53, 853), 'DNS / encrypted DNS', "
    "DstPort IN (80, 443, 8080, 8443) OR SrcPort IN (80, 443, 8080, 8443), 'Web / QUIC', "
    "DstPort IN (9100, 9090, 3000, 8081, 8123) OR SrcPort IN (9100, 9090, 3000, 8081, 8123), 'Monitoring stack', "
    "DstPort IN (22, 2222) OR SrcPort IN (22, 2222), 'SSH / admin', "
    "DstPort = 123 OR SrcPort = 123, 'NTP', "
    "DstPort = 5353 OR SrcPort = 5353, 'mDNS', "
    "DstPort = 1900 OR SrcPort = 1900, 'SSDP / discovery', "
    "'Other')"
)


def pivot_columns(label_expr: str, names: list[str], agg: str) -> str:
    """Turn a label expression into one numeric column per label value.

    The grafana-clickhouse-datasource does NOT split a long-format result
    (time, label, value) into one series per label. It returns a single series
    named after the value column, so a three-way direction split renders as
    one line called "bps" -- wrong, and wrong in a way that looks like a
    working panel. Validate it against the Grafana version used by the operator.

    Two fixes exist. For a dynamic label set, `partitionByValues` splits the
    frame browser-side but names the series "<value column> <label>". For a
    known, fixed label set, pivoting in SQL is better: the column name IS the
    series name, so the names stay clean and `byName` colour overrides match
    them. That is what this builds.

    Pivoting on the computed label rather than on each label's own condition
    is deliberate: expressions like SERVICE_CLASS and TCP_SHAPE are ordered
    `multiIf`s where the first match wins, so re-deriving per-label
    conditions would double-count rows that satisfy more than one branch.
    Comparing against the label keeps the panel and its donut companion
    exactly consistent by construction.
    """
    return ", ".join(
        f"sumIf({agg}, {label_expr} = '{name}') AS \"{name}\"" for name in names
    )


# Splits the frame into one series per distinct label value, browser-side.
# Needed for the top-N panels, where the label set is whatever the data
# happened to contain and so cannot be pivoted in SQL. Series come out named
# "<value column> <label>" (e.g. "bps 443"), which is why the fixed-set panels
# use pivot_columns instead.
def partition_by(field: str) -> dict[str, Any]:
    return {
        "kind": "partitionByValues",
        "spec": {
            "id": "partitionByValues",
            "options": {"fields": [field], "keepFields": False},
        },
    }


DIRECTIONS = ["outbound", "inbound", "local"]
DIRECTION_EXPR = (
    "multiIf(SrcNetRole = 'internal' AND DstNetRole = 'internal', 'local', "
    "SrcNetRole = 'internal', 'outbound', 'inbound')"
)

SERVICE_CLASSES = [
    "DNS / encrypted DNS",
    "Web / QUIC",
    "Monitoring stack",
    "SSH / admin",
    "NTP",
    "mDNS",
    "SSDP / discovery",
    "Other",
]

TCP_SHAPES = [
    "Completed - opened and closed",
    "RST - reset or refused",
    "SYN, never answered",
    "Open - handshake, still running",
    "Established - data only",
    "Closed - handshake not captured",
    "Other",
]


def packet_size_columns() -> str:
    """One sumIf column per packet-size bucket, named by its upper bound."""
    parts = []
    previous = 0
    for edge in PACKET_SIZE_EDGES:
        lower = f"PacketSize >= {previous} AND " if previous else ""
        parts.append(f'sumIf(Packets * SamplingRate, {lower}PacketSize < {edge}) AS "{edge}"')
        previous = edge
    parts.append(
        f'sumIf(Packets * SamplingRate, PacketSize >= {PACKET_SIZE_EDGES[-1]}) AS "+Inf"'
    )
    return ", ".join(parts)


def ch_query(sql: str, ref: str = "A", fmt: int = 1) -> dict[str, Any]:
    """A ClickHouse SQL query in the v2 dashboard schema.

    format 1 is time series, 2 is table -- the plugin's own enum. Kept
    alongside prom_query/loki_query in shape so the panel constructors in
    build_openwrt_operations_dashboard can take it unchanged.
    """
    return {
        "kind": "PanelQuery",
        "spec": {
            "refId": ref,
            "hidden": False,
            "query": {
                "kind": "DataQuery",
                "group": "grafana-clickhouse-datasource",
                "version": "v0",
                "datasource": {"name": CH_DS},
                "spec": {
                    "rawSql": sql,
                    "editorType": "sql",
                    "format": fmt,
                    "queryType": "table",
                },
            },
        },
    }


def ch_stat(pid: int, title: str, sql: str, unit: str, desc: str) -> tuple[str, dict[str, Any]]:
    """Single-value tile backed by ClickHouse.

    The shared `stat()` helper builds its own prom_query and so cannot be used
    for flow data.
    """
    return panel(
        pid,
        title,
        "stat",
        [ch_query(sql, fmt=2)],
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
            "thresholds": THRESHOLDS["neutral"],
            "mappings": [],
            "color": {"mode": "thresholds"},
            "noValue": "No flows",
        },
    )


def ch_geomap(pid: int, title: str, sql: str, lookup_field: str, desc: str) -> tuple[str, dict[str, Any]]:
    """World map of a two-letter country code column, sized by bytes.

    ClickHouse has no coordinates -- only the ISO 3166-1 alpha-2 code that
    MaxMind's Country database resolved. The marker layer's own `lookup`
    location mode resolves those against Grafana's built-in countries
    gazetteer, which is keyed on exactly that alpha-2 form.

    Do NOT reach for the `fieldLookup` transformation here. It is the
    documented way to attach coordinates to a frame, but on a geomap it
    fails: the transform errors with "missing frame in gazetteer" (browser
    console only) and the panel renders a bare basemap with **no panel-level
    error at all**. All three transform-based variants were tried against a
    the bundled Grafana -- `gazetteer` as a path, as a label, and
    paired with an explicit `coords` location -- and every one rendered an
    empty map. The geomap's built-in lookup is the mechanism that works, and
    it needs no transformation.

    Kept local rather than shared: it is the only geographic data anywhere in
    this repository, and the transform chain is specific to the FixedString(2)
    country columns Akvorado writes.
    """
    return panel(
        pid,
        title,
        "geomap",
        [ch_query(sql, fmt=2)],
        "bytes",
        desc,
        options={
            "basemap": {"config": {}, "name": "Basemap", "type": "default"},
            "controls": {
                "mouseWheelZoom": False,
                "showAttribution": True,
                "showDebug": False,
                "showMeasure": False,
                "showScale": False,
                "showZoom": True,
            },
            "layers": [
                {
                    "config": {
                        "showLegend": True,
                        "style": {
                            "color": {"field": "bytes", "fixed": BLUE},
                            "opacity": 0.6,
                            "rotation": {"fixed": 0, "max": 360, "min": -360, "mode": "mod"},
                            "size": {"field": "bytes", "fixed": 5, "max": 24, "min": 5},
                            "symbol": {"fixed": "img/icons/marker/circle.svg", "mode": "fixed"},
                            "symbolAlign": {"horizontal": "center", "vertical": "center"},
                            "textConfig": {
                                "fontSize": 12,
                                "offsetX": 0,
                                "offsetY": 0,
                                "textAlign": "center",
                                "textBaseline": "middle",
                            },
                        },
                    },
                    "location": {
                        "mode": "lookup",
                        "lookup": lookup_field,
                        "gazetteer": "public/gazetteer/countries.json",
                    },
                    "name": "Countries",
                    "tooltip": True,
                    "type": "markers",
                }
            ],
            "tooltip": {"mode": "details"},
            "view": {"allLayers": True, "id": "zero", "lat": 25, "lon": 0, "zoom": 1.5},
        },
        field_defaults={
            "color": {"mode": "continuous-BlPu"},
            "thresholds": THRESHOLDS["neutral"],
        },
    )


def ch_nodegraph(pid: int, title: str, nodes_sql: str, edges_sql: str, desc: str) -> tuple[str, dict[str, Any]]:
    """Local hosts and the external networks they talk to, as a graph.

    The node graph is the strictest panel in Grafana about frame shape: it
    needs two frames, recognised here by their refIds `nodes` and `edges`.
    The nodes frame must carry a unique `id`; the edges frame must carry a
    unique `id` plus `source`/`target` that match node ids exactly. An edge
    pointing at an id no node supplies crashes the panel rather than
    degrading, which is why both queries derive their ids from the same
    expressions over the same table and time filter.

    Cardinality was checked live before building this: 19 local hosts and 59
    external ASes over 6 hours, 174 edges. Grafana only displays 200 nodes
    before hiding the rest behind cluster markers, so the LIMITs below keep
    the graph inside that budget even if the LAN grows or the range widens.
    """
    return panel(
        pid,
        title,
        "nodeGraph",
        [
            ch_query(nodes_sql, ref="nodes", fmt=2),
            ch_query(edges_sql, ref="edges", fmt=2),
        ],
        "bytes",
        desc,
        options={
            "zoomMode": "cooperative",
            # Two clean tiers (local hosts on one side, external networks on
            # the other) is exactly the shape `layered` renders well; the
            # force layout turns a bipartite graph into crossing spaghetti.
            "layoutAlgorithm": "layered",
            "nodes": {"mainStatUnit": "bytes"},
            "edges": {"mainStatUnit": "bytes"},
        },
        field_defaults={"thresholds": THRESHOLDS["neutral"]},
    )


def variables() -> list[dict[str, Any]]:
    return [
        datasource_var("DS_PROMETHEUS", "Prometheus", "prometheus", "prometheus"),
        datasource_var("DS_CLICKHOUSE", "ClickHouse", "grafana-clickhouse-datasource", "ClickHouse"),
        query_var(
            "router",
            "Router",
            'label_values(node_load1{job="openwrt"}, router)',
            "All",
            include_all=True,
            multi=True,
        ),
    ]


def build_dashboard() -> dict[str, Any]:
    builder = DashboardBuilder()
    tabs: list[dict[str, Any]] = []

    # ── Flow Overview ────────────────────────────────────────────────────────

    overview: list[dict[str, Any]] = []
    builder.add(overview, text(1, "", (
        "## Per-flow traffic\n"
        "NetFlow v9 from softflowd on the router, collected by Akvorado and stored in ClickHouse. "
        "Byte counts are multiplied by the reported sampling rate.\n\n"
        "**Coverage caveat:** softflowd captures with libpcap, so traffic forwarded by the switch ASIC "
        "under hardware flow offload never reaches it. Check the **Pipeline Health** tab before treating "
        "these totals as complete."
    )), 0, 0, 24, 4)

    # Hero band. Six tiles, and the two that lead the story -- how much of
    # this traffic ever leaves the house, and how much of it is IPv6 -- were
    # previously buried on tab 3 or absent entirely.
    builder.add(overview, ch_stat(2, "Flows", (
        "SELECT count() AS flows FROM flows "
        f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL}"
    ), "none", "Flow records stored in the selected range."), 0, 4, 4, 4)

    builder.add(overview, ch_stat(3, "Traffic", (
        "SELECT sum(Bytes * SamplingRate) AS bytes FROM flows "
        f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL}"
    ), "bytes", "Total bytes in the selected range, sampling-rate corrected."), 4, 4, 4, 4)

    builder.add(overview, ch_stat(10, "Peak Bitrate", (
        "SELECT max(bps) AS bps FROM ("
        "SELECT $__timeInterval(TimeReceived) AS time, "
        "sum(Bytes * SamplingRate) * 8 / $__interval_s AS bps "
        "FROM flows "
        f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL} "
        "GROUP BY time)"
    ), "bps", "Highest sampling-rate-corrected bitrate bucket in the selected range."), 8, 4, 4, 4)

    # The single most interesting number in the whole dataset on a home
    # network: almost everything stays on the LAN. Measured in bytes rather
    # than flows, because the question is "how much of my traffic pays for
    # internet bandwidth", not "how many conversations happened".
    builder.add(overview, ch_stat(12, "External Share", (
        "SELECT round(100 * sumIf(Bytes * SamplingRate, "
        f"{CROSSES_BOUNDARY}) "
        "/ greatest(sum(Bytes * SamplingRate), 1)) AS pct FROM flows "
        f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL}"
    ), "percent", (
        "Share of bytes that cross the local network boundary rather than staying LAN-local. "
        "On a typical home network this is low -- most bytes are between local devices -- which is "
        "the context every other panel on this dashboard should be read in. Derived from address "
        "role, not interface boundary."
    )), 12, 4, 4, 4)

    # Deliberately flow share, not byte share. IPv6 carries ~9% of flows but
    # well under 1% of bytes here, because the bulk transfers are still IPv4;
    # a byte-share tile would read 0% and look like broken enrichment.
    builder.add(overview, ch_stat(13, "IPv6 Flow Share", (
        f"SELECT round(100 * countIf({IPV6}) / greatest(count(), 1)) AS pct FROM flows "
        f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL}"
    ), "percent", (
        "Share of flow records carried over IPv6, from the ethertype.\n\n"
        "This is a share of **flows**, not of bytes. The two differ sharply here: IPv6 carries a "
        "meaningful fraction of conversations (DNS, mDNS, NTP, service discovery) while the large "
        "transfers are still IPv4, so byte share reads near zero and would look like an enrichment "
        "failure rather than a real property of the traffic."
    )), 16, 4, 4, 4)

    builder.add(overview, ch_stat(4, "Local Hosts", (
        "SELECT uniq(SrcAddr) AS hosts FROM flows "
        f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL} AND SrcNetRole = 'internal'"
    ), "none", "Distinct local source addresses seen. Depends on the `networks` prefixes in akvorado.yaml matching your addressing."), 20, 4, 4, 4)

    builder.add(overview, ch_stat(5, "External Peers", (
        "SELECT uniq(DstAddr) AS peers FROM flows "
        f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL} AND {EXTERNAL_DST}"
    ), "none", "Distinct internet-side addresses contacted."), 0, 8, 6, 4)

    builder.add(overview, ch_stat(11, "ASN Coverage", (
        "SELECT round(100 * countIf(SrcAS != 0 OR DstAS != 0) / greatest(count(), 1)) AS pct "
        "FROM flows "
        f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL} AND {CROSSES_BOUNDARY}"
    ), "percent", "Share of boundary-crossing flows with a source or destination AS after GeoIP/ASN enrichment."), 6, 8, 6, 4)

    # InIfSpeed is a constant 1000 (Mbps) in this deployment -- softflowd
    # reports the capture device's link speed. Reading the denominator out of
    # the data rather than hardcoding 1e9 means this stays correct if the
    # router is ever moved onto a faster or slower link. greatest(..., 1)
    # guards the divide when no flows matched at all.
    builder.add(overview, panel(
        14,
        "Peak Link Utilization",
        "gauge",
        [ch_query(
            "SELECT max(bps) / greatest(max(link_bps), 1) AS util FROM ("
            "SELECT $__timeInterval(TimeReceived) AS time, "
            "sum(Bytes * SamplingRate) * 8 / $__interval_s AS bps, "
            "max(InIfSpeed) * 1000000 AS link_bps "
            "FROM flows "
            f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL} "
            "GROUP BY time)",
            fmt=2,
        )],
        "percentunit",
        (
            "Peak bitrate as a fraction of the capture interface's reported link speed "
            "(InIfSpeed, a constant 1 Gbps here).\n\n"
            "This is a **lower bound**: anything hardware flow offload hid from softflowd is missing "
            "from the numerator, so real link utilization is at least this high. Use it for "
            "'is the link anywhere near saturated', not for capacity planning."
        ),
        options={
            "minVizHeight": 75,
            "minVizWidth": 75,
            "orientation": "auto",
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "showThresholdLabels": False,
            "showThresholdMarkers": True,
            "sizing": "auto",
        },
        field_defaults={
            "min": 0,
            "max": 1,
            "thresholds": THRESHOLDS["capacity"],
            "noValue": "No flows",
        },
    ), 12, 8, 12, 4)

    builder.add(overview, timeseries(6, "Throughput by Direction", [ch_query(
        "SELECT $__timeInterval(TimeReceived) AS time, "
        f"{pivot_columns(DIRECTION_EXPR, DIRECTIONS, 'Bytes * SamplingRate * 8 / $__interval_s')} "
        "FROM flows "
        f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL} "
        "GROUP BY time ORDER BY time"
    )], "bps", (
        "Bit rate split into outbound, inbound, and LAN-local, derived from whether each address "
        "falls in one of the `clickhouse.networks` prefixes in akvorado.yaml.\n\n"
        "This deliberately does NOT use InIfBoundary: softflowd reports the same ifIndex for both "
        "ingress and egress, so interface boundary is constant across every flow and cannot "
        "distinguish direction. If everything lands in one series, your LAN prefix is missing from "
        "`clickhouse.networks`."
    ), overrides=DIRECTION_COLORS), 0, 12, 12, 8)

    # Companion to the absolute view, not a replacement. Percent stacking
    # answers a different question -- what the mix is right now, independent
    # of whether the link is busy -- and the two together read as one story:
    # a spike in the left panel that does not change the shape of the right
    # one is just more of the same traffic.
    builder.add(overview, timeseries(15, "Direction Share", [ch_query(
        "SELECT $__timeInterval(TimeReceived) AS time, "
        f"{pivot_columns(DIRECTION_EXPR, DIRECTIONS, 'Bytes * SamplingRate')} "
        "FROM flows "
        f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL} "
        "GROUP BY time ORDER BY time"
    )], "bytes", (
        "The same three buckets as the panel on the left, stacked to 100%. This answers 'what is the "
        "mix right now' rather than 'how much is there' -- useful for spotting a shift to outbound "
        "traffic that the absolute view hides because the total barely moved."
    ), stacked=True, stack_mode="percent", fill=60, overrides=DIRECTION_COLORS), 12, 12, 12, 8)

    builder.add(overview, bargauge(7, "Top Local Talkers", [ch_query(
        f"SELECT {ip_display('SrcAddr')} AS host, sum(Bytes * SamplingRate) AS bytes "
        "FROM flows "
        f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL} AND SrcNetRole = 'internal' "
        "GROUP BY host ORDER BY bytes DESC LIMIT 15",
        fmt=2,
    )], "bytes", "Busiest local sources by uploaded bytes. Raw `flows` table only, so bounded by the interval-0 retention window.", transformations=[limit(15)], values=True), 0, 20, 12, 9)

    builder.add(overview, bargauge(8, "Top External Destinations", [ch_query(
        f"SELECT {ip_display('DstAddr')} AS peer, sum(Bytes * SamplingRate) AS bytes "
        "FROM flows "
        f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL} AND {EXTERNAL_DST} "
        "GROUP BY peer ORDER BY bytes DESC LIMIT 15",
        fmt=2,
    )], "bytes", "Busiest internet-side destinations by bytes.", transformations=[limit(15)], values=True), 12, 20, 12, 9)

    builder.add(overview, table(9, "Top Conversations", [ch_query(
        f"SELECT {ip_display('SrcAddr')} AS Source, "
        f"{ip_display('DstAddr')} AS Destination, "
        "DstPort AS Port, "
        "dictGetOrDefault('protocols', 'name', toUInt64(Proto), toString(Proto)) AS Protocol, "
        "sum(Bytes * SamplingRate) AS Bytes, "
        "sum(Packets * SamplingRate) AS Packets "
        "FROM flows "
        f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL} "
        "GROUP BY Source, Destination, Port, Protocol ORDER BY Bytes DESC LIMIT 50",
        fmt=2,
    )], "Individual source/destination/port conversations, heaviest first. This is the panel that answers 'what is saturating the link right now'.", sort_col="Bytes"), 0, 29, 24, 10)

    tabs.append(builder.tab("Flow Overview", overview))

    # ── Applications ─────────────────────────────────────────────────────────

    applications: list[dict[str, Any]] = []
    builder.add(applications, text(100, "", (
        "## Ports and protocols\n"
        "Port numbers are a weak proxy for application identity -- almost everything is 443/TCP now. "
        "For real application labels, install the `dpi` profile (Netifyd) and use the Advanced dashboard; "
        "this tab answers 'which service ports and protocols carry the bytes', not 'which app'."
    )), 0, 0, 24, 4)

    builder.add(applications, bargauge(101, "Top Destination Ports", [ch_query(
        "SELECT concat(toString(DstPort), '/', "
        "dictGetOrDefault('protocols', 'name', toUInt64(Proto), toString(Proto))) AS port, "
        "sum(Bytes * SamplingRate) AS bytes "
        "FROM flows "
        f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL} AND {EXTERNAL_DST} "
        "GROUP BY port ORDER BY bytes DESC LIMIT 15",
        fmt=2,
    )], "bytes", "Internet-side destination ports by bytes. This is usually the most useful port ranking for web, DNS, streaming, VPN, and gaming traffic.", transformations=[limit(15)], values=True), 0, 4, 8, 9)

    builder.add(applications, bargauge(106, "Top Source Ports", [ch_query(
        "SELECT concat(toString(SrcPort), '/', "
        "dictGetOrDefault('protocols', 'name', toUInt64(Proto), toString(Proto))) AS port, "
        "sum(Bytes * SamplingRate) AS bytes "
        "FROM flows "
        f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL} AND SrcPort > 0 "
        "GROUP BY port ORDER BY bytes DESC LIMIT 15",
        fmt=2,
    )], "bytes", "Source ports by bytes. Expect many ephemeral client ports here; fixed source ports such as 443/UDP or 9100/TCP are the interesting exceptions.", transformations=[limit(15)], values=True), 8, 4, 8, 9)

    builder.add(applications, piechart(102, "Protocol Mix", [ch_query(
        "SELECT dictGetOrDefault('protocols', 'name', toUInt64(Proto), toString(Proto)) AS protocol, "
        "sum(Bytes * SamplingRate) AS bytes "
        "FROM flows "
        f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL} "
        "GROUP BY protocol ORDER BY bytes DESC LIMIT 10",
        fmt=2,
    )], "bytes", "Share of bytes by IP protocol.", values=True), 16, 4, 8, 9)

    builder.add(applications, timeseries(103, "Throughput by Destination Port", [ch_query(
        "SELECT $__timeInterval(TimeReceived) AS time, "
        "toString(DstPort) AS port, "
        "sum(Bytes * SamplingRate) * 8 / $__interval_s AS bps "
        "FROM flows "
        f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL} AND {EXTERNAL_DST} "
        "AND DstPort IN (SELECT DstPort FROM flows WHERE $__timeFilter(TimeReceived) "
        f"AND {ROUTER_SQL} AND {EXTERNAL_DST} "
        "GROUP BY DstPort ORDER BY sum(Bytes * SamplingRate) DESC LIMIT 8) "
        "GROUP BY time, port ORDER BY time"
    )], "bps", "Bit rate over time for the eight busiest destination ports in the range.", stacked=True, transformations=[partition_by("port")]), 0, 13, 24, 9)

    builder.add(applications, piechart(107, "Service Class Mix", [ch_query(
        f"SELECT {SERVICE_CLASS} AS service, "
        "sum(Bytes * SamplingRate) AS bytes "
        "FROM flows "
        f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL} "
        "GROUP BY service ORDER BY bytes DESC",
        fmt=2,
    )], "bytes", "Human-readable service buckets from source and destination ports. This is a convenience grouping, not DPI. Answers 'what is the mix over the whole range'; the companion panel below answers 'did it change'.", values=True), 0, 22, 8, 9)

    # Was a static table of bucket totals, which answered "what is the mix
    # over the whole range" and hid the thing that is actually interesting:
    # the mix *changes* over the day. Small-packet-heavy periods are
    # interactive and control traffic; the 1024+ band appearing is a bulk
    # transfer starting. A table cannot show that transition at all.
    builder.add(applications, heatmap(
        104,
        "Packet Size over Time",
        [ch_query(
            "SELECT $__timeInterval(TimeReceived) AS time, "
            f"{packet_size_columns()} "
            "FROM flows "
            f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL} "
            "GROUP BY time ORDER BY time"
        )],
        "packets",
        (
            "Packets per mean-packet-size bucket, over time. Column names are bucket **upper** "
            "bounds in bytes.\n\n"
            "A band that sits at the bottom is interactive, control, and ACK traffic; weight in the "
            "1024-1500 band is bulk transfer. The shift between them across a day is the shape "
            "worth watching -- a sustained small-packet band alongside a high flow count is also "
            "what scan-like behaviour looks like.\n\n"
            "Sizes are per-flow means (`PacketSize`), not per-packet, so a flow mixing large and "
            "tiny packets lands in a middle bucket rather than in both."
        ),
        y_axis_unit="bytes",
    ), 8, 22, 8, 9)

    # Was a raw bitmask table: 19, 27, 23, 18... Nobody reads bitmasks, and
    # the numbers are genuinely interesting once decoded. See TCP_SHAPE for
    # the decoding and the live distribution it was checked against.
    builder.add(applications, bargauge(105, "TCP Flow Outcomes", [ch_query(
        f"SELECT {TCP_SHAPE} AS shape, count() AS flows "
        "FROM flows "
        f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL} AND Proto = 6 "
        "GROUP BY shape ORDER BY flows DESC",
        fmt=2,
    )], "none", (
        "TCP flag bitmasks decoded into what actually happened to each flow.\n\n"
        "`TCPFlags` is the cumulative OR of every packet's flags across the flow's lifetime, so it "
        "describes an outcome rather than a packet: a flow carrying both SYN and FIN was opened and "
        "closed inside the capture window.\n\n"
        "- **Completed** -- handshake and close both seen. The healthy majority.\n"
        "- **RST** -- something reset the connection. Normal in small amounts (browsers abandon "
        "connections constantly); a sustained rise against one destination is a service refusing.\n"
        "- **SYN, never answered** -- a connection attempt that got nothing back. This is the scan "
        "and unreachable-host signature, and it is normally near zero here.\n"
        "- **Open / Established** -- still running when the flow was cut. Expected, because "
        "`maxlife=60` cuts every flow once a minute regardless of state.\n\n"
        "Colours are an identity contract shared with the Security Signals tab."
    ), color_mode="palette-classic-by-name", overrides=TCP_SHAPE_COLORS, values=True), 16, 22, 8, 9)

    builder.add(applications, bargauge(108, "Top Local Service Ports", [ch_query(
        "SELECT concat(toString(DstPort), '/', "
        "dictGetOrDefault('protocols', 'name', toUInt64(Proto), toString(Proto))) AS port, "
        "sum(Bytes * SamplingRate) AS bytes "
        "FROM flows "
        f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL} AND {LOCAL_DST} AND DstPort > 0 "
        "GROUP BY port ORDER BY bytes DESC LIMIT 15",
        fmt=2,
    )], "bytes", "Ports receiving traffic on internal destinations. Useful for spotting local scrapes, media servers, admin interfaces, and noisy LAN services.", transformations=[limit(15)], values=True), 0, 31, 12, 9)

    builder.add(applications, table(109, "Port Conversation Matrix", [ch_query(
        "SELECT concat(toString(SrcPort), ' -> ', toString(DstPort), '/', "
        "dictGetOrDefault('protocols', 'name', toUInt64(Proto), toString(Proto))) AS PortPath, "
        "count() AS Flows, "
        "sum(Bytes * SamplingRate) AS Bytes, "
        "sum(Packets * SamplingRate) AS Packets "
        "FROM flows "
        f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL} AND SrcPort > 0 AND DstPort > 0 "
        "GROUP BY PortPath ORDER BY Bytes DESC LIMIT 40",
        fmt=2,
    )], "Source-to-destination port pairs. This makes fixed source-port services stand out from ordinary client ephemeral ports.", sort_col="Bytes"), 12, 31, 12, 9)

    # Companion to the Service Class donut, same buckets. The donut collapses
    # the whole range into one number per class and so cannot show a mix that
    # moved; percent stacking over time is the panel that does.
    builder.add(applications, timeseries(110, "Service Class over Time", [ch_query(
        "SELECT $__timeInterval(TimeReceived) AS time, "
        f"{pivot_columns(SERVICE_CLASS, SERVICE_CLASSES, 'Bytes * SamplingRate')} "
        "FROM flows "
        f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL} "
        "GROUP BY time ORDER BY time"
    )], "bytes", (
        "The same port-derived service buckets as the donut above, stacked to 100% over time.\n\n"
        "Read it for shape changes rather than for levels: a backup window, a streaming session, or "
        "an overnight update run all show up here as one band taking over, where the donut would "
        "only show a slightly different slice."
    ), stacked=True, stack_mode="percent", fill=60), 0, 40, 24, 9)

    tabs.append(builder.tab("Applications", applications))

    # ── External ─────────────────────────────────────────────────────────────

    external: list[dict[str, Any]] = []
    builder.add(external, text(200, "", (
        "## Autonomous systems and geography\n"
        "ASN and country enrichment comes from the MaxMind/IPinfo databases mounted in `akvorado/geoip/`. "
        "When the databases are missing, Akvorado still stores flows but AS, country, and network "
        "labels stay empty. The resolution tiles below make that state explicit.\n\n"
        "**Resolution here reads low by design, and that is not a fault.** Most flows on this network "
        "are LAN-to-LAN, and a private address has no AS or country by definition. The tiles measure "
        "resolution over boundary-crossing flows, so the honest reading is 'of the traffic that "
        "actually left, how much did we identify' -- not 'how much of the enrichment is broken'.\n\n"
        "Read source-side panels as **who sent traffic to you or to local services** and destination-side "
        "panels as **where your clients sent traffic**. On a home LAN most source ports are ephemeral; AS "
        "and destination ports are usually the cleaner story.\n\n"
        "City-level enrichment is deliberately off (the City and Country databases together OOM the "
        "orchestrator at its current memory limit), so there is no city panel here."
    )), 0, 0, 24, 5)

    builder.add(external, ch_stat(201, "Destination AS Resolution", (
        "SELECT round(100 * countIf(DstAS != 0) / greatest(count(), 1)) AS pct FROM flows "
        f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL} AND {EXTERNAL_DST}"
    ), "percent", "Share of outbound/external-destination flows that resolved to a destination AS number."), 0, 5, 6, 4)

    builder.add(external, ch_stat(202, "Source AS Resolution", (
        "SELECT round(100 * countIf(SrcAS != 0) / greatest(count(), 1)) AS pct FROM flows "
        f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL} AND {EXTERNAL_SRC}"
    ), "percent", "Share of internet-source flows that resolved to a source AS number."), 6, 5, 6, 4)

    builder.add(external, ch_stat(207, "Country Resolution", (
        f"SELECT round(100 * countIf({SRC_COUNTRY} != '' OR {DST_COUNTRY} != '') / greatest(count(), 1)) AS pct "
        "FROM flows "
        f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL} AND {CROSSES_BOUNDARY}"
    ), "percent", "Share of boundary-crossing flows with either side resolved to a non-empty country code."), 12, 5, 6, 4)

    # Was a second copy of the hero row's External Share tile. Replaced with
    # the number this tab actually lacked: how wide the geographic spread is,
    # which is the headline for the map below it.
    builder.add(external, ch_stat(203, "Countries Contacted", (
        f"SELECT uniqExactIf({DST_COUNTRY}, {DST_COUNTRY} != '') AS countries FROM flows "
        f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL} AND {EXTERNAL_DST}"
    ), "none", (
        "Distinct destination countries resolved in this range. Counts only flows whose destination "
        "country actually resolved, so it is a floor on the real spread, not an estimate of it."
    )), 18, 5, 6, 4)

    builder.add(external, bargauge(204, "Top Destination AS", [ch_query(
        "SELECT concat('AS', toString(DstAS), ' ', "
        "dictGetOrDefault('asns', 'name', toUInt64(DstAS), '')) AS asn, "
        "sum(Bytes * SamplingRate) AS bytes "
        "FROM flows "
        f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL} AND {EXTERNAL_DST} AND DstAS != 0 "
        "GROUP BY asn ORDER BY bytes DESC LIMIT 15",
        fmt=2,
    )], "bytes", "Networks your traffic actually goes to, by bytes. Requires GeoIP/ASN.", transformations=[limit(15)], values=True), 0, 9, 12, 9)

    builder.add(external, bargauge(208, "Top Source AS", [ch_query(
        "SELECT concat('AS', toString(SrcAS), ' ', "
        "dictGetOrDefault('asns', 'name', toUInt64(SrcAS), '')) AS asn, "
        "sum(Bytes * SamplingRate) AS bytes "
        "FROM flows "
        f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL} AND {EXTERNAL_SRC} AND SrcAS != 0 "
        "GROUP BY asn ORDER BY bytes DESC LIMIT 15",
        fmt=2,
    )], "bytes", "Internet-side source networks by bytes. This is the panel to inspect for inbound traffic or remote services sending data to the LAN.", transformations=[limit(15)], values=True), 12, 9, 12, 9)

    builder.add(external, bargauge(205, "Top Destination Country", [ch_query(
        f"SELECT {DST_COUNTRY} AS country, sum(Bytes * SamplingRate) AS bytes "
        "FROM flows "
        f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL} AND {EXTERNAL_DST} AND {DST_COUNTRY} != '' "
        "GROUP BY country ORDER BY bytes DESC LIMIT 15",
        fmt=2,
    )], "bytes", "Destination countries by bytes. Requires GeoIP.", transformations=[limit(15)], values=True), 0, 18, 12, 9)

    builder.add(external, bargauge(209, "Top Source Country", [ch_query(
        f"SELECT {SRC_COUNTRY} AS country, sum(Bytes * SamplingRate) AS bytes "
        "FROM flows "
        f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL} AND {EXTERNAL_SRC} AND {SRC_COUNTRY} != '' "
        "GROUP BY country ORDER BY bytes DESC LIMIT 15",
        fmt=2,
    )], "bytes", "Internet-side source countries by bytes. Missing countries are stripped instead of showing ClickHouse FixedString NUL bytes.", transformations=[limit(15)], values=True), 12, 18, 12, 9)

    builder.add(external, timeseries(206, "Throughput by Destination AS", [ch_query(
        "SELECT $__timeInterval(TimeReceived) AS time, "
        "concat('AS', toString(DstAS)) AS asn, "
        "sum(Bytes * SamplingRate) * 8 / $__interval_s AS bps "
        "FROM flows "
        f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL} AND {EXTERNAL_DST} AND DstAS != 0 "
        "AND DstAS IN (SELECT DstAS FROM flows WHERE $__timeFilter(TimeReceived) "
        f"AND {ROUTER_SQL} AND {EXTERNAL_DST} AND DstAS != 0 "
        "GROUP BY DstAS ORDER BY sum(Bytes * SamplingRate) DESC LIMIT 8) "
        "GROUP BY time, asn ORDER BY time"
    )], "bps", "Bit rate to the eight busiest destination networks. Requires GeoIP/ASN.", stacked=True, transformations=[partition_by("asn")]), 0, 27, 12, 9)

    builder.add(external, timeseries(210, "Throughput by Source AS", [ch_query(
        "SELECT $__timeInterval(TimeReceived) AS time, "
        "concat('AS', toString(SrcAS)) AS asn, "
        "sum(Bytes * SamplingRate) * 8 / $__interval_s AS bps "
        "FROM flows "
        f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL} AND {EXTERNAL_SRC} AND SrcAS != 0 "
        "AND SrcAS IN (SELECT SrcAS FROM flows WHERE $__timeFilter(TimeReceived) "
        f"AND {ROUTER_SQL} AND {EXTERNAL_SRC} AND SrcAS != 0 "
        "GROUP BY SrcAS ORDER BY sum(Bytes * SamplingRate) DESC LIMIT 8) "
        "GROUP BY time, asn ORDER BY time"
    )], "bps", "Bit rate from the eight busiest source networks. Useful for inbound traffic and remote services that send large responses.", stacked=True, transformations=[partition_by("asn")]), 12, 27, 12, 9)

    builder.add(external, table(211, "AS Conversation Matrix", [ch_query(
        "SELECT "
        "concat('AS', toString(SrcAS), ' ', dictGetOrDefault('asns', 'name', toUInt64(SrcAS), '')) AS SourceAS, "
        "concat('AS', toString(DstAS), ' ', dictGetOrDefault('asns', 'name', toUInt64(DstAS), '')) AS DestinationAS, "
        "count() AS Flows, "
        "uniq(SrcAddr) AS Sources, "
        "uniq(DstAddr) AS Destinations, "
        "sum(Bytes * SamplingRate) AS Bytes, "
        "sum(Packets * SamplingRate) AS Packets "
        "FROM flows "
        f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL} AND (SrcAS != 0 OR DstAS != 0) "
        "GROUP BY SourceAS, DestinationAS ORDER BY Bytes DESC LIMIT 50",
        fmt=2,
    )], "Who is talking to whom at the AS level, with local or unresolved sides grouped as AS0. This makes CDN, resolver, cloud, and remote-service relationships visible in one sortable table.", sort_col="Bytes"), 0, 36, 24, 10)

    builder.add(external, table(212, "Country Pair Matrix", [ch_query(
        f"SELECT if({SRC_COUNTRY} = '', 'unresolved', {SRC_COUNTRY}) AS SourceCountry, "
        f"if({DST_COUNTRY} = '', 'unresolved', {DST_COUNTRY}) AS DestinationCountry, "
        "count() AS Flows, "
        "sum(Bytes * SamplingRate) AS Bytes "
        "FROM flows "
        f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL} AND {CROSSES_BOUNDARY} "
        "GROUP BY SourceCountry, DestinationCountry ORDER BY Bytes DESC LIMIT 50",
        fmt=2,
    )], "Country-to-country pairs after stripping missing FixedString values. This is a compact way to see where traffic is going without exposing individual IPs.", sort_col="Bytes"), 0, 46, 24, 9)

    # The destination-country codes are already a live, ~30%-populated
    # FixedString(2), which is exactly what Grafana's `countries` gazetteer
    # is keyed on -- so the map needs no coordinate source of its own and no
    # invented positions. The marker layer's own `lookup` location mode does
    # the resolution -- see ch_geomap for why the fieldLookup transform is
    # the wrong tool here despite being the documented one.
    builder.add(external, ch_geomap(214, "Where the Traffic Goes", (
        f"SELECT {DST_COUNTRY} AS country, sum(Bytes * SamplingRate) AS bytes "
        "FROM flows "
        f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL} AND {EXTERNAL_DST} "
        f"AND {DST_COUNTRY} != '' "
        "GROUP BY country ORDER BY bytes DESC LIMIT 100"
    ), "country", (
        "Destination countries, marker size and colour by bytes.\n\n"
        "Only flows whose destination country resolved appear here -- roughly a third of "
        "boundary-crossing flows, per the Country Resolution tile above. Read the map as "
        "'where the identified traffic went', not as a complete picture: an absent country means "
        "unresolved just as often as it means unvisited.\n\n"
        "Markers sit at country centroids from Grafana's built-in gazetteer. They are not the "
        "location of any actual host, and country-level GeoIP is itself only an approximation -- "
        "anycast CDN traffic in particular resolves to wherever the address block is registered."
    )), 0, 55, 24, 11)

    # Local hosts on one side, the networks they talk to on the other. The
    # node and edge frames are derived from the same LIMITed pair set, so
    # every edge endpoint is guaranteed to exist as a node -- a dangling
    # endpoint crashes this panel rather than degrading.
    _NODE_PAIRS = (
        "WITH pairs AS ("
        f"SELECT {ip_display('SrcAddr')} AS host, "
        "toString(DstAS) AS asn, "
        "dictGetOrDefault('asns', 'name', toUInt64(DstAS), concat('AS', toString(DstAS))) AS as_name, "
        "sum(Bytes * SamplingRate) AS bytes, "
        "count() AS flows "
        "FROM flows "
        f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL} "
        f"AND {LOCAL_SRC} AND {EXTERNAL_DST} AND DstAS != 0 "
        "GROUP BY host, asn, as_name ORDER BY bytes DESC LIMIT 60) "
    )
    builder.add(external, ch_nodegraph(
        215,
        "Local Hosts to External Networks",
        _NODE_PAIRS + (
            "SELECT concat('h:', host) AS id, host AS title, 'local host' AS subtitle, "
            "sum(bytes) AS mainstat, sum(flows) AS secondarystat "
            "FROM pairs GROUP BY id, title "
            "UNION ALL "
            "SELECT concat('a:', asn) AS id, as_name AS title, concat('AS', asn) AS subtitle, "
            "sum(bytes) AS mainstat, sum(flows) AS secondarystat "
            "FROM pairs GROUP BY id, title, subtitle"
        ),
        _NODE_PAIRS + (
            "SELECT concat(host, '>', asn) AS id, "
            "concat('h:', host) AS source, concat('a:', asn) AS target, "
            "bytes AS mainstat, flows AS secondarystat "
            "FROM pairs"
        ),
        (
            "Which local devices talk to which networks on the internet, sized by bytes. Node stats "
            "are bytes (main) and flow count (secondary).\n\n"
            "Only flows with a resolved destination AS are included, and only the 60 heaviest "
            "host-to-network pairs in the range -- Grafana hides nodes past 200 behind cluster "
            "markers, so the cap keeps the graph readable rather than truthful-but-unusable. "
            "The dashboard keeps the grouped label set bounded for Grafana.\n\n"
            "This is the same relationship the AS Conversation Matrix below states as a table. The "
            "table is what you sort and filter; this is what makes a device with an unexpected "
            "number of network relationships obvious at a glance."
        ),
    ), 0, 66, 24, 13)

    # Panel 213 was "Top Geo Cities". Removed rather than restyled: the City
    # database is deliberately not loaded (it OOMs the orchestrator alongside
    # Country at the configured memory limit), so SrcGeoCity/DstGeoCity are
    # 0% populated and the panel was a permanent "no data" wall -- the exact
    # thing the enrichment tiles above exist to distinguish from a real fault.
    # The geomap below is what that space is worth spending on instead.

    tabs.append(builder.tab("External", external))

    # ── Security Signals ─────────────────────────────────────────────────────
    #
    # Deliberately small and deliberately hedged. Every panel here describes a
    # *shape* that is sometimes worth a second look, never a verdict: on a
    # home network the same shapes are produced constantly by ordinary
    # browsing, CDN fan-out, and service discovery. Framing them as alerts
    # would train the operator to ignore the tab.
    #
    # What is NOT here, and why:
    #
    #   - ICMP type/code decoding. The `icmp` dictionary exists in ClickHouse
    #     and maps (proto, type, code) to names, but softflowd never populates
    #     the type/code -- DstPort is a constant 0 on every Proto=1 and
    #     Proto=58 flow. Decoding it would
    #     silently render every ICMP flow as "echo-reply", which is the
    #     dictionary's response to (1,0,0), not a fact about the traffic.
    #     Volume over time is what the data actually supports.
    #
    #   - A dedicated SYN-scan tile. The unanswered-SYN count is real and
    #     included below, but it is a handful of flows per day here, so it is
    #     presented as a number to watch rather than as a threshold to trip.

    security: list[dict[str, Any]] = []
    builder.add(security, text(400, "", (
        "## Shapes worth a second look\n"
        "Nothing on this tab is an alert, and nothing here means you have been attacked. These are "
        "traffic *shapes* that are occasionally worth explaining -- and that a home network produces "
        "innocently all the time.\n\n"
        "- A browser opening a page touches dozens of CDN endpoints; that is fan-out.\n"
        "- Connections get reset constantly as pages are abandoned; that is resets.\n"
        "- Phones and TVs probe the LAN continuously; that is discovery traffic.\n\n"
        "The useful signal is almost never a single number being non-zero. It is a **change**: a "
        "device that suddenly fans out far wider than it used to, or a reset rate that climbs "
        "against one destination. Compare against a quiet period before drawing conclusions.\n\n"
        "**Coverage caveat applies here more than anywhere else.** Hardware flow offload means "
        "softflowd never sees some traffic, so absence of a signal on this tab is not evidence of "
        "absence. Check the Pipeline Health tab first."
    )), 0, 0, 24, 7)

    builder.add(security, ch_stat(401, "Reset Rate", (
        "SELECT round(100 * countIf(bitAnd(TCPFlags, 4) != 0) / greatest(count(), 1)) AS pct "
        "FROM flows "
        f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL} AND Proto = 6"
    ), "percent", (
        "Share of TCP flows in which a RST was seen at any point.\n\n"
        "A quarter to a third is entirely normal -- browsers abandon connections, servers close "
        "idle sockets, and connection racing (Happy Eyeballs) resets the loser by design. Watch the "
        "trend, not the value, and cross-check against the destination breakdown below if it moves."
    )), 0, 7, 6, 4)

    builder.add(security, ch_stat(402, "Unanswered Attempts", (
        "SELECT countIf(bitAnd(TCPFlags, 2) != 0 AND bitAnd(TCPFlags, 16) = 0) AS flows "
        "FROM flows "
        f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL} AND Proto = 6"
    ), "none", (
        "TCP flows that carried a SYN and never a matching ACK -- a connection attempt that got "
        "nothing back.\n\n"
        "This is the closest thing in the dataset to a scan signature, and it is normally a handful "
        "of flows: an unreachable host, a service that moved, a stale bookmark. It has no threshold "
        "colour on purpose, because the number that matters is how it compares to yesterday, not "
        "whether it crossed a line someone guessed at."
    )), 6, 7, 6, 4)

    builder.add(security, ch_stat(403, "Widest Fan-out", (
        "SELECT max(dests) AS dests FROM ("
        "SELECT uniq(DstAddr) AS dests FROM flows "
        f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL} "
        f"AND {LOCAL_SRC} AND {EXTERNAL_DST} "
        "GROUP BY SrcAddr)"
    ), "none", (
        "The largest number of distinct external addresses any single local host contacted in this "
        "range.\n\n"
        "Ordinary web browsing reaches hundreds of endpoints per hour, so a high number is normal "
        "for a laptop or phone and notable for a device that should be quiet -- a thermostat, a "
        "printer, a camera. The table below names which host it was."
    )), 12, 7, 6, 4)

    builder.add(security, ch_stat(404, "Inbound Sources", (
        "SELECT uniq(SrcAddr) AS sources FROM flows "
        f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL} "
        f"AND {EXTERNAL_SRC} AND {LOCAL_DST} AND DstPort > 0 AND DstPort < 1024"
    ), "none", (
        "Distinct internet-side addresses that sent traffic to a well-known port (below 1024) on a "
        "local host.\n\n"
        "Behind NAT with no port forwards this is mostly replies to things the LAN started -- NTP "
        "servers answering, for instance. A rising count, or traffic to a port you did not expose, "
        "is worth explaining."
    )), 18, 7, 6, 4)

    builder.add(security, timeseries(405, "TCP Flow Outcomes over Time", [ch_query(
        "SELECT $__timeInterval(TimeReceived) AS time, "
        + ", ".join(f"countIf({TCP_SHAPE} = '{name}') AS \"{name}\"" for name in TCP_SHAPES)
        + " FROM flows "
        f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL} AND Proto = 6 "
        "GROUP BY time ORDER BY time"
    )], "none", (
        "The decoded TCP outcomes from the Applications tab, over time and stacked.\n\n"
        "The point of the time axis is that the mix is normally stable. A step change -- resets "
        "suddenly dominating, or unanswered attempts appearing in a band where there were none -- "
        "is the thing to notice; the absolute counts follow whatever the network was doing."
    ), stacked=True, overrides=TCP_SHAPE_COLORS), 0, 11, 12, 9)

    builder.add(security, timeseries(406, "ICMP Volume", [ch_query(
        "SELECT $__timeInterval(TimeReceived) AS time, "
        "countIf(Proto = 1) AS \"ICMPv4\", countIf(Proto = 58) AS \"ICMPv6\" "
        "FROM flows "
        f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL} AND Proto IN (1, 58) "
        "GROUP BY time ORDER BY time"
    )], "none", (
        "ICMP and ICMPv6 flow counts over time.\n\n"
        "**No type/code breakdown is possible here.** ClickHouse ships an `icmp` dictionary that "
        "maps (protocol, type, code) to names like `echo-request` or `destination-unreachable`, but "
        "softflowd does not export ICMP type or code -- the field is a constant zero on every ICMP "
        "flow in this deployment. Decoding it would label everything `echo-reply`, which is what the "
        "dictionary returns for (1,0,0) and not a fact about the traffic. Volume is what the data "
        "honestly supports.\n\n"
        "ICMPv6 is steady and high on any IPv6 LAN: neighbour discovery and router advertisement "
        "are ICMPv6. A sustained ICMPv4 climb is the more interesting of the two."
    )), 12, 11, 12, 9)

    builder.add(security, table(407, "Fan-out by Local Host", [ch_query(
        f"SELECT {ip_display('SrcAddr')} AS Host, "
        "uniq(DstAddr) AS Destinations, "
        "uniq(DstAS) AS Networks, "
        "uniq(DstPort) AS Ports, "
        "count() AS Flows, "
        "sum(Bytes * SamplingRate) AS Bytes "
        "FROM flows "
        f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL} "
        f"AND {LOCAL_SRC} AND {EXTERNAL_DST} "
        "GROUP BY Host ORDER BY Destinations DESC LIMIT 30",
        fmt=2,
    )], (
        "Local hosts ranked by how many distinct external destinations they contacted.\n\n"
        "**This is a browsing-activity ranking as much as anything else.** A laptop at the top of "
        "this table is the expected result, not a finding. What is worth a look is a device with a "
        "narrow job -- a printer, a smart plug, an IP camera -- sitting anywhere near the top, or a "
        "host whose `Ports` count is high while its `Bytes` stay tiny, which is the shape of "
        "probing rather than of using a service.\n\n"
        "`Networks` counts distinct destination ASes and is the steadier column: CDN fan-out "
        "inflates `Destinations` heavily but usually resolves to only a handful of networks."
    ), sort_col="Destinations"), 0, 20, 12, 10)

    builder.add(security, table(408, "Inbound to Local Hosts", [ch_query(
        f"SELECT {ip_display('DstAddr')} AS Target, "
        "DstPort AS Port, "
        "dictGetOrDefault('protocols', 'name', toUInt64(Proto), toString(Proto)) AS Protocol, "
        "uniq(SrcAddr) AS Sources, "
        "count() AS Flows, "
        "sum(Bytes * SamplingRate) AS Bytes "
        "FROM flows "
        f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL} "
        f"AND {EXTERNAL_SRC} AND {LOCAL_DST} AND DstPort > 0 AND DstPort < 1024 "
        "GROUP BY Target, Port, Protocol ORDER BY Flows DESC LIMIT 30",
        fmt=2,
    )], (
        "Traffic from internet-side sources to well-known ports on local hosts.\n\n"
        "Restricted to ports below 1024 to keep ordinary reply traffic on ephemeral ports out. Most "
        "of what remains is still solicited -- an NTP server answering the LAN's own query looks "
        "exactly like an inbound flow to port 123, because from the capture's point of view that is "
        "what it is. softflowd cannot tell a reply from an unsolicited connection.\n\n"
        "Read it as an inventory of what reaches in and on which port, and check anything you did "
        "not deliberately expose. An empty table is the expected state behind NAT with no port "
        "forwards."
    ), sort_col="Flows"), 12, 20, 12, 10)

    tabs.append(builder.tab("Security Signals", security))

    # ── Pipeline Health (Prometheus) ─────────────────────────────────────────

    health: list[dict[str, Any]] = []
    builder.add(health, text(300, "", (
        "## Is the flow data trustworthy?\n"
        "Everything on this tab is Prometheus, not ClickHouse. The flow pipeline has three failure modes "
        "that produce *plausible but wrong* data rather than an error:\n\n"
        "1. **Hardware flow offload** -- packets forwarded by the switch ASIC never reach softflowd.\n"
        "2. **libpcap drops** -- the router could not keep up and flows are missing packets.\n"
        "3. **Flow table overflow** -- `max_flows` was hit, so flows expired early and byte counts are truncated.\n\n"
        "All three are surfaced below. Treat a non-zero value in any of them as "
        "'the numbers on the other tabs are a lower bound'."
    )), 0, 0, 24, 6)

    builder.add(health, stat(301, "Exporter Running", (
        f'min(openwrt_netflow_exporter_up{{{PROM_FILTER}}}) or vector(0)'
    ), "none", "Whether every configured softflowd instance answered on its control socket. Reads 0 if any capture interface has no live exporter.",
        mappings=AVAILABILITY_MAPPINGS, thresholds_key="unavailable", color_mode="value"), 0, 6, 6, 4)

    builder.add(health, stat(302, "Flow Data Complete", (
        # 1 only when no hardware offload is active anywhere in the selection.
        f'(max(openwrt_flow_offload_enabled{{{PROM_FILTER}, mode="hw"}}) == bool 0) or vector(0)'
    ), "none", (
        "0 means hardware flow offload is enabled, so the switch ASIC forwards traffic the CPU never sees "
        "and softflowd cannot capture it. Flow totals are then incomplete by an unknown amount. "
        "Set NETFLOW_DISABLE_HW_OFFLOAD=1 when running setup.sh to trade routing throughput for complete accounting."
    ), mappings=[{"type": "value", "options": {
        "1": {"text": "Complete", "color": "green", "index": 0},
        "0": {"text": "Incomplete (HW offload)", "color": "red", "index": 1},
    }}], thresholds_key="unavailable", color_mode="value"), 6, 6, 6, 4)

    builder.add(health, stat(303, "Capture Drops", (
        f'sum(rate(openwrt_netflow_pcap_packets_dropped_total{{{PROM_FILTER}}}[$__rate_interval])) or vector(0)'
    ), "pps", "Packets libpcap discarded because softflowd could not keep up. Anything above zero means flows are missing packets; reduce the captured interface set or raise NETFLOW_SAMPLING_RATE.",
        thresholds_key="unavailable", graph=True), 12, 6, 6, 4)

    builder.add(health, stat(304, "Forced Flow Expiry", (
        f'sum(rate(openwrt_netflow_flows_force_expired_total{{{PROM_FILTER}}}[$__rate_interval])) or vector(0)'
    ), "none", "Flows expired early because the softflowd flow table was full. Non-zero truncates byte counts; raise NETFLOW_MAX_FLOWS.",
        thresholds_key="unavailable", graph=True), 18, 6, 6, 4)

    builder.add(health, timeseries(305, "Router Exporter Rates", [
        prom_query(f'sum(rate(openwrt_netflow_packets_processed_total{{{PROM_FILTER}}}[$__rate_interval]))', "packets processed", "A"),
        prom_query(f'sum(rate(openwrt_netflow_flows_exported_total{{{PROM_FILTER}}}[$__rate_interval]))', "flows exported", "B"),
        prom_query(f'sum(rate(openwrt_netflow_pcap_packets_dropped_total{{{PROM_FILTER}}}[$__rate_interval]))', "libpcap drops", "C"),
        prom_query(f'sum(rate(openwrt_netflow_export_failures_total{{{PROM_FILTER}}}[$__rate_interval]))', "export failures", "D"),
    ], "none", "softflowd's own counters. Export failures mean the router could not send to the collector -- check the inlet is reachable on the NetFlow port.",
        overrides=[
            {"matcher": {"id": "byName", "options": "libpcap drops"}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": RED}}]},
            {"matcher": {"id": "byName", "options": "export failures"}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": RED}}]},
            {"matcher": {"id": "byName", "options": "flows exported"}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": GREEN}}]},
        ]), 0, 10, 12, 9)

    # Metric names verified against Akvorado's troubleshooting documentation
    # (console/data/docs/05-troubleshooting.md), which walks this exact
    # pipeline: inlet UDP -> Kafka -> outlet received -> enriched/forwarded.
    builder.add(health, timeseries(306, "Collector Pipeline Rates", [
        prom_query('sum(rate(akvorado_inlet_flow_input_udp_packets_total[$__rate_interval])) or vector(0)', "inlet UDP packets", "A"),
        prom_query('sum(rate(akvorado_inlet_flow_input_udp_in_dropped_packets_total[$__rate_interval])) or vector(0)', "inlet kernel drops", "B"),
        prom_query('sum(rate(akvorado_outlet_core_received_flows_total[$__rate_interval])) or vector(0)', "outlet flows received", "C"),
        prom_query('sum(rate(akvorado_outlet_core_forwarded_flows_total[$__rate_interval])) or vector(0)', "outlet flows stored", "D"),
    ], "none", (
        "Akvorado's own counters, scraped as job=\"akvorado\". Two gaps to read here:\n\n"
        "- **inlet kernel drops** above zero means the UDP receive buffer overflowed. Akvorado's docs are "
        "explicit that this makes byte and packet counts unreliable, because the reported sampling rate no "
        "longer matches what actually arrived.\n"
        "- **received but not stored** means enrichment is dropping flows. Almost always an ifIndex that "
        "akvorado/exporters.yaml does not know. The breakdown panel below names the exact reason."
    ), overrides=[
            {"matcher": {"id": "byName", "options": "inlet kernel drops"}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": RED}}]},
            {"matcher": {"id": "byName", "options": "outlet flows stored"}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": GREEN}}]},
        ]), 12, 10, 12, 9)

    builder.add(health, table(307, "Capture Interfaces and ifIndex", [
        prom_query(f'openwrt_netflow_ifindex{{{PROM_FILTER}}}', "", "A", fmt="table", instant=True),
        prom_query(f'openwrt_netflow_exporter_up{{{PROM_FILTER}}}', "", "B", fmt="table", instant=True),
        prom_query(f'openwrt_netflow_active_flows{{{PROM_FILTER}}}', "", "C", fmt="table", instant=True),
    ], (
        "The ifIndex column is what must match akvorado/exporters.yaml. A mismatch discards every flow "
        "from that interface with no error anywhere -- it looks identical to NetFlow not working at all."
    ), transformations=[
        {"kind": "merge", "spec": {"id": "merge", "options": {}}},
        organize(
            exclude=["Time", "__name__", "cluster", "endpoint", "instance", "job", "namespace", "prometheus", "prometheus_replica", "service"],
            rename={"router": "Router", "interface": "Interface", "Value #A": "ifIndex", "Value #B": "Exporter Up", "Value #C": "Active Flows"},
            index={"Router": 0, "Interface": 1, "ifIndex": 2, "Exporter Up": 3, "Active Flows": 4},
        ),
        limit(25),
    ], sort_col="Router", sort_desc=False), 0, 19, 12, 9)

    builder.add(health, timeseries(308, "Flow Table Occupancy", [
        prom_query(f'openwrt_netflow_active_flows{{{PROM_FILTER}}}', "{{router}} {{interface}}", "A"),
    ], "none", "Flows currently tracked by softflowd. Approaching NETFLOW_MAX_FLOWS (8192 by default) is what causes the forced expiry above.",
        overrides=[]), 12, 19, 12, 9)

    builder.add(health, stat(309, "Collector Available", (
        f'min(openwrt_netflow_collector_available{{{PROM_FILTER}}}) or vector(0)'
    ), "none", "Whether the health collector itself ran and reached at least one exporter. 0 with the netflow profile installed means softflowctl is missing or every instance is down.",
        mappings=AVAILABILITY_MAPPINGS, thresholds_key="unavailable", color_mode="value"), 0, 28, 8, 4)

    builder.add(health, stat(310, "Export Failures", (
        f'sum(increase(openwrt_netflow_export_failures_total{{{PROM_FILTER}}}[$__range])) or vector(0)'
    ), "none", "Flows softflowd could not send to the collector over the selected range. Non-zero means UDP to the Akvorado inlet is failing.",
        thresholds_key="unavailable"), 8, 28, 8, 4)

    builder.add(health, stat(311, "Textfile Freshness", (
        f'max(time() - node_textfile_mtime_seconds{{{PROM_FILTER}}}) or vector(999999)'
    ), "s", "Age of the router's textfile metrics, including this collector's output. A stale value means cron is not running the health script.",
        thresholds_key="freshness", graph=True), 16, 28, 8, 4)

    # The most useful diagnostic panel in this dashboard. Akvorado's enrichment
    # errors carry a human-readable `error` label -- "metadata missing" (the
    # ifIndex is not in exporters.yaml), "sampling rate missing", "input and
    # output interfaces missing". Each silently discards flows, so naming the
    # reason turns the predicted failure mode from a hunt into a glance.
    #
    # Matched by regex rather than an exact metric name so the panel survives
    # Akvorado renaming the counter between releases; the `error` label is the
    # payload and is what the upstream troubleshooting guide keys on.
    builder.add(health, bargauge(312, "Collector Error Reasons", [
        prom_query(
            'sum by(error) (increase({__name__=~"akvorado_outlet_core_.*errors_total"}[$__range]))',
            "{{error}}", "A",
        ),
    ], "none", (
        "Why the outlet discarded flows over the selected range, by Akvorado's own reason label. "
        "'metadata missing' means the ifIndex is not in akvorado/exporters.yaml - the most likely "
        "failure on a fresh install. 'sampling rate missing' is normal briefly at startup but should "
        "stop increasing; if it keeps climbing, akvorado.yaml's default-sampling-rate is not taking "
        "effect. Empty is the healthy state."
    ), transformations=[limit(10)]), 0, 32, 24, 7)

    # Every other tile on this tab is instantaneous: it answers "is the
    # pipeline healthy right now" and cannot answer "was it healthy at 3am",
    # which is the question you actually have when yesterday's byte totals
    # look wrong. One state timeline covers the whole selected range.
    #
    # Value mappings live in fieldConfig.defaults, never in a byType override:
    # that matcher silently fails to apply on state timelines and the panel
    # renders raw "-Inf - +Inf" bracket text instead of the state names.
    builder.add(health, state_timeline(313, "Availability History", [
        prom_query(f'min(openwrt_netflow_exporter_up{{{PROM_FILTER}}}) or vector(0)', "Exporter running", "A"),
        prom_query(f'min(openwrt_netflow_collector_available{{{PROM_FILTER}}}) or vector(0)', "Health collector", "B"),
        prom_query(
            f'(max(openwrt_flow_offload_enabled{{{PROM_FILTER}, mode="hw"}}) == bool 0) or vector(0)',
            "Capture complete", "C",
        ),
    ], "none", (
        "Each of the three trust conditions above, over the selected range instead of right now.\n\n"
        "This is the panel that answers 'was it down at 3am'. A gap in **Exporter running** means "
        "softflowd was not answering, so flows from that window are missing entirely rather than "
        "merely incomplete. **Capture complete** going red means hardware flow offload was active "
        "and the totals for that period are a lower bound.\n\n"
        "The three series are deliberately the same conditions as the tiles at the top of the tab, "
        "so a tile and its history can never disagree."
    ), mappings=[
        {"type": "value", "options": {
            "1": {"text": "OK", "color": GREEN, "index": 0},
            "0": {"text": "Degraded", "color": RED, "index": 1},
        }},
        {"type": "special", "options": {"match": "null", "result": {"text": "No data", "color": GRAY, "index": 2}}},
    ],
        # Value mappings only reach the legend when the colour scheme is NOT
        # "thresholds": with threshold colouring Grafana labels each band by
        # its threshold bracket ("< 1", "1+") and the mapped state names are
        # discarded. The mappings above carry their own colours, so
        # palette-classic never actually assigns one.
        color_mode="palette-classic", thresholds_key="ok_bad"), 0, 39, 24, 8)

    # The one ClickHouse panel on an otherwise all-Prometheus tab, and it
    # earns its place: it cross-checks the address-role direction logic that
    # every throughput panel on this dashboard depends on against the
    # exporter's own FlowDirection field, which nothing else uses.
    builder.add(health, table(314, "Direction Cross-Check", [ch_query(
        "SELECT FlowDirection AS Reported, "
        "multiIf(SrcNetRole = 'internal' AND DstNetRole = 'internal', 'local', "
        "SrcNetRole = 'internal', 'outbound', 'inbound') AS Derived, "
        "count() AS Flows, "
        "sum(Bytes * SamplingRate) AS Bytes "
        "FROM flows "
        f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL} "
        "GROUP BY Reported, Derived ORDER BY Flows DESC LIMIT 20",
        fmt=2,
    )], (
        "Akvorado's own `FlowDirection` against the direction this dashboard derives from address "
        "role. These are independent signals and this table is the only place they meet.\n\n"
        "The expected shape is a strong diagonal: `egress`/`outbound` and `ingress`/`inbound` "
        "should carry nearly all the flows. That agreement is what justifies using address role "
        "everywhere else -- `InIfBoundary` is unusable here because softflowd reports one ifIndex "
        "for both directions, but `FlowDirection` is populated independently and corroborates the "
        "workaround.\n\n"
        "If the diagonal breaks down, suspect `clickhouse.networks` in akvorado.yaml: a LAN prefix "
        "missing from it makes every local address look external and skews every direction-split "
        "panel on the Flow Overview tab. Rows with a blank role are flows where neither side "
        "matched a configured prefix."
    ), sort_col="Flows"), 0, 47, 24, 9)

    tabs.append(builder.tab("Pipeline Health", health))

    # ── Pipeline Internals ───────────────────────────────────────────────────
    #
    # Pipeline Health answers "can I trust the numbers": binary, instantaneous,
    # router-side. This tab answers "where in the collector is it struggling":
    # rate-of-change, saturation, backlog, all Akvorado-side. They are kept
    # apart because the first is for anyone reading the dashboard and the
    # second is only useful once something on the first tab looks wrong.
    #
    # Every metric name below was confirmed present on the live VictoriaMetrics
    # instance before being written here.

    internals: list[dict[str, Any]] = []
    builder.add(internals, text(500, "", (
        "## Where the collector is struggling\n"
        "Akvorado's own pipeline, in order: **inlet** receives UDP and pushes to Kafka without "
        "parsing, **Kafka** buffers, **outlet** consumes, decodes, enriches, and batch-inserts into "
        "ClickHouse.\n\n"
        "Walk it in that order when something is wrong -- the first stage that stops keeping up is "
        "the one to fix. Consumer lag rising while everything else looks fine means the outlet is "
        "the bottleneck; insert latency rising means ClickHouse is.\n\n"
        "All of this is collector health, not flow data. A perfectly healthy pipeline still cannot "
        "recover traffic that hardware offload hid from softflowd in the first place -- that is the "
        "Pipeline Health tab's job."
    )), 0, 0, 24, 5)

    # The top candidate alert for this stack and, until now, invisible on
    # every dashboard. Lag is the one number that says the outlet is not
    # keeping up with what the inlet is producing.
    builder.add(internals, timeseries(501, "Kafka Consumer Lag", [
        prom_query('sum(akvorado_outlet_kafka_consumergroup_lag_messages) or vector(0)', "messages behind", "A"),
    ], "none", (
        "Messages sitting in Kafka that the outlet has not consumed yet.\n\n"
        "Zero or near-zero is healthy and normal here. Sustained growth means the outlet cannot "
        "keep up with the inlet -- flows are not lost while Kafka still holds them, but they are "
        "arriving in ClickHouse late, so recent panels on the other tabs will read low until it "
        "catches up. If lag keeps climbing, the retention on the Kafka topic eventually decides "
        "what gets dropped.\n\n"
        "The threshold band is a judgement call, not a measured limit: this deployment sits at zero, "
        "so anything into the thousands is a real change of state rather than noise."
    ), thresholds_value=thresholds(("green", None), ("yellow", 1000), ("red", 10000))), 0, 5, 12, 8)

    # insert_time is a real histogram (_bucket with le), so histogram_quantile
    # applies. flow_per_batch below is a summary and must NOT be treated the
    # same way -- see its comment.
    builder.add(internals, timeseries(502, "ClickHouse Insert Latency", [
        prom_query(
            'histogram_quantile(0.50, sum by (le) (rate(akvorado_outlet_clickhouse_insert_time_seconds_bucket[$__rate_interval])))',
            "p50", "A",
        ),
        prom_query(
            'histogram_quantile(0.95, sum by (le) (rate(akvorado_outlet_clickhouse_insert_time_seconds_bucket[$__rate_interval])))',
            "p95", "B",
        ),
        prom_query(
            'histogram_quantile(0.99, sum by (le) (rate(akvorado_outlet_clickhouse_insert_time_seconds_bucket[$__rate_interval])))',
            "p99", "C",
        ),
    ], "s", (
        "How long each batch insert into ClickHouse takes, from Akvorado's own histogram.\n\n"
        "`le` is preserved inside the inner aggregation, which `histogram_quantile` requires. "
        "Rising p95 with flat p50 is ClickHouse occasionally stalling -- usually merges or disk "
        "pressure; both percentiles rising together is sustained write pressure. This is the stage "
        "immediately upstream of every ClickHouse panel on this dashboard, so latency here shows up "
        "there as data arriving late."
    ), thresholds_value=thresholds(("green", None), ("yellow", 1), ("red", 5))), 12, 5, 12, 8)

    builder.add(internals, timeseries(503, "Decoder Throughput by Record Type", [
        prom_query(
            'sum by (type) (rate(akvorado_outlet_flow_decoder_netflow_records_total[$__rate_interval]))',
            "{{type}}", "A",
        ),
    ], "none", (
        "NetFlow records decoded per second, split by record type.\n\n"
        "This panel exists for one specific failure that is otherwise invisible: **templates "
        "arriving with no data records**. NetFlow v9 sends the schema (`TemplateFlowSet`, "
        "`OptionsTemplateFlowSet`) separately from the data (`DataFlowSet`). If softflowd is running "
        "but capturing nothing, the template lines keep ticking along at their usual slow rate while "
        "`DataFlowSet` sits flat at zero -- the pipeline looks alive from every other angle and no "
        "flows arrive.\n\n"
        "Healthy is `DataFlowSet` far above the rest. `DataFlowSet` at zero with templates still "
        "flowing means checking the capture interface and pcap filter on the router."
    ), overrides=[
        color_override("DataFlowSet", GREEN),
        color_override("TemplateFlowSet", GRAY),
        color_override("OptionsTemplateFlowSet", GRAY),
        color_override("OptionsDataFlowSet", BLUE),
    ]), 0, 13, 12, 8)

    # Guarded divide: an unguarded hits/(hits+misses) yields +Inf or NaN
    # whenever the outlet has been restarted and neither counter has moved.
    builder.add(internals, timeseries(504, "Metadata Cache Hit Ratio", [
        prom_query(
            "sum(rate(akvorado_outlet_metadata_cache_hits_total[$__rate_interval])) "
            "/ (sum(rate(akvorado_outlet_metadata_cache_hits_total[$__rate_interval])) "
            "+ sum(rate(akvorado_outlet_metadata_cache_misses_total[$__rate_interval])) > 0)",
            "hit ratio", "A",
        ),
    ], "percentunit", (
        "Share of interface-metadata lookups served from the outlet's cache.\n\n"
        "The denominator is guarded with `> 0` so an idle or freshly-restarted outlet renders no "
        "data rather than `+Inf` or a misleading 100%.\n\n"
        "This should sit very close to 1 in a single-router deployment: there is one exporter and "
        "one interface, so after the first lookup everything is a hit. A ratio that drops and stays "
        "down means entries are being evicted or expired faster than they are used, which is the "
        "same underlying condition that produces `metadata missing` errors on the Collector Error "
        "Reasons panel."
    ), thresholds_value=thresholds(("red", None), ("yellow", 0.8), ("green", 0.95))), 12, 13, 12, 8)

    builder.add(internals, timeseries(505, "Outlet Worker Load", [
        prom_query('sum(rate(akvorado_outlet_clickhouse_worker_steady_total[$__rate_interval])) or vector(0)', "steady", "A"),
        prom_query('sum(rate(akvorado_outlet_clickhouse_worker_overloaded_total[$__rate_interval])) or vector(0)', "overloaded", "B"),
        prom_query('sum(rate(akvorado_outlet_clickhouse_worker_underloaded_total[$__rate_interval])) or vector(0)', "underloaded", "C"),
    ], "none", (
        "How often the outlet's ClickHouse writer workers reported themselves steady, overloaded, "
        "or underloaded -- Akvorado's own autoscaling signal.\n\n"
        "Sustained `overloaded` is the saturation warning that precedes consumer lag: the workers "
        "are asking for help before the backlog becomes visible upstream. `underloaded` dominating "
        "is the normal state on a home link and is not a problem to fix."
    ), stacked=True, overrides=[
        color_override("steady", GREEN),
        color_override("overloaded", RED),
        color_override("underloaded", GRAY),
    ]), 0, 21, 12, 8)

    # flow_per_batch is a SUMMARY, not a histogram: it exposes precomputed
    # quantiles under a `quantile` label and has no `le` buckets at all.
    # histogram_quantile() over it returns nothing. Select the quantiles
    # directly instead.
    builder.add(internals, timeseries(506, "Flows per ClickHouse Batch", [
        prom_query('akvorado_outlet_clickhouse_flow_per_batch{quantile="0.5"}', "p50", "A"),
        prom_query('akvorado_outlet_clickhouse_flow_per_batch{quantile="0.9"}', "p90", "B"),
        prom_query('akvorado_outlet_clickhouse_flow_per_batch{quantile="0.99"}', "p99", "C"),
    ], "none", (
        "How many flows the outlet packs into each ClickHouse insert.\n\n"
        "This is a Prometheus **summary**, not a histogram -- it publishes precomputed quantiles "
        "under a `quantile` label and has no `le` buckets, so `histogram_quantile()` over it "
        "returns nothing. The quantiles are selected directly.\n\n"
        "Read it together with insert latency: small batches with high latency means the outlet is "
        "flushing early under time pressure rather than filling batches, which is inefficient but "
        "not lossy. Batch size is driven by flow arrival rate, so it tracks how busy the network is."
    )), 12, 21, 12, 8)

    tabs.append(builder.tab("Pipeline Internals", internals))

    spec: dict[str, Any] = {
        "title": "OpenWrt - NetFlow",
        "description": "Per-flow traffic from softflowd via Akvorado, stored in ClickHouse, plus the exporter and collector health that says whether it is complete.",
        "tags": ["openwrt", "netflow", "router", "akvorado"],
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
            "from": "now-6h",
            "to": "now",
            "autoRefresh": "1m",
            "autoRefreshIntervals": ["30s", "1m", "5m", "15m"],
            "hideTimepicker": False,
            "fiscalYearStartMonth": 0,
        },
    }
    dashboard = {
        "apiVersion": "dashboard.grafana.app/v2beta1",
        "kind": "Dashboard",
        "metadata": {"name": "openwrt-netflow"},
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
    assert dashboard["metadata"]["name"] == "openwrt-netflow"
    spec = dashboard["spec"]
    assert spec["title"] == "OpenWrt - NetFlow"
    assert [tab["spec"]["title"] for tab in spec["layout"]["spec"]["tabs"]] == [
        "Flow Overview",
        "Applications",
        "External",
        "Security Signals",
        "Pipeline Health",
        "Pipeline Internals",
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
        assert "noValue" not in panel_spec["vizConfig"]["spec"]["options"], f"noValue in panel options: {key}"
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
    assert CH_DS in text_blob

    # Every ClickHouse byte/packet aggregation must be sampling-rate corrected.
    # Forgetting the multiplier under-reports silently and by exactly the
    # sampling factor, which is invisible unless you already know the answer.
    for element in spec["elements"].values():
        for query in element["spec"]["data"]["spec"]["queries"]:
            sql = query["spec"]["query"]["spec"].get("rawSql")
            if not sql:
                continue
            # softflowd reports one ifIndex for both directions, so any filter
            # on interface boundary silently matches all rows or none. See the
            # LOCAL_SRC/EXTERNAL_DST comment above.
            assert "IfBoundary" not in sql, (
                f"{element['spec']['id']}: query filters on interface boundary, "
                "which softflowd cannot populate meaningfully; use "
                "SrcNetRole/DstNetRole instead"
            )
            for match in re.finditer(r"sum(?:If)?\(([^()]*(?:Bytes|Packets)[^()]*)\)", sql):
                inner = match.group(1)
                assert "SamplingRate" in inner, (
                    f"{element['spec']['id']}: aggregate over {inner.strip()} is not "
                    "multiplied by SamplingRate"
                )

    # histogram_quantile() only works over a classic histogram's `le` buckets.
    # Akvorado publishes both shapes side by side and they are easy to confuse:
    # insert_time_seconds is a histogram (_bucket + le), flow_per_batch is a
    # summary that exposes precomputed quantiles under a `quantile` label and
    # has no buckets at all. histogram_quantile() over the summary returns
    # nothing -- an empty panel with no error anywhere, which is exactly the
    # failure this dashboard exists to make impossible elsewhere.
    for element in spec["elements"].values():
        for query in element["spec"]["data"]["spec"]["queries"]:
            expr = query["spec"]["query"]["spec"].get("expr")
            if not expr or "histogram_quantile" not in expr:
                continue
            metrics = re.findall(r"\b(akvorado_\w+|openwrt_\w+|node_\w+)\b", expr)
            assert metrics, f"{element['spec']['id']}: histogram_quantile over no known metric"
            for metric in metrics:
                assert metric.endswith("_bucket"), (
                    f"{element['spec']['id']}: histogram_quantile over {metric}, which is not a "
                    "_bucket series; summaries expose a `quantile` label instead and must be "
                    "selected directly"
                )

    defined = {variable["spec"]["name"] for variable in spec["variables"]}
    globals_allowed = {
        "__rate_interval", "__range", "__from", "__to", "__all", "__value",
        "__interval", "__auto",
        # grafana-clickhouse-datasource SQL macros.
        "__timeFilter", "__timeFilter_ms", "__timeInterval", "__timeInterval_ms",
        "__interval_s", "__conditionalAll", "__fromTime", "__toTime",
        "__dateFilter", "__dateTimeFilter",
    }
    referenced: set[str] = set()
    for value in iter_strings(spec):
        referenced.update(re.findall(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?", value))
    assert not (referenced - defined - globals_allowed), referenced - defined - globals_allowed
    assert not (defined - referenced - {"DS_PROMETHEUS", "DS_CLICKHOUSE"}), defined - referenced - {"DS_PROMETHEUS", "DS_CLICKHOUSE"}


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
