"""
OpenWRT Grafana Monitor — Dashboard Builder
Generated from live metrics at http://192.168.0.1:9100/metrics

Confirmed metrics and interfaces:
  WAN interface:       wan
  WiFi 2.4 GHz AP:    phy0-ap0
  WiFi 5 GHz AP:      phy1-ap0
  Tailscale VPN:      tailscale0
  LAN bridge:         br-lan
  Router model:       ASUS RT-AX53U (MediaTek MT7621)

Key metrics confirmed live:
  node_cpu_seconds_total{cpu, mode}
  node_memory_*_bytes
  node_load1/5/15
  node_nf_conntrack_entries / node_nf_conntrack_entries_limit
  node_network_*_total{device}
  node_hwmon_temp_celsius / node_thermal_zone_temp
  router_device_up{device, status, mac, ip}
  dhcp_lease{mac, hostname, ip}
  uci_dhcp_host{name, mac, ip}
  wifi_network_quality{ifname, ssid, device}
  wifi_network_signal_dbm{ifname, ssid, device}
  wifi_stations{ifname}
  dnsmasq_*
  packet_loss
  wan_info{wanip, publicip}
  node_openwrt_info{board_name, model, release, ...}
  node_uname_info{release, machine, nodename}
  node_boot_time_seconds / node_time_seconds
  node_filefd_allocated / node_filefd_maximum
"""

import json
import copy

DS = {"type": "prometheus", "uid": "${DS_PROMETHEUS}"}

# ── Target builder ────────────────────────────────────────────────────────────

def tgt(expr, legend="", ref="A", fmt="time_series", instant=False):
    t = {
        "datasource": copy.deepcopy(DS),
        "expr": expr,
        "legendFormat": legend,
        "refId": ref,
    }
    if fmt != "time_series":
        t["format"] = fmt
    if instant:
        t["instant"] = True
    return t

# ── Panel builders ────────────────────────────────────────────────────────────

def stat(id, title, expr, x, y, w, h, unit="short", legend="", desc="",
         thresholds=None, graph=False):
    steps = thresholds or [{"color": "blue", "value": 0}]
    return {
        "type": "stat", "id": id, "title": title, "description": desc,
        "datasource": copy.deepcopy(DS),
        "targets": [tgt(expr, legend)],
        "fieldConfig": {
            "defaults": {
                "color": {"mode": "thresholds"},
                "mappings": [],
                "thresholds": {"mode": "absolute", "steps": steps},
                "unit": unit,
            },
            "overrides": [],
        },
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "options": {
            "colorMode": "background",
            "graphMode": "area" if graph else "none",
            "justifyMode": "auto",
            "orientation": "auto",
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "textMode": "auto",
            "wideLayout": True,
        },
        "pluginVersion": "12.4.0",
    }

def ts(id, title, targets, x, y, w, h, unit="short", calcs=None,
       stacked=False, fill=15, desc="", overrides=None):
    custom = {
        "drawStyle": "line",
        "lineInterpolation": "smooth",
        "lineWidth": 2,
        "fillOpacity": fill,
        "gradientMode": "opacity",
        "showPoints": "auto",
        "spanNulls": False,
        "stacking": {"group": "A", "mode": "normal" if stacked else "none"},
        "thresholdsStyle": {"mode": "off"},
        "scaleDistribution": {"type": "linear"},
        "hideFrom": {"legend": False, "tooltip": False, "viz": False},
    }
    return {
        "type": "timeseries", "id": id, "title": title, "description": desc,
        "datasource": copy.deepcopy(DS), "targets": targets,
        "fieldConfig": {
            "defaults": {
                "color": {"mode": "palette-classic"},
                "custom": custom,
                "mappings": [],
                "unit": unit,
                "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": 0}]},
            },
            "overrides": overrides or [],
        },
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "options": {
            "legend": {
                "calcs": calcs or ["lastNotNull"],
                "displayMode": "table",
                "placement": "bottom",
                "showLegend": True,
            },
            "tooltip": {"hideZeros": False, "mode": "multi", "sort": "desc"},
        },
        "transformations": [],
        "pluginVersion": "12.4.0",
    }

def table(id, title, targets, x, y, w, h, desc="", overrides=None,
          transforms=None, sort_col=None, sort_desc=True):
    return {
        "type": "table", "id": id, "title": title, "description": desc,
        "datasource": copy.deepcopy(DS), "targets": targets,
        "fieldConfig": {
            "defaults": {
                "custom": {
                    "align": "auto",
                    "cellOptions": {"type": "auto"},
                    "filterable": True,
                    "inspect": False,
                },
                "mappings": [],
                "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": 0}]},
            },
            "overrides": overrides or [],
        },
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "options": {
            "cellHeight": "sm",
            "showHeader": True,
            "footer": {"show": False, "reducer": ["sum"], "fields": ""},
            "sortBy": [{"desc": sort_desc, "displayName": sort_col}] if sort_col else [],
        },
        "transformations": transforms or [],
        "pluginVersion": "12.4.0",
    }

def row_panel(id, title, y, collapsed=False):
    return {
        "type": "row", "id": id, "title": title,
        "collapsed": collapsed,
        "panels": [],
        "gridPos": {"x": 0, "y": y, "w": 24, "h": 1},
    }

def bargauge(id, title, targets, x, y, w, h, unit="short", min=0, max=None,
             thresholds=None, desc="", orientation="horizontal"):
    steps = thresholds or [
        {"color": "green", "value": 0},
        {"color": "yellow", "value": 0.6},
        {"color": "red", "value": 0.85},
    ]
    fd = {"color": {"mode": "thresholds"}, "mappings": [], "min": min,
          "thresholds": {"mode": "absolute", "steps": steps}, "unit": unit}
    if max is not None:
        fd["max"] = max
    return {
        "type": "bargauge", "id": id, "title": title, "description": desc,
        "datasource": copy.deepcopy(DS), "targets": targets,
        "fieldConfig": {"defaults": fd, "overrides": []},
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "options": {
            "displayMode": "gradient",
            "orientation": orientation,
            "namePlacement": "auto",
            "valueMode": "color",
            "showUnfilled": True,
            "sizing": "auto",
            "minVizHeight": 16, "maxVizHeight": 300, "minVizWidth": 8,
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "legend": {"calcs": [], "displayMode": "list", "placement": "bottom", "showLegend": False},
        },
        "transformations": [],
        "pluginVersion": "12.4.0",
    }

# ── Common dashboard wrapper ───────────────────────────────────────────────────

TEMPLATING = {"list": [
    {
        "name": "DS_PROMETHEUS", "type": "datasource", "query": "prometheus",
        "refresh": 1, "includeAll": False, "options": [], "regex": "",
        "current": {"text": "Prometheus", "value": "prometheus"},
        "hide": 0, "label": "Datasource",
    },
]}

ANNOTATIONS = {"list": [{
    "builtIn": 1,
    "datasource": {"type": "grafana", "uid": "-- Grafana --"},
    "enable": True, "hide": True, "iconColor": "rgba(0, 211, 255, 1)",
    "name": "Annotations & Alerts", "type": "dashboard",
}]}

def make_dashboard(uid, title, description, panels, tags=None, refresh="30s", time_from="now-3h"):
    for p in panels:
        gp = p.get("gridPos", {})
        assert gp.get("x", 0) + gp.get("w", 0) <= 24, \
            f"Panel {p['id']} [{p['title']}] overflows grid: x={gp.get('x')} w={gp.get('w')}"
    return {
        "title": title,
        "uid": uid,
        "description": description,
        "tags": tags or ["openwrt"],
        "schemaVersion": 42,
        "version": 1,
        "refresh": refresh,
        "timezone": "browser",
        "graphTooltip": 1,
        "time": {"from": time_from, "to": "now"},
        "timepicker": {},
        "weekStart": "",
        "fiscalYearStartMonth": 0,
        "preload": False,
        "editable": True,
        "annotations": ANNOTATIONS,
        "links": [
            {"title": "Overview",  "url": "/d/openwrt-overview", "type": "link", "icon": "external link"},
            {"title": "Network",   "url": "/d/openwrt-network",  "type": "link", "icon": "external link"},
            {"title": "Devices",   "url": "/d/openwrt-devices",  "type": "link", "icon": "external link"},
            {"title": "Logs",      "url": "/d/openwrt-logs",     "type": "link", "icon": "external link"},
        ],
        "panels": panels,
        "templating": TEMPLATING,
    }

# ═══════════════════════════════════════════════════════════════════════════════
# DASHBOARD 1 — OVERVIEW
# ═══════════════════════════════════════════════════════════════════════════════

def build_overview():
    panels = []
    y = 0

    # ── Tier 1: KPI stats row (8 × w=3) ─────────────────────────────────────
    # 1. Uptime
    panels.append(stat(1, "Uptime",
        'node_time_seconds{job="openwrt"} - node_boot_time_seconds{job="openwrt"}',
        x=0, y=y, w=4, h=4, unit="s", desc="Time since last reboot",
        thresholds=[
            {"color": "red",    "value": 0},
            {"color": "yellow", "value": 3600},
            {"color": "green",  "value": 86400},
        ]))

    # 2. Memory used %
    panels.append(stat(2, "Memory Used",
        '1 - (node_memory_MemAvailable_bytes{job="openwrt"} / node_memory_MemTotal_bytes{job="openwrt"})',
        x=4, y=y, w=4, h=4, unit="percentunit",
        desc="Percentage of RAM in use. Routers often run tight — >90% is a concern.",
        thresholds=[
            {"color": "green",  "value": 0},
            {"color": "yellow", "value": 0.7},
            {"color": "red",    "value": 0.9},
        ]))

    # 3. CPU busy %
    panels.append(stat(3, "CPU Busy",
        '1 - avg(rate(node_cpu_seconds_total{job="openwrt", mode="idle"}[$__rate_interval]))',
        x=8, y=y, w=4, h=4, unit="percentunit",
        desc="Average CPU utilisation across all cores",
        thresholds=[
            {"color": "green",  "value": 0},
            {"color": "yellow", "value": 0.5},
            {"color": "red",    "value": 0.8},
        ]))

    # 4. Online devices
    panels.append(stat(4, "Online Devices",
        'count(router_device_up{job="openwrt"} == 1)',
        x=12, y=y, w=4, h=4, unit="short",
        desc="Number of devices currently seen as online by the router",
        thresholds=[{"color": "blue", "value": 0}]))

    # 5. NAT sessions
    panels.append(stat(5, "NAT Sessions",
        'node_nf_conntrack_entries{job="openwrt"}',
        x=16, y=y, w=4, h=4, unit="short",
        desc="Active NAT/conntrack sessions. High values may indicate port scanning or excessive connections.",
        thresholds=[
            {"color": "green",  "value": 0},
            {"color": "yellow", "value": 5000},
            {"color": "red",    "value": 15000},
        ]))

    # 6. Packet loss
    panels.append(stat(6, "Packet Loss",
        'packet_loss{job="openwrt"}',
        x=20, y=y, w=4, h=4, unit="percent",
        desc="Packet loss percentage on WAN connection. 0 = perfect, >5% = degraded.",
        thresholds=[
            {"color": "green",  "value": 0},
            {"color": "yellow", "value": 1},
            {"color": "red",    "value": 5},
        ]))
    y += 4

    # ── Tier 2: WAN throughput + CPU load ────────────────────────────────────
    panels.append(ts(7, "WAN Throughput (wan)",
        targets=[
            tgt('rate(node_network_receive_bytes_total{job="openwrt", device="wan"}[$__rate_interval])',
                "Download (RX)", "A"),
            tgt('rate(node_network_transmit_bytes_total{job="openwrt", device="wan"}[$__rate_interval])',
                "Upload (TX)", "B"),
        ],
        x=0, y=y, w=12, h=9, unit="Bps",
        desc="Real-time WAN download and upload throughput",
        overrides=[
            {"matcher": {"id": "byName", "options": "Download (RX)"},
             "properties": [{"id": "color", "value": {"fixedColor": "#1a9e3a", "mode": "fixed"}}]},
            {"matcher": {"id": "byName", "options": "Upload (TX)"},
             "properties": [{"id": "color", "value": {"fixedColor": "#5794F2", "mode": "fixed"}}]},
        ]))

    panels.append(ts(8, "CPU Load Average",
        targets=[
            tgt('node_load1{job="openwrt"}',  "1 min",  "A"),
            tgt('node_load5{job="openwrt"}',  "5 min",  "B"),
            tgt('node_load15{job="openwrt"}', "15 min", "C"),
        ],
        x=12, y=y, w=12, h=9, unit="short",
        desc="System load average. On this 4-thread MT7621 router, values >4 indicate sustained overload.",
        calcs=["lastNotNull", "max"]))
    y += 9

    # ── Tier 2: Memory + NAT sessions ────────────────────────────────────────
    panels.append(ts(9, "Memory Usage",
        targets=[
            tgt('node_memory_MemTotal_bytes{job="openwrt"} - node_memory_MemAvailable_bytes{job="openwrt"}',
                "Used", "A"),
            tgt('node_memory_MemAvailable_bytes{job="openwrt"}',
                "Available", "B"),
            tgt('node_memory_Buffers_bytes{job="openwrt"} + node_memory_Cached_bytes{job="openwrt"}',
                "Buffers+Cache", "C"),
        ],
        x=0, y=y, w=12, h=8, unit="bytes", stacked=True, fill=40,
        desc="RAM breakdown. 'Used' is actively allocated. 'Buffers+Cache' can be reclaimed."))

    panels.append(ts(10, "NAT Conntrack Sessions",
        targets=[
            tgt('node_nf_conntrack_entries{job="openwrt"}',
                "Active sessions", "A"),
            tgt('node_nf_conntrack_entries_limit{job="openwrt"}',
                "Maximum limit", "B"),
        ],
        x=12, y=y, w=12, h=8, unit="short",
        desc="Active NAT sessions vs the table limit. Approaching the limit causes connection failures.",
        overrides=[
            {"matcher": {"id": "byName", "options": "Maximum limit"},
             "properties": [
                 {"id": "color", "value": {"fixedColor": "#F2495C", "mode": "fixed"}},
                 {"id": "custom.lineStyle", "value": {"dash": [10, 10], "fill": "dash"}},
             ]},
        ]))
    y += 8

    # ── Tier 2: Per-CPU breakdown ─────────────────────────────────────────────
    panels.append(ts(11, "CPU Usage by Mode (all cores)",
        targets=[
            tgt('sum(rate(node_cpu_seconds_total{job="openwrt", mode="user"}[$__rate_interval]))',
                "user", "A"),
            tgt('sum(rate(node_cpu_seconds_total{job="openwrt", mode="system"}[$__rate_interval]))',
                "system", "B"),
            tgt('sum(rate(node_cpu_seconds_total{job="openwrt", mode="softirq"}[$__rate_interval]))',
                "softirq", "C"),
            tgt('sum(rate(node_cpu_seconds_total{job="openwrt", mode="iowait"}[$__rate_interval]))',
                "iowait", "D"),
        ],
        x=0, y=y, w=24, h=8, unit="short", stacked=True, fill=60,
        desc="CPU time breakdown across all 4 threads. softirq = network packet processing (normal on a router)."))
    y += 8

    # ── Tier 3: Router info table ─────────────────────────────────────────────
    panels.append(row_panel(20, "Router Info", y))
    y += 1

    panels.append(table(21, "OpenWRT Firmware Info",
        targets=[tgt(
            'node_openwrt_info{job="openwrt"}',
            "", "A", fmt="table", instant=True,
        )],
        x=0, y=y, w=24, h=4,
        desc="Router hardware and firmware details from node_openwrt_info",
        transforms=[
            {"id": "organize", "options": {
                "excludeByName": {"Time": True, "__name__": True, "Value": True, "job": True},
                "renameByName": {
                    "board_name": "Board",
                    "model": "Model",
                    "release": "OpenWRT Release",
                    "revision": "Revision",
                    "target": "Target",
                    "system": "CPU",
                    "id": "ID",
                },
            }},
        ]))
    y += 4

    panels.append(table(22, "WAN IP Info",
        targets=[tgt(
            'wan_info{job="openwrt"}',
            "", "A", fmt="table", instant=True,
        )],
        x=0, y=y, w=12, h=4,
        desc="Current WAN and public IP addresses",
        transforms=[
            {"id": "organize", "options": {
                "excludeByName": {"Time": True, "__name__": True, "Value": True, "job": True},
                "renameByName": {"wanip": "WAN IP", "publicip": "Public IP"},
            }},
        ]))

    panels.append(stat(23, "Router Temperature",
        'max(node_hwmon_temp_celsius{job="openwrt"}) or max(node_thermal_zone_temp{job="openwrt"})',
        x=12, y=y, w=6, h=4, unit="celsius",
        desc="Preferred temperature signal from hwmon or thermal collectors. Install both if available on your router.",
        thresholds=[
            {"color": "green", "value": 0},
            {"color": "yellow", "value": 70},
            {"color": "red", "value": 85},
        ]))

    panels.append(stat(24, "WiFi Clients",
        'sum(wifi_stations{job="openwrt"})',
        x=18, y=y, w=6, h=4, unit="short",
        desc="Associated WiFi clients across all access-point interfaces. Requires the wifi_stations collector.",
        thresholds=[{"color": "blue", "value": 0}]))

    return make_dashboard(
        uid="openwrt-overview",
        title="OpenWRT — Overview",
        description="OpenWRT system health: CPU, memory, WAN throughput, NAT sessions, WiFi clients, and router temperature.",
        panels=panels,
        tags=["openwrt", "overview"],
    )

# ═══════════════════════════════════════════════════════════════════════════════
# DASHBOARD 2 — NETWORK
# ═══════════════════════════════════════════════════════════════════════════════

def build_network():
    panels = []
    y = 0

    # ── WAN section ──────────────────────────────────────────────────────────
    panels.append(row_panel(1, "WAN — Internet Connection", y))
    y += 1

    panels.append(ts(2, "WAN Download (RX)",
        targets=[tgt(
            'rate(node_network_receive_bytes_total{job="openwrt", device="wan"}[$__rate_interval])',
            "WAN Download", "A")],
        x=0, y=y, w=12, h=8, unit="Bps",
        desc="Bytes per second received on the WAN interface (internet download)",
        overrides=[{"matcher": {"id": "byName", "options": "WAN Download"},
                    "properties": [{"id": "color", "value": {"fixedColor": "#1a9e3a", "mode": "fixed"}}]}]))

    panels.append(ts(3, "WAN Upload (TX)",
        targets=[tgt(
            'rate(node_network_transmit_bytes_total{job="openwrt", device="wan"}[$__rate_interval])',
            "WAN Upload", "A")],
        x=12, y=y, w=12, h=8, unit="Bps",
        desc="Bytes per second transmitted on the WAN interface (internet upload)",
        overrides=[{"matcher": {"id": "byName", "options": "WAN Upload"},
                    "properties": [{"id": "color", "value": {"fixedColor": "#5794F2", "mode": "fixed"}}]}]))
    y += 8

    panels.append(ts(4, "WAN Packets/sec",
        targets=[
            tgt('rate(node_network_receive_packets_total{job="openwrt", device="wan"}[$__rate_interval])',
                "RX packets", "A"),
            tgt('rate(node_network_transmit_packets_total{job="openwrt", device="wan"}[$__rate_interval])',
                "TX packets", "B"),
        ],
        x=0, y=y, w=12, h=7, unit="pps",
        desc="Packets per second on WAN. High PPS with low bytes = many small packets (DNS, keepalives)."))

    panels.append(ts(5, "WAN Errors & Drops",
        targets=[
            tgt('rate(node_network_receive_errs_total{job="openwrt", device="wan"}[$__rate_interval])',
                "RX errors", "A"),
            tgt('rate(node_network_receive_drop_total{job="openwrt", device="wan"}[$__rate_interval])',
                "RX drops", "B"),
            tgt('rate(node_network_transmit_errs_total{job="openwrt", device="wan"}[$__rate_interval])',
                "TX errors", "C"),
        ],
        x=12, y=y, w=12, h=7, unit="pps",
        desc="Non-zero errors indicate hardware problems, driver issues, or line quality issues.",
        overrides=[
            {"matcher": {"id": "byName", "options": "RX errors"},
             "properties": [{"id": "color", "value": {"fixedColor": "#F2495C", "mode": "fixed"}}]},
            {"matcher": {"id": "byName", "options": "TX errors"},
             "properties": [{"id": "color", "value": {"fixedColor": "#FF9830", "mode": "fixed"}}]},
        ]))
    y += 7

    # ── WiFi section ─────────────────────────────────────────────────────────
    panels.append(row_panel(10, "WiFi — phy0-ap0 (2.4 GHz) + phy1-ap0 (5 GHz)", y))
    y += 1

    panels.append(ts(11, "WiFi AP Throughput — Both Bands",
        targets=[
            tgt('rate(node_network_receive_bytes_total{job="openwrt", device="phy0-ap0"}[$__rate_interval])',
                "2.4 GHz RX", "A"),
            tgt('rate(node_network_transmit_bytes_total{job="openwrt", device="phy0-ap0"}[$__rate_interval])',
                "2.4 GHz TX", "B"),
            tgt('rate(node_network_receive_bytes_total{job="openwrt", device="phy1-ap0"}[$__rate_interval])',
                "5 GHz RX", "C"),
            tgt('rate(node_network_transmit_bytes_total{job="openwrt", device="phy1-ap0"}[$__rate_interval])',
                "5 GHz TX", "D"),
        ],
        x=0, y=y, w=24, h=8, unit="Bps",
        desc="Traffic on the 2.4 GHz (phy0-ap0) and 5 GHz (phy1-ap0) WiFi access point interfaces"))
    y += 8

    panels.append(ts(12, "WiFi Clients by AP",
        targets=[tgt(
            'wifi_stations{job="openwrt"}',
            '{{ifname}}', "A")],
        x=0, y=y, w=12, h=7, unit="short",
        desc="Associated WiFi client count per AP interface. Requires the wifi_stations collector."))

    panels.append(ts(13, "WiFi Signal by AP",
        targets=[tgt(
            'wifi_network_signal_dbm{job="openwrt"}',
            '{{ifname}} {{ssid}}', "A")],
        x=12, y=y, w=12, h=7, unit="dBm",
        desc="Reported signal level per WiFi AP interface. Requires the wifi collector."))
    y += 7

    # ── LAN section ──────────────────────────────────────────────────────────
    panels.append(row_panel(20, "LAN & Internal Interfaces", y))
    y += 1

    panels.append(ts(21, "All Interface Throughput (RX)",
        targets=[tgt(
            'rate(node_network_receive_bytes_total{job="openwrt", device!~"lo|lan2|lan3"}[$__rate_interval])',
            "{{device}}", "A")],
        x=0, y=y, w=12, h=8, unit="Bps",
        desc="RX throughput per interface. Excludes loopback and unused LAN ports (lan2, lan3)."))

    panels.append(ts(22, "All Interface Throughput (TX)",
        targets=[tgt(
            'rate(node_network_transmit_bytes_total{job="openwrt", device!~"lo|lan2|lan3"}[$__rate_interval])',
            "{{device}}", "A")],
        x=12, y=y, w=12, h=8, unit="Bps",
        desc="TX throughput per interface. Excludes loopback and unused LAN ports."))
    y += 8

    # ── Tailscale section ─────────────────────────────────────────────────────
    panels.append(row_panel(30, "Tailscale VPN (tailscale0)", y))
    y += 1

    panels.append(ts(31, "Tailscale VPN Throughput",
        targets=[
            tgt('rate(node_network_receive_bytes_total{job="openwrt", device="tailscale0"}[$__rate_interval])',
                "VPN RX", "A"),
            tgt('rate(node_network_transmit_bytes_total{job="openwrt", device="tailscale0"}[$__rate_interval])',
                "VPN TX", "B"),
        ],
        x=0, y=y, w=12, h=7, unit="Bps",
        desc="Traffic flowing through the Tailscale VPN tunnel"))

    panels.append(ts(32, "NAT Conntrack Sessions",
        targets=[
            tgt('node_nf_conntrack_entries{job="openwrt"}',         "Active sessions", "A"),
            tgt('node_nf_conntrack_entries_limit{job="openwrt"}',   "Limit",           "B"),
        ],
        x=12, y=y, w=12, h=7, unit="short",
        desc="NAT connection tracking table usage vs limit. Approaching the limit causes new connections to fail."))
    y += 7

    # ── DNS/DHCP section ──────────────────────────────────────────────────────
    panels.append(row_panel(40, "DNS & DHCP (dnsmasq)", y))
    y += 1

    panels.append(ts(41, "DNS Queries",
        targets=[
            tgt('rate(dnsmasq_dns_queries_forwarded{job="openwrt"}[$__rate_interval])',
                "Forwarded", "A"),
            tgt('rate(dnsmasq_dns_local_answered{job="openwrt"}[$__rate_interval])',
                "Local (cached)", "B"),
            tgt('rate(dnsmasq_dns_unanswered{job="openwrt"}[$__rate_interval])',
                "Unanswered", "C"),
        ],
        x=0, y=y, w=12, h=8, unit="reqps",
        desc="DNS query rates. 'Local' = served from cache. 'Forwarded' = sent upstream. 'Unanswered' = failures."))

    panels.append(ts(42, "DHCP Events",
        targets=[
            tgt('rate(dnsmasq_dhcp_ack{job="openwrt"}[$__rate_interval])',
                "ACK (granted)", "A"),
            tgt('rate(dnsmasq_dhcp_request{job="openwrt"}[$__rate_interval])',
                "Request", "B"),
            tgt('rate(dnsmasq_dhcp_discover{job="openwrt"}[$__rate_interval])',
                "Discover (new)", "C"),
        ],
        x=12, y=y, w=12, h=8, unit="reqps",
        desc="DHCP handshake events. Many 'Discover' events indicate devices frequently reconnecting."))
    y += 8

    # ── Interface summary table ────────────────────────────────────────────────
    panels.append(row_panel(50, "Interface Summary", y))
    y += 1

    panels.append(table(51, "Network Interface Status",
        targets=[
            tgt('node_network_info{job="openwrt"}', "", "A", fmt="table", instant=True),
        ],
        x=0, y=y, w=24, h=8,
        desc="Current state of all network interfaces",
        transforms=[
            {"id": "organize", "options": {
                "excludeByName": {"Time": True, "__name__": True, "Value": True, "job": True,
                                  "ifalias": True, "broadcast": True},
                "renameByName": {
                    "device": "Interface",
                    "operstate": "State",
                    "address": "MAC",
                    "duplex": "Duplex",
                },
            }},
        ],
        overrides=[
            {"matcher": {"id": "byName", "options": "State"}, "properties": [
                {"id": "custom.cellOptions", "value": {"type": "color-background"}},
                {"id": "mappings", "value": [
                    {"type": "value", "options": {"up": {"color": "#1a9e3a", "text": "up", "index": 0}}},
                    {"type": "value", "options": {"down": {"color": "#F2495C", "text": "down", "index": 1}}},
                    {"type": "value", "options": {"lowerlayerdown": {"color": "#808080", "text": "no cable", "index": 2}}},
                ]},
            ]},
        ]))

    return make_dashboard(
        uid="openwrt-network",
        title="OpenWRT — Network",
        description="WAN throughput, WiFi AP traffic and client counts, Tailscale VPN, LAN interfaces, DNS/DHCP stats.",
        panels=panels,
        tags=["openwrt", "network"],
    )

# ═══════════════════════════════════════════════════════════════════════════════
# DASHBOARD 3 — DEVICES
# Uses bundled lease/presence collectors and optional WiFi station metrics
# ═══════════════════════════════════════════════════════════════════════════════

def build_devices():
    panels = []
    y = 0

    # ── Stats row ────────────────────────────────────────────────────────────
    panels.append(stat(1, "Online Devices",
        'count(router_device_up{job="openwrt"} == 1)',
        x=0, y=y, w=6, h=4, unit="short",
        desc="Devices currently seen as online by the router",
        thresholds=[{"color": "blue", "value": 0}]))

    panels.append(stat(2, "Offline Devices",
        'count(router_device_up{job="openwrt"} == 0)',
        x=6, y=y, w=6, h=4, unit="short",
        desc="Devices with known leases that are currently offline",
        thresholds=[
            {"color": "green", "value": 0},
            {"color": "yellow", "value": 5},
        ]))

    panels.append(stat(3, "DHCP Leases",
        'count(dhcp_lease{job="openwrt"})',
        x=12, y=y, w=6, h=4, unit="short",
        desc="Total DHCP leases (active + recently expired)",
        thresholds=[{"color": "blue", "value": 0}]))

    panels.append(stat(4, "Packet Loss %",
        'packet_loss{job="openwrt"}',
        x=18, y=y, w=6, h=4, unit="percent",
        desc="Current WAN packet loss percentage",
        thresholds=[
            {"color": "green", "value": 0},
            {"color": "yellow", "value": 1},
            {"color": "red", "value": 5},
        ]))
    y += 4

    # ── Online devices over time ──────────────────────────────────────────────
    panels.append(ts(5, "Online Device Count Over Time",
        targets=[
            tgt('count(router_device_up{job="openwrt"} == 1)', "Online",  "A"),
            tgt('count(router_device_up{job="openwrt"} == 0)', "Offline", "B"),
        ],
        x=0, y=y, w=12, h=8, unit="short",
        desc="How many devices are online vs offline over time",
        overrides=[
            {"matcher": {"id": "byName", "options": "Online"},
             "properties": [{"id": "color", "value": {"fixedColor": "#1a9e3a", "mode": "fixed"}}]},
            {"matcher": {"id": "byName", "options": "Offline"},
             "properties": [{"id": "color", "value": {"fixedColor": "#808080", "mode": "fixed"}}]},
        ]))

    panels.append(bargauge(6, "Top Devices by NAT Traffic (bytes)",
        targets=[tgt(
            'topk(10, sum by(src)(node_nat_traffic{job="openwrt"}))',
            "{{src}}", "A",
        )],
        x=12, y=y, w=12, h=8, unit="bytes",
        desc="Top 10 LAN clients by total NAT traffic bytes (current snapshot, not rate)",
        thresholds=[
            {"color": "green",  "value": 0},
            {"color": "blue",   "value": 10000},
            {"color": "purple", "value": 100000},
        ]))
    y += 8

    # ── WiFi AP traffic ───────────────────────────────────────────────────────
    panels.append(row_panel(10, "WiFi Access Points", y))
    y += 1

    panels.append(ts(11, "WiFi AP Throughput",
        targets=[
            tgt('rate(node_network_receive_bytes_total{job="openwrt", device="phy0-ap0"}[$__rate_interval])',
                "2.4 GHz RX (phy0-ap0)", "A"),
            tgt('rate(node_network_transmit_bytes_total{job="openwrt", device="phy0-ap0"}[$__rate_interval])',
                "2.4 GHz TX (phy0-ap0)", "B"),
            tgt('rate(node_network_receive_bytes_total{job="openwrt", device="phy1-ap0"}[$__rate_interval])',
                "5 GHz RX (phy1-ap0)", "C"),
            tgt('rate(node_network_transmit_bytes_total{job="openwrt", device="phy1-ap0"}[$__rate_interval])',
                "5 GHz TX (phy1-ap0)", "D"),
        ],
        x=0, y=y, w=24, h=8, unit="Bps",
        desc="Traffic on the WiFi AP interfaces. 2.4 GHz (phy0-ap0) and 5 GHz (phy1-ap0)."))
    y += 8

    # ── Device tables ─────────────────────────────────────────────────────────
    panels.append(row_panel(20, "Device Details", y))
    y += 1

    panels.append(table(21, "All Devices — Online Status",
        targets=[tgt(
            'router_device_up{job="openwrt"}',
            "", "A", fmt="table", instant=True,
        )],
        x=0, y=y, w=24, h=12,
        desc="All known devices with their current online/offline status, MAC address, and IP",
        transforms=[
            {"id": "organize", "options": {
                "excludeByName": {"Time": True, "__name__": True, "job": True},
                "renameByName": {
                    "device": "Hostname",
                    "status": "Status",
                    "mac": "MAC Address",
                    "ip": "IP Address",
                    "Value": "Online",
                },
            }},
        ],
        sort_col="Online", sort_desc=True,
        overrides=[
            {"matcher": {"id": "byName", "options": "Online"}, "properties": [
                {"id": "custom.cellOptions", "value": {"type": "color-background"}},
                {"id": "mappings", "value": [
                    {"type": "value", "options": {"1": {"color": "#1a9e3a", "text": "Online", "index": 0}}},
                    {"type": "value", "options": {"0": {"color": "#808080", "text": "Offline", "index": 1}}},
                ]},
            ]},
            {"matcher": {"id": "byName", "options": "Status"}, "properties": [
                {"id": "custom.cellOptions", "value": {"type": "color-background"}},
                {"id": "mappings", "value": [
                    {"type": "value", "options": {"online":  {"color": "#1a9e3a", "text": "online",  "index": 0}}},
                    {"type": "value", "options": {"offline": {"color": "#808080", "text": "offline", "index": 1}}},
                ]},
            ]},
        ]))
    y += 12

    panels.append(table(22, "DHCP Lease Expiry",
        targets=[tgt(
            'dhcp_lease{job="openwrt"}',
            "", "A", fmt="table", instant=True,
        )],
        x=0, y=y, w=12, h=10,
        desc="Current DHCP leases. Value is Unix timestamp of lease expiry. Filter by hostname to find a device.",
        transforms=[
            {"id": "organize", "options": {
                "excludeByName": {"Time": True, "__name__": True, "job": True, "dnsmasq": True},
                "renameByName": {
                    "hostname": "Hostname",
                    "mac": "MAC Address",
                    "ip": "IP Address",
                    "Value": "Lease Expires (unix)",
                },
            }},
        ]))

    panels.append(table(23, "Static DHCP Reservations",
        targets=[tgt(
            'uci_dhcp_host{job="openwrt"}',
            "", "A", fmt="table", instant=True,
        )],
        x=12, y=y, w=12, h=10,
        desc="Static DHCP host reservations from UCI. Helpful for mapping device names to fixed IPs.",
        transforms=[
            {"id": "organize", "options": {
                "excludeByName": {"Time": True, "__name__": True, "job": True, "Value": True},
                "renameByName": {
                    "name": "Hostname",
                    "mac": "MAC Address",
                    "ip": "Reserved IP",
                    "dns": "Register DNS",
                },
            }},
        ]))

    return make_dashboard(
        uid="openwrt-devices",
        title="OpenWRT — Devices",
        description="LAN device tracking via ping-based presence, NAT traffic per device, DHCP leases, static reservations, and WiFi AP throughput.",
        panels=panels,
        tags=["openwrt", "devices"],
    )

# ═══════════════════════════════════════════════════════════════════════════════
# DASHBOARD 4 — LOGS
# Loki-based, no Prometheus queries — kept as separate Loki datasource panels
# ═══════════════════════════════════════════════════════════════════════════════

LOKI_DS = {"type": "loki", "uid": "${DS_LOKI}"}

def loki_tgt(expr, ref="A"):
    return {
        "datasource": copy.deepcopy(LOKI_DS),
        "expr": expr,
        "refId": ref,
        "queryType": "range",
    }

def loki_stat(id, title, expr, x, y, w, h, desc="", thresholds=None):
    steps = thresholds or [{"color": "blue", "value": 0}]
    return {
        "type": "stat", "id": id, "title": title, "description": desc,
        "datasource": copy.deepcopy(LOKI_DS),
        "targets": [loki_tgt(expr)],
        "fieldConfig": {
            "defaults": {
                "color": {"mode": "thresholds"},
                "mappings": [],
                "thresholds": {"mode": "absolute", "steps": steps},
                "unit": "short",
            },
            "overrides": [],
        },
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "options": {
            "colorMode": "background",
            "graphMode": "none",
            "justifyMode": "auto",
            "orientation": "auto",
            "reduceOptions": {"calcs": ["sum"], "fields": "", "values": False},
            "textMode": "auto",
        },
        "pluginVersion": "12.4.0",
    }

def loki_ts(id, title, expr, x, y, w, h, desc="", legend=""):
    return {
        "type": "timeseries", "id": id, "title": title, "description": desc,
        "datasource": copy.deepcopy(LOKI_DS),
        "targets": [loki_tgt(expr)],
        "fieldConfig": {
            "defaults": {
                "color": {"mode": "palette-classic"},
                "custom": {
                    "drawStyle": "bars",
                    "lineWidth": 1,
                    "fillOpacity": 80,
                    "gradientMode": "none",
                    "showPoints": "never",
                    "spanNulls": False,
                    "stacking": {"group": "A", "mode": "none"},
                },
                "unit": "short",
            },
            "overrides": [],
        },
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "options": {
            "legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
            "tooltip": {"mode": "multi", "sort": "none"},
        },
        "pluginVersion": "12.4.0",
    }

def loki_logs(id, title, expr, x, y, w, h, desc=""):
    return {
        "type": "logs", "id": id, "title": title, "description": desc,
        "datasource": copy.deepcopy(LOKI_DS),
        "targets": [loki_tgt(expr)],
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "options": {
            "dedupStrategy": "none",
            "enableLogDetails": True,
            "prettifyLogMessage": False,
            "showCommonLabels": False,
            "showLabels": False,
            "showTime": True,
            "sortOrder": "Descending",
            "wrapLogMessage": True,
        },
        "pluginVersion": "12.4.0",
    }

def build_logs():
    panels = []
    y = 0

    LOKI_TEMPLATING = {"list": [
        {
            "name": "DS_LOKI", "type": "datasource", "query": "loki",
            "refresh": 1, "includeAll": False, "options": [], "regex": "",
            "current": {"text": "Loki", "value": "loki"},
            "hide": 0, "label": "Loki",
        },
    ]}

    # Stats row
    panels.append(loki_stat(1, "Errors (1h)",
        'count_over_time({job="openwrt-syslog"} |~ "(?i)error|err" [1h])',
        x=0, y=y, w=4, h=4, desc="Log lines containing 'error' in the last hour",
        thresholds=[{"color": "green", "value": 0}, {"color": "yellow", "value": 1}, {"color": "red", "value": 10}]))

    panels.append(loki_stat(2, "Warnings (1h)",
        'count_over_time({job="openwrt-syslog"} |~ "(?i)warn" [1h])',
        x=4, y=y, w=4, h=4, desc="Log lines containing 'warn' in the last hour",
        thresholds=[{"color": "green", "value": 0}, {"color": "yellow", "value": 1}, {"color": "orange", "value": 20}]))

    panels.append(loki_stat(3, "DHCP Events (1h)",
        'count_over_time({job="openwrt-syslog"} |= "DHCP" [1h])',
        x=8, y=y, w=4, h=4, desc="DHCP-related log events in the last hour",
        thresholds=[{"color": "blue", "value": 0}]))

    panels.append(loki_stat(4, "Firewall Drops (1h)",
        'count_over_time({job="openwrt-syslog"} |~ "DROP|REJECT" [1h])',
        x=12, y=y, w=4, h=4, desc="Firewall drop/reject events in the last hour",
        thresholds=[{"color": "green", "value": 0}, {"color": "yellow", "value": 10}, {"color": "red", "value": 100}]))

    panels.append(loki_stat(5, "Total Log Lines (1h)",
        'count_over_time({job="openwrt-syslog"} [1h])',
        x=16, y=y, w=4, h=4, desc="Total log lines received from the router in the last hour",
        thresholds=[{"color": "gray", "value": 0}]))

    panels.append(loki_stat(6, "Kernel Messages (1h)",
        'count_over_time({job="openwrt-syslog"} |= "kernel" [1h])',
        x=20, y=y, w=4, h=4, desc="Kernel log messages (interface changes, OOM, driver events)",
        thresholds=[{"color": "blue", "value": 0}]))
    y += 4

    # Log rate over time
    panels.append(loki_ts(7, "Log Rate by Severity",
        'sum by(message_severity) (rate({job="openwrt-syslog"}[$__rate_interval]))',
        x=0, y=y, w=12, h=7, desc="Rate of log lines over time, grouped by syslog severity."))

    panels.append(loki_ts(13, "Log Rate by App",
        'sum by(message_app_name) (rate({job="openwrt-syslog"}[$__rate_interval]))',
        x=12, y=y, w=12, h=7, desc="Which daemons are emitting the most logs over time. Requires the syslog app-name label to be preserved."))
    y += 7

    # All logs
    panels.append(loki_logs(8, "All System Logs",
        '{job="openwrt-syslog"}',
        x=0, y=y, w=24, h=14,
        desc="Full log stream from OpenWRT's logd. Use the search bar to filter by keyword."))
    y += 14

    # Specialized log panels
    panels.append(loki_logs(9, "DHCP Events",
        '{job="openwrt-syslog"} |= "DHCP"',
        x=0, y=y, w=12, h=10,
        desc="DHCP lease assignments, renewals, and releases. Shows which devices got IPs and when."))

    panels.append(loki_logs(10, "Firewall Events (DROP / REJECT)",
        '{job="openwrt-syslog"} |~ "DROP|REJECT"',
        x=12, y=y, w=12, h=10,
        desc="Firewall blocked connections. Enable firewall logging with 'option log 1' in /etc/config/firewall."))
    y += 10

    panels.append(loki_logs(11, "Kernel Messages",
        '{job="openwrt-syslog"} |= "kernel"',
        x=0, y=y, w=12, h=10,
        desc="Kernel events: network interface state changes, driver errors, OOM events."))

    panels.append(loki_logs(12, "Error & Warning Events",
        '{job="openwrt-syslog"} |~ "(?i)error|warn|fail|critical"',
        x=12, y=y, w=12, h=10,
        desc="All log lines containing error, warning, fail, or critical keywords."))

    # Build manually since logs uses Loki datasource (different template var)
    for p in panels:
        gp = p.get("gridPos", {})
        assert gp.get("x", 0) + gp.get("w", 0) <= 24, \
            f"Panel {p['id']} [{p['title']}] overflows grid"

    return {
        "title": "OpenWRT — Logs",
        "uid": "openwrt-logs",
        "description": "System logs from OpenWRT's logd via remote syslog → Loki: DHCP events, firewall drops, kernel messages.",
        "tags": ["openwrt", "logs"],
        "schemaVersion": 42,
        "version": 1,
        "refresh": "30s",
        "timezone": "browser",
        "graphTooltip": 1,
        "time": {"from": "now-3h", "to": "now"},
        "timepicker": {},
        "weekStart": "",
        "fiscalYearStartMonth": 0,
        "preload": False,
        "editable": True,
        "annotations": ANNOTATIONS,
        "links": [
            {"title": "Overview", "url": "/d/openwrt-overview", "type": "link", "icon": "external link"},
            {"title": "Network",  "url": "/d/openwrt-network",  "type": "link", "icon": "external link"},
            {"title": "Devices",  "url": "/d/openwrt-devices",  "type": "link", "icon": "external link"},
        ],
        "panels": panels,
        "templating": LOKI_TEMPLATING,
    }

# ═══════════════════════════════════════════════════════════════════════════════
# BUILD ALL DASHBOARDS
# ═══════════════════════════════════════════════════════════════════════════════

OUTDIR = "grafana/provisioning/dashboards"

dashboards = [
    ("openwrt-overview.json", build_overview()),
    ("openwrt-network.json",  build_network()),
    ("openwrt-devices.json",  build_devices()),
    ("openwrt-logs.json",     build_logs()),
]

for filename, dash in dashboards:
    path = f"{OUTDIR}/{filename}"
    with open(path, "w") as f:
        json.dump(dash, f, indent=2)
    size_kb = len(json.dumps(dash)) // 1024
    print(f"  {filename}: {len(dash['panels'])} panels, {size_kb}KB")

print(f"\nBuilt {len(dashboards)} dashboards into {OUTDIR}/")
