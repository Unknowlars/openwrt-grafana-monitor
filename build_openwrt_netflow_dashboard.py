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
    RED,
    THRESHOLDS,
    DashboardBuilder,
    bargauge,
    data_group,
    datasource_var,
    limit,
    organize,
    panel,
    prom_query,
    piechart,
    query_var,
    stat,
    stable_json,
    table,
    text,
    timeseries,
)


OUTS = [
    Path("grafana-dashboard-exports/openwrt-netflow-v2.json"),
    Path("grafana/provisioning/dashboards/openwrt-netflow-v2.json"),
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

    builder.add(overview, ch_stat(4, "Local Hosts", (
        "SELECT uniq(SrcAddr) AS hosts FROM flows "
        f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL} AND SrcNetRole = 'internal'"
    ), "none", "Distinct local source addresses seen. Depends on the `networks` prefixes in akvorado.yaml matching your addressing."), 12, 4, 4, 4)

    builder.add(overview, ch_stat(5, "External Peers", (
        "SELECT uniq(DstAddr) AS peers FROM flows "
        f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL} AND {EXTERNAL_DST}"
    ), "none", "Distinct internet-side addresses contacted."), 16, 4, 4, 4)

    builder.add(overview, ch_stat(11, "ASN Coverage", (
        "SELECT round(100 * countIf(SrcAS != 0 OR DstAS != 0) / greatest(count(), 1)) AS pct "
        "FROM flows "
        f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL} AND {CROSSES_BOUNDARY}"
    ), "percent", "Share of boundary-crossing flows with a source or destination AS after GeoIP/ASN enrichment."), 20, 4, 4, 4)

    builder.add(overview, timeseries(6, "Throughput by Direction", [ch_query(
        "SELECT $__timeInterval(TimeReceived) AS time, "
        "multiIf(SrcNetRole = 'internal' AND DstNetRole = 'internal', 'local', "
        "SrcNetRole = 'internal', 'outbound', 'inbound') AS direction, "
        "sum(Bytes * SamplingRate) * 8 / $__interval_s AS bps "
        "FROM flows "
        f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL} "
        "GROUP BY time, direction ORDER BY time"
    )], "bps", (
        "Bit rate split into outbound, inbound, and LAN-local, derived from whether each address "
        "falls in one of the `clickhouse.networks` prefixes in akvorado.yaml.\n\n"
        "This deliberately does NOT use InIfBoundary: softflowd reports the same ifIndex for both "
        "ingress and egress, so interface boundary is constant across every flow and cannot "
        "distinguish direction. If everything lands in one series, your LAN prefix is missing from "
        "`clickhouse.networks`."
    )), 0, 8, 24, 8)

    builder.add(overview, bargauge(7, "Top Local Talkers", [ch_query(
        "SELECT IPv6NumToString(SrcAddr) AS host, sum(Bytes * SamplingRate) AS bytes "
        "FROM flows "
        f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL} AND SrcNetRole = 'internal' "
        "GROUP BY host ORDER BY bytes DESC LIMIT 15",
        fmt=2,
    )], "bytes", "Busiest local sources by uploaded bytes. Raw `flows` table only, so bounded by the interval-0 retention window.", transformations=[limit(15)]), 0, 16, 12, 9)

    builder.add(overview, bargauge(8, "Top External Destinations", [ch_query(
        "SELECT IPv6NumToString(DstAddr) AS peer, sum(Bytes * SamplingRate) AS bytes "
        "FROM flows "
        f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL} AND {EXTERNAL_DST} "
        "GROUP BY peer ORDER BY bytes DESC LIMIT 15",
        fmt=2,
    )], "bytes", "Busiest internet-side destinations by bytes.", transformations=[limit(15)]), 12, 16, 12, 9)

    builder.add(overview, table(9, "Top Conversations", [ch_query(
        "SELECT IPv6NumToString(SrcAddr) AS Source, "
        "IPv6NumToString(DstAddr) AS Destination, "
        "DstPort AS Port, "
        "dictGetOrDefault('protocols', 'name', toUInt64(Proto), toString(Proto)) AS Protocol, "
        "sum(Bytes * SamplingRate) AS Bytes, "
        "sum(Packets * SamplingRate) AS Packets "
        "FROM flows "
        f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL} "
        "GROUP BY Source, Destination, Port, Protocol ORDER BY Bytes DESC LIMIT 50",
        fmt=2,
    )], "Individual source/destination/port conversations, heaviest first. This is the panel that answers 'what is saturating the link right now'.", sort_col="Bytes"), 0, 25, 24, 10)

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
    )], "bytes", "Internet-side destination ports by bytes. This is usually the most useful port ranking for web, DNS, streaming, VPN, and gaming traffic.", transformations=[limit(15)]), 0, 4, 8, 9)

    builder.add(applications, bargauge(106, "Top Source Ports", [ch_query(
        "SELECT concat(toString(SrcPort), '/', "
        "dictGetOrDefault('protocols', 'name', toUInt64(Proto), toString(Proto))) AS port, "
        "sum(Bytes * SamplingRate) AS bytes "
        "FROM flows "
        f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL} AND SrcPort > 0 "
        "GROUP BY port ORDER BY bytes DESC LIMIT 15",
        fmt=2,
    )], "bytes", "Source ports by bytes. Expect many ephemeral client ports here; fixed source ports such as 443/UDP or 9100/TCP are the interesting exceptions.", transformations=[limit(15)]), 8, 4, 8, 9)

    builder.add(applications, piechart(102, "Protocol Mix", [ch_query(
        "SELECT dictGetOrDefault('protocols', 'name', toUInt64(Proto), toString(Proto)) AS protocol, "
        "sum(Bytes * SamplingRate) AS bytes "
        "FROM flows "
        f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL} "
        "GROUP BY protocol ORDER BY bytes DESC LIMIT 10",
        fmt=2,
    )], "bytes", "Share of bytes by IP protocol."), 16, 4, 8, 9)

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
    )], "bps", "Bit rate over time for the eight busiest destination ports in the range.", stacked=True), 0, 13, 24, 9)

    builder.add(applications, piechart(107, "Service Class Mix", [ch_query(
        "SELECT multiIf("
        "DstPort IN (53, 853) OR SrcPort IN (53, 853), 'DNS / encrypted DNS', "
        "DstPort IN (80, 443, 8080, 8443) OR SrcPort IN (80, 443, 8080, 8443), 'Web / QUIC', "
        "DstPort IN (9100, 9090, 3000, 8081, 8123) OR SrcPort IN (9100, 9090, 3000, 8081, 8123), 'Monitoring stack', "
        "DstPort IN (22, 2222) OR SrcPort IN (22, 2222), 'SSH / admin', "
        "DstPort = 123 OR SrcPort = 123, 'NTP', "
        "DstPort = 5353 OR SrcPort = 5353, 'mDNS', "
        "DstPort = 1900 OR SrcPort = 1900, 'SSDP / discovery', "
        "'Other') AS service, "
        "sum(Bytes * SamplingRate) AS bytes "
        "FROM flows "
        f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL} "
        "GROUP BY service ORDER BY bytes DESC",
        fmt=2,
    )], "bytes", "Human-readable service buckets from source and destination ports. This is a convenience grouping, not DPI."), 0, 22, 8, 9)

    builder.add(applications, table(104, "Packet Size Distribution", [ch_query(
        "SELECT PacketSizeBucket AS Bucket, "
        "sum(Packets * SamplingRate) AS Packets, "
        "sum(Bytes * SamplingRate) AS Bytes "
        "FROM flows "
        f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL} "
        "GROUP BY Bucket ORDER BY Packets DESC",
        fmt=2,
    )], "Packet size buckets. A distribution skewed to small packets alongside high packet rates is a useful signal for interactive vs bulk traffic, and for scan-like behaviour."), 8, 22, 8, 9)

    builder.add(applications, table(105, "TCP Flag Combinations", [ch_query(
        "SELECT TCPFlags AS Flags, count() AS Flows, sum(Bytes * SamplingRate) AS Bytes "
        "FROM flows "
        f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL} AND Proto = 6 "
        "GROUP BY Flags ORDER BY Flows DESC LIMIT 20",
        fmt=2,
    )], "Raw TCP flag bitmasks. Large numbers of flows carrying only SYN (2) are the classic signature of a scan or of a service that is refusing connections."), 16, 22, 8, 9)

    builder.add(applications, bargauge(108, "Top Local Service Ports", [ch_query(
        "SELECT concat(toString(DstPort), '/', "
        "dictGetOrDefault('protocols', 'name', toUInt64(Proto), toString(Proto))) AS port, "
        "sum(Bytes * SamplingRate) AS bytes "
        "FROM flows "
        f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL} AND {LOCAL_DST} AND DstPort > 0 "
        "GROUP BY port ORDER BY bytes DESC LIMIT 15",
        fmt=2,
    )], "bytes", "Ports receiving traffic on internal destinations. Useful for spotting local scrapes, media servers, admin interfaces, and noisy LAN services.", transformations=[limit(15)]), 0, 31, 12, 9)

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

    tabs.append(builder.tab("Applications", applications))

    # ── External ─────────────────────────────────────────────────────────────

    external: list[dict[str, Any]] = []
    builder.add(external, text(200, "", (
        "## Autonomous systems and geography\n"
        "ASN and country enrichment comes from the MaxMind/IPinfo databases mounted in `akvorado/geoip/`. "
        "When the databases are missing, Akvorado still stores flows but AS, country, city, and network "
        "labels stay empty. The resolution tiles below make that state explicit.\n\n"
        "Read source-side panels as **who sent traffic to you or to local services** and destination-side "
        "panels as **where your clients sent traffic**. On a home LAN most source ports are ephemeral; AS "
        "and destination ports are usually the cleaner story."
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

    builder.add(external, ch_stat(203, "External Share", (
        "SELECT round(100 * sumIf(Bytes * SamplingRate, "
        f"{CROSSES_BOUNDARY}) "
        "/ greatest(sum(Bytes * SamplingRate), 1)) AS pct FROM flows "
        f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL}"
    ), "percent", "Share of bytes crossing the local network boundary rather than staying LAN-local. Derived from address role, not interface boundary."), 18, 5, 6, 4)

    builder.add(external, bargauge(204, "Top Destination AS", [ch_query(
        "SELECT concat('AS', toString(DstAS), ' ', "
        "dictGetOrDefault('asns', 'name', toUInt64(DstAS), '')) AS asn, "
        "sum(Bytes * SamplingRate) AS bytes "
        "FROM flows "
        f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL} AND {EXTERNAL_DST} AND DstAS != 0 "
        "GROUP BY asn ORDER BY bytes DESC LIMIT 15",
        fmt=2,
    )], "bytes", "Networks your traffic actually goes to, by bytes. Requires GeoIP/ASN.", transformations=[limit(15)]), 0, 9, 12, 9)

    builder.add(external, bargauge(208, "Top Source AS", [ch_query(
        "SELECT concat('AS', toString(SrcAS), ' ', "
        "dictGetOrDefault('asns', 'name', toUInt64(SrcAS), '')) AS asn, "
        "sum(Bytes * SamplingRate) AS bytes "
        "FROM flows "
        f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL} AND {EXTERNAL_SRC} AND SrcAS != 0 "
        "GROUP BY asn ORDER BY bytes DESC LIMIT 15",
        fmt=2,
    )], "bytes", "Internet-side source networks by bytes. This is the panel to inspect for inbound traffic or remote services sending data to the LAN.", transformations=[limit(15)]), 12, 9, 12, 9)

    builder.add(external, bargauge(205, "Top Destination Country", [ch_query(
        f"SELECT {DST_COUNTRY} AS country, sum(Bytes * SamplingRate) AS bytes "
        "FROM flows "
        f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL} AND {EXTERNAL_DST} AND {DST_COUNTRY} != '' "
        "GROUP BY country ORDER BY bytes DESC LIMIT 15",
        fmt=2,
    )], "bytes", "Destination countries by bytes. Requires GeoIP.", transformations=[limit(15)]), 0, 18, 12, 9)

    builder.add(external, bargauge(209, "Top Source Country", [ch_query(
        f"SELECT {SRC_COUNTRY} AS country, sum(Bytes * SamplingRate) AS bytes "
        "FROM flows "
        f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL} AND {EXTERNAL_SRC} AND {SRC_COUNTRY} != '' "
        "GROUP BY country ORDER BY bytes DESC LIMIT 15",
        fmt=2,
    )], "bytes", "Internet-side source countries by bytes. Missing countries are stripped instead of showing ClickHouse FixedString NUL bytes.", transformations=[limit(15)]), 12, 18, 12, 9)

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
    )], "bps", "Bit rate to the eight busiest destination networks. Requires GeoIP/ASN.", stacked=True), 0, 27, 12, 9)

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
    )], "bps", "Bit rate from the eight busiest source networks. Useful for inbound traffic and remote services that send large responses.", stacked=True), 12, 27, 12, 9)

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
    )], "Country-to-country pairs after stripping missing FixedString values. This is a compact way to see where traffic is going without exposing individual IPs.", sort_col="Bytes"), 0, 46, 12, 9)

    builder.add(external, table(213, "Top Geo Cities", [ch_query(
        "SELECT "
        f"multiIf(DstGeoCity != '', concat({DST_COUNTRY}, ' / ', DstGeoCity), "
        f"SrcGeoCity != '', concat({SRC_COUNTRY}, ' / ', SrcGeoCity), 'unresolved') AS City, "
        "count() AS Flows, "
        "sum(Bytes * SamplingRate) AS Bytes "
        "FROM flows "
        f"WHERE $__timeFilter(TimeReceived) AND {ROUTER_SQL} AND {CROSSES_BOUNDARY} "
        "AND (DstGeoCity != '' OR SrcGeoCity != '') "
        "GROUP BY City ORDER BY Bytes DESC LIMIT 50",
        fmt=2,
    )], "City-level enrichment when the mounted GeoIP database provides it. Empty is normal with country-only databases.", sort_col="Bytes"), 12, 46, 12, 9)

    tabs.append(builder.tab("External", external))

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

    tabs.append(builder.tab("Pipeline Health", health))

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
        "Pipeline Health",
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
