"""
OpenWRT Grafana Monitor — Dashboard Builder
Generated for prometheus-node-exporter-lua plus this repo's textfile metrics.

Default dashboard variables:
  router:              openwrt
  WAN interface:       wan
  WiFi 2.4 GHz AP:    phy0-ap0
  WiFi 5 GHz AP:      phy1-ap0
  VPN interface:       tailscale0
  LAN bridge:         br-lan
  Router model:       ASUS RT-AX53U (MediaTek MT7621)

Key metrics confirmed live:
  node_cpu_seconds_total{cpu, mode}
  node_memory_*_bytes
  node_load1/5/15
  node_nf_conntrack_entries / node_nf_conntrack_entries_limit
  node_network_*_total{device}
  router_device_up{device, status, mac, ip}
  dhcp_lease{mac, hostname, ip}
  packet_loss{target}
  dns_probe_success{host}
  dns_probe_duration_seconds{host}
  wan_info{wanip, publicip, hostname}
  node_openwrt_info{board_name, model, release, ...}
  node_uname_info{release, machine, nodename}
  node_boot_time_seconds / node_time_seconds
  node_filefd_allocated / node_filefd_maximum
  overlay_bytes_total / overlay_bytes_used
  gateway_packet_loss
  wan_public_ip_changed
  dhcpv6_lease_count
  sqm_backlog_bytes / sqm_dropped_packets_total / sqm_overlimits_total
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
    {
        "name": "router", "type": "custom", "query": "openwrt",
        "current": {"text": "openwrt", "value": "openwrt"},
        "hide": 0, "label": "Router",
    },
    {
        "name": "wan_interface", "type": "custom", "query": "wan",
        "current": {"text": "wan", "value": "wan"},
        "hide": 0, "label": "WAN interface",
    },
    {
        "name": "wifi24_interface", "type": "custom", "query": "phy0-ap0",
        "current": {"text": "phy0-ap0", "value": "phy0-ap0"},
        "hide": 0, "label": "2.4 GHz interface",
    },
    {
        "name": "wifi5_interface", "type": "custom", "query": "phy1-ap0",
        "current": {"text": "phy1-ap0", "value": "phy1-ap0"},
        "hide": 0, "label": "5 GHz interface",
    },
    {
        "name": "vpn_interface", "type": "custom", "query": "tailscale0",
        "current": {"text": "tailscale0", "value": "tailscale0"},
        "hide": 0, "label": "VPN interface",
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
        'node_time_seconds{job="openwrt", router="$router"} - node_boot_time_seconds{job="openwrt", router="$router"}',
        x=0, y=y, w=4, h=4, unit="s", desc="Time since last reboot",
        thresholds=[
            {"color": "red",    "value": 0},
            {"color": "yellow", "value": 3600},
            {"color": "green",  "value": 86400},
        ]))

    # 2. Memory used %
    panels.append(stat(2, "Memory Used",
        '1 - (node_memory_MemAvailable_bytes{job="openwrt", router="$router"} / node_memory_MemTotal_bytes{job="openwrt", router="$router"})',
        x=4, y=y, w=4, h=4, unit="percentunit",
        desc="Percentage of RAM in use. Routers often run tight — >90% is a concern.",
        thresholds=[
            {"color": "green",  "value": 0},
            {"color": "yellow", "value": 0.7},
            {"color": "red",    "value": 0.9},
        ]))

    # 3. CPU busy %
    panels.append(stat(3, "CPU Busy",
        '1 - avg(rate(node_cpu_seconds_total{job="openwrt", router="$router", mode="idle"}[$__rate_interval]))',
        x=8, y=y, w=4, h=4, unit="percentunit",
        desc="Average CPU utilisation across all cores",
        thresholds=[
            {"color": "green",  "value": 0},
            {"color": "yellow", "value": 0.5},
            {"color": "red",    "value": 0.8},
        ]))

    # 4. Online devices
    panels.append(stat(4, "Online Devices",
        'count(router_device_up{job="openwrt", router="$router"} == 1)',
        x=12, y=y, w=4, h=4, unit="short",
        desc="Number of devices currently seen as online by the router",
        thresholds=[{"color": "blue", "value": 0}]))

    # 5. NAT sessions
    panels.append(stat(5, "NAT Sessions",
        'node_nf_conntrack_entries{job="openwrt", router="$router"}',
        x=16, y=y, w=4, h=4, unit="short",
        desc="Active NAT/conntrack sessions. High values may indicate port scanning or excessive connections.",
        thresholds=[
            {"color": "green",  "value": 0},
            {"color": "yellow", "value": 5000},
            {"color": "red",    "value": 15000},
        ]))

    # 6. Packet loss
    panels.append(stat(6, "Packet Loss",
        'packet_loss{job="openwrt", router="$router"}',
        x=20, y=y, w=4, h=4, unit="percent",
        desc="Packet loss percentage on WAN connection. 0 = perfect, >5% = degraded.",
        thresholds=[
            {"color": "green",  "value": 0},
            {"color": "yellow", "value": 1},
            {"color": "red",    "value": 5},
        ]))
    y += 4

    # ── Tier 2: WAN throughput + CPU load ────────────────────────────────────
    panels.append(ts(7, "WAN Throughput",
        targets=[
            tgt('rate(node_network_receive_bytes_total{job="openwrt", router="$router", device="$wan_interface"}[$__rate_interval])',
                "Download (RX)", "A"),
            tgt('rate(node_network_transmit_bytes_total{job="openwrt", router="$router", device="$wan_interface"}[$__rate_interval])',
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
            tgt('node_load1{job="openwrt", router="$router"}',  "1 min",  "A"),
            tgt('node_load5{job="openwrt", router="$router"}',  "5 min",  "B"),
            tgt('node_load15{job="openwrt", router="$router"}', "15 min", "C"),
        ],
        x=12, y=y, w=12, h=9, unit="short",
        desc="System load average. On this 4-thread MT7621 router, values >4 indicate sustained overload.",
        calcs=["lastNotNull", "max"]))
    y += 9

    # ── Tier 2: Memory + NAT sessions ────────────────────────────────────────
    panels.append(ts(9, "Memory Usage",
        targets=[
            tgt('node_memory_MemTotal_bytes{job="openwrt", router="$router"} - node_memory_MemAvailable_bytes{job="openwrt", router="$router"}',
                "Used", "A"),
            tgt('node_memory_MemAvailable_bytes{job="openwrt", router="$router"}',
                "Available", "B"),
            tgt('node_memory_Buffers_bytes{job="openwrt", router="$router"} + node_memory_Cached_bytes{job="openwrt", router="$router"}',
                "Buffers+Cache", "C"),
        ],
        x=0, y=y, w=12, h=8, unit="bytes", stacked=True, fill=40,
        desc="RAM breakdown. 'Used' is actively allocated. 'Buffers+Cache' can be reclaimed."))

    panels.append(ts(10, "NAT Conntrack Sessions",
        targets=[
            tgt('node_nf_conntrack_entries{job="openwrt", router="$router"}',
                "Active sessions", "A"),
            tgt('node_nf_conntrack_entries_limit{job="openwrt", router="$router"}',
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
            tgt('sum(rate(node_cpu_seconds_total{job="openwrt", router="$router", mode="user"}[$__rate_interval]))',
                "user", "A"),
            tgt('sum(rate(node_cpu_seconds_total{job="openwrt", router="$router", mode="system"}[$__rate_interval]))',
                "system", "B"),
            tgt('sum(rate(node_cpu_seconds_total{job="openwrt", router="$router", mode="softirq"}[$__rate_interval]))',
                "softirq", "C"),
            tgt('sum(rate(node_cpu_seconds_total{job="openwrt", router="$router", mode="iowait"}[$__rate_interval]))',
                "iowait", "D"),
        ],
        x=0, y=y, w=24, h=8, unit="short", stacked=True, fill=60,
        desc="CPU time breakdown across all 4 threads. softirq = network packet processing (normal on a router)."))
    y += 8

    # ── Tier 3: System health ────────────────────────────────────────────────
    panels.append(row_panel(12, "System Health", y))
    y += 1

    panels.append(stat(13, "CPU Temperature",
        'max(node_thermal_zone_temp{job="openwrt", router="$router"}) or max(node_hwmon_temp_celsius{job="openwrt", router="$router"})',
        x=0, y=y, w=8, h=5, unit="celsius",
        desc="Firmware-reported temperature from thermal or hwmon collector. Optional collector availability is device-dependent.",
        thresholds=[
            {"color": "green", "value": 0},
            {"color": "yellow", "value": 60},
            {"color": "red", "value": 75},
        ],
        graph=True))

    panels.append(bargauge(14, "Overlay Flash Usage",
        targets=[tgt(
            'overlay_bytes_used{job="openwrt", router="$router"} / overlay_bytes_total{job="openwrt", router="$router"}',
            "Overlay used", "A",
        )],
        x=8, y=y, w=8, h=5, unit="percentunit", max=1,
        desc="Writable overlay/rootfs_data usage. A full overlay can break package installs and config writes.",
        thresholds=[
            {"color": "green", "value": 0},
            {"color": "yellow", "value": 0.7},
            {"color": "red", "value": 0.9},
        ]))

    panels.append(stat(15, "DHCPv6 Leases",
        'dhcpv6_lease_count{job="openwrt", router="$router"}',
        x=12, y=y, w=6, h=5, unit="short",
        desc="Active DHCPv6/RA leases known to odhcpd, when odhcpd is in use.",
        thresholds=[{"color": "blue", "value": 0}]))

    panels.append(stat(16, "Reboots in Range",
        'changes(node_boot_time_seconds{job="openwrt", router="$router"}[$__range])',
        x=18, y=y, w=6, h=5, unit="short",
        desc="Number of boot-time changes in the selected dashboard range.",
        thresholds=[
            {"color": "green", "value": 0},
            {"color": "yellow", "value": 1},
        ]))
    y += 5

    # ── Tier 4: Router info table ─────────────────────────────────────────────
    panels.append(row_panel(20, "Router Info", y))
    y += 1

    panels.append(table(21, "OpenWRT Firmware Info",
        targets=[tgt(
            'node_openwrt_info{job="openwrt", router="$router"}',
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
            'wan_info{job="openwrt", router="$router"}',
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

    panels.append(stat(23, "Open File Descriptors",
        'node_filefd_allocated{job="openwrt", router="$router"} / node_filefd_maximum{job="openwrt", router="$router"}',
        x=12, y=y, w=6, h=4, unit="percentunit",
        desc="File descriptor usage. High values indicate too many open connections/files.",
        thresholds=[
            {"color": "green", "value": 0},
            {"color": "yellow", "value": 0.7},
            {"color": "red", "value": 0.9},
        ]))

    panels.append(stat(24, "DHCP Leases Active",
        'count(dhcp_lease{job="openwrt", router="$router"})',
        x=18, y=y, w=6, h=4, unit="short",
        desc="Number of active DHCP leases (devices with an IP from the router)",
        thresholds=[{"color": "blue", "value": 0}]))

    return make_dashboard(
        uid="openwrt-overview",
        title="OpenWRT — Overview",
        description="ASUS RT-AX53U system health: CPU, memory, WAN throughput, NAT sessions, and device counts.",
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
            'rate(node_network_receive_bytes_total{job="openwrt", router="$router", device="$wan_interface"}[$__rate_interval])',
            "WAN Download", "A")],
        x=0, y=y, w=12, h=8, unit="Bps",
        desc="Bytes per second received on the WAN interface (internet download)",
        overrides=[{"matcher": {"id": "byName", "options": "WAN Download"},
                    "properties": [{"id": "color", "value": {"fixedColor": "#1a9e3a", "mode": "fixed"}}]}]))

    panels.append(ts(3, "WAN Upload (TX)",
        targets=[tgt(
            'rate(node_network_transmit_bytes_total{job="openwrt", router="$router", device="$wan_interface"}[$__rate_interval])',
            "WAN Upload", "A")],
        x=12, y=y, w=12, h=8, unit="Bps",
        desc="Bytes per second transmitted on the WAN interface (internet upload)",
        overrides=[{"matcher": {"id": "byName", "options": "WAN Upload"},
                    "properties": [{"id": "color", "value": {"fixedColor": "#5794F2", "mode": "fixed"}}]}]))
    y += 8

    panels.append(ts(4, "WAN Packets/sec",
        targets=[
            tgt('rate(node_network_receive_packets_total{job="openwrt", router="$router", device="$wan_interface"}[$__rate_interval])',
                "RX packets", "A"),
            tgt('rate(node_network_transmit_packets_total{job="openwrt", router="$router", device="$wan_interface"}[$__rate_interval])',
                "TX packets", "B"),
        ],
        x=0, y=y, w=12, h=7, unit="pps",
        desc="Packets per second on WAN. High PPS with low bytes = many small packets (DNS, keepalives)."))

    panels.append(ts(5, "WAN Errors & Drops",
        targets=[
            tgt('rate(node_network_receive_errs_total{job="openwrt", router="$router", device="$wan_interface"}[$__rate_interval])',
                "RX errors", "A"),
            tgt('rate(node_network_receive_drop_total{job="openwrt", router="$router", device="$wan_interface"}[$__rate_interval])',
                "RX drops", "B"),
            tgt('rate(node_network_transmit_errs_total{job="openwrt", router="$router", device="$wan_interface"}[$__rate_interval])',
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

    # ── WAN health and firewall counters ─────────────────────────────────────
    panels.append(row_panel(6, "WAN Health & Firewall Security", y))
    y += 1

    panels.append(stat(7, "Gateway Packet Loss",
        'gateway_packet_loss{job="openwrt", router="$router"}',
        x=0, y=y, w=6, h=6, unit="percent",
        desc="Packet loss percentage to the IPv4 default gateway. This separates LAN-side gateway reachability from internet probe loss.",
        thresholds=[
            {"color": "green", "value": 0},
            {"color": "yellow", "value": 1},
            {"color": "red", "value": 5},
        ],
        graph=True))

    panels.append(stat(8, "Public IP Changed",
        'wan_public_ip_changed{job="openwrt", router="$router"}',
        x=6, y=y, w=6, h=6, unit="short",
        desc="1 when the public IP changed on the latest throttled lookup, otherwise 0.",
        thresholds=[
            {"color": "green", "value": 0},
            {"color": "yellow", "value": 1},
        ]))

    panels.append(stat(50, "DNS Probe",
        'dns_probe_success{job="openwrt", router="$router"}',
        x=12, y=y, w=6, h=6, unit="short",
        desc="DNS resolution probe for the configured host. 1 = successful, 0 = failed.",
        thresholds=[
            {"color": "red", "value": 0},
            {"color": "green", "value": 1},
        ],
        graph=True))

    panels.append(ts(9, "Named nftables Counter Rate",
        targets=[tgt(
            'sum by(name) (rate(nft_counter_packets{job="openwrt", router="$router"}[$__rate_interval]))',
            "{{name}}", "A",
        )],
        x=18, y=y, w=6, h=6, unit="pps",
        desc="Packet rate from explicitly named nftables counters. Add only bounded counters such as WAN reject/drop rules."))
    y += 6

    # ── Multi-WAN section ────────────────────────────────────────────────────
    panels.append(row_panel(60, "Multi-WAN (mwan3)", y))
    y += 1

    panels.append(table(61, "mwan3 Interface Status",
        targets=[
            tgt('mwan3_interface_status{job="openwrt", router="$router"}', "", "A", fmt="table", instant=True),
            tgt('mwan3_interface_score{job="openwrt", router="$router"}', "", "B", fmt="table", instant=True),
            tgt('mwan3_interface_uptime{job="openwrt", router="$router"}', "", "C", fmt="table", instant=True),
            tgt('mwan3_interface_lost{job="openwrt", router="$router"}', "", "D", fmt="table", instant=True),
        ],
        x=0, y=y, w=10, h=7,
        desc="Optional mwan3 collector metrics. This table is empty when mwan3 is not installed.",
        transforms=[
            {"id": "organize", "options": {
                "excludeByName": {"Time": True, "__name__": True, "job": True, "router": True},
                "renameByName": {"interface": "Interface", "status": "Status", "Value": "Value"},
            }},
        ]))

    panels.append(bargauge(62, "mwan3 Interfaces Up",
        targets=[tgt(
            'mwan3_interface_up{job="openwrt", router="$router"}',
            "{{interface}}", "A",
        )],
        x=10, y=y, w=6, h=7, unit="short", max=1,
        desc="Interface up status from the optional mwan3 collector.",
        thresholds=[
            {"color": "red", "value": 0},
            {"color": "green", "value": 1},
        ]))

    panels.append(ts(63, "mwan3 Interface Score",
        targets=[tgt(
            'mwan3_interface_score{job="openwrt", router="$router"}',
            "{{interface}}", "A",
        )],
        x=16, y=y, w=8, h=7, unit="short",
        desc="mwan3 interface score over time. Empty when mwan3 is absent."))
    y += 7

    # ── SQM/cake section ─────────────────────────────────────────────────────
    panels.append(row_panel(80, "SQM / Cake", y))
    y += 1

    panels.append(ts(81, "SQM Backlog",
        targets=[tgt(
            'sqm_backlog_bytes{job="openwrt", router="$router"}',
            "{{iface}} {{direction}}", "A",
        )],
        x=0, y=y, w=8, h=7, unit="bytes",
        desc="Optional disabled-by-default SQM/cake textfile collector backlog. Empty until ENABLE_SQM_METRICS=1 is configured."))

    panels.append(ts(82, "SQM Drops",
        targets=[tgt(
            'rate(sqm_dropped_packets_total{job="openwrt", router="$router"}[$__rate_interval])',
            "{{iface}} {{direction}}", "A",
        )],
        x=8, y=y, w=8, h=7, unit="pps",
        desc="SQM/cake dropped packet rate for configured egress and IFB ingress interfaces."))

    panels.append(ts(83, "SQM Overlimits",
        targets=[tgt(
            'rate(sqm_overlimits_total{job="openwrt", router="$router"}[$__rate_interval])',
            "{{iface}} {{direction}}", "A",
        )],
        x=16, y=y, w=8, h=7, unit="pps",
        desc="SQM/cake overlimit rate for configured interfaces."))
    y += 7

    # ── WiFi section ─────────────────────────────────────────────────────────
    panels.append(row_panel(10, "WiFi Access Points", y))
    y += 1

    panels.append(ts(11, "WiFi AP Throughput — Both Bands",
        targets=[
            tgt('rate(node_network_receive_bytes_total{job="openwrt", router="$router", device="$wifi24_interface"}[$__rate_interval])',
                "2.4 GHz RX", "A"),
            tgt('rate(node_network_transmit_bytes_total{job="openwrt", router="$router", device="$wifi24_interface"}[$__rate_interval])',
                "2.4 GHz TX", "B"),
            tgt('rate(node_network_receive_bytes_total{job="openwrt", router="$router", device="$wifi5_interface"}[$__rate_interval])',
                "5 GHz RX", "C"),
            tgt('rate(node_network_transmit_bytes_total{job="openwrt", router="$router", device="$wifi5_interface"}[$__rate_interval])',
                "5 GHz TX", "D"),
        ],
        x=0, y=y, w=24, h=8, unit="Bps",
        desc="Traffic on the configured 2.4 GHz and 5 GHz WiFi access point interfaces"))
    y += 8

    # ── LAN section ──────────────────────────────────────────────────────────
    panels.append(row_panel(20, "LAN & Internal Interfaces", y))
    y += 1

    panels.append(ts(21, "All Interface Throughput (RX)",
        targets=[tgt(
            'rate(node_network_receive_bytes_total{job="openwrt", router="$router", device!~"lo|lan2|lan3"}[$__rate_interval])',
            "{{device}}", "A")],
        x=0, y=y, w=12, h=8, unit="Bps",
        desc="RX throughput per interface. Excludes loopback and unused LAN ports (lan2, lan3)."))

    panels.append(ts(22, "All Interface Throughput (TX)",
        targets=[tgt(
            'rate(node_network_transmit_bytes_total{job="openwrt", router="$router", device!~"lo|lan2|lan3"}[$__rate_interval])',
            "{{device}}", "A")],
        x=12, y=y, w=12, h=8, unit="Bps",
        desc="TX throughput per interface. Excludes loopback and unused LAN ports."))
    y += 8

    # ── IPv6 section ─────────────────────────────────────────────────────────
    panels.append(row_panel(70, "IPv6 (snmp6)", y))
    y += 1

    panels.append(ts(71, "IPv6 Packets",
        targets=[
            tgt('rate(snmp6_Ip6InReceives{job="openwrt", router="$router"}[$__rate_interval])',
                "In receives {{device}}", "A"),
            tgt('rate(snmp6_Ip6OutRequests{job="openwrt", router="$router"}[$__rate_interval])',
                "Out requests {{device}}", "B"),
        ],
        x=0, y=y, w=12, h=7, unit="pps",
        desc="Curated IPv6 packet counters from the optional snmp6 collector. Empty when snmp6 is unavailable."))

    panels.append(ts(72, "IPv6 Discards",
        targets=[
            tgt('rate(snmp6_Ip6InDiscards{job="openwrt", router="$router"}[$__rate_interval])',
                "In discards {{device}}", "A"),
            tgt('rate(snmp6_Ip6OutDiscards{job="openwrt", router="$router"}[$__rate_interval])',
                "Out discards {{device}}", "B"),
        ],
        x=12, y=y, w=12, h=7, unit="pps",
        desc="Optional IPv6 discard counters. Non-zero sustained rates can indicate path or forwarding issues."))
    y += 7

    # ── Tailscale section ─────────────────────────────────────────────────────
    panels.append(row_panel(30, "VPN Interface", y))
    y += 1

    panels.append(ts(31, "Tailscale VPN Throughput",
        targets=[
            tgt('rate(node_network_receive_bytes_total{job="openwrt", router="$router", device="$vpn_interface"}[$__rate_interval])',
                "VPN RX", "A"),
            tgt('rate(node_network_transmit_bytes_total{job="openwrt", router="$router", device="$vpn_interface"}[$__rate_interval])',
                "VPN TX", "B"),
        ],
        x=0, y=y, w=12, h=7, unit="Bps",
        desc="Traffic flowing through the Tailscale VPN tunnel"))

    panels.append(ts(32, "NAT Conntrack Sessions",
        targets=[
            tgt('node_nf_conntrack_entries{job="openwrt", router="$router"}',         "Active sessions", "A"),
            tgt('node_nf_conntrack_entries_limit{job="openwrt", router="$router"}',   "Limit",           "B"),
        ],
        x=12, y=y, w=12, h=7, unit="short",
        desc="NAT connection tracking table usage vs limit. Approaching the limit causes new connections to fail."))
    y += 7

    # ── Interface summary table ────────────────────────────────────────────────
    panels.append(row_panel(40, "Interface Summary", y))
    y += 1

    panels.append(table(41, "Network Interface Status",
        targets=[
            tgt('node_network_info{job="openwrt", router="$router"}', "", "A", fmt="table", instant=True),
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
        description="WAN throughput, WiFi AP traffic, Tailscale VPN, LAN interfaces, and interface status.",
        panels=panels,
        tags=["openwrt", "network"],
    )

# ═══════════════════════════════════════════════════════════════════════════════
# DASHBOARD 3 — DEVICES (replaces WiFi dashboard)
# Uses router_device_up and dhcp_lease since wifi_station_* yields no data
# ═══════════════════════════════════════════════════════════════════════════════

def build_devices():
    panels = []
    y = 0

    # ── Stats row ────────────────────────────────────────────────────────────
    panels.append(stat(1, "Online Devices",
        'count(router_device_up{job="openwrt", router="$router"} == 1)',
        x=0, y=y, w=6, h=4, unit="short",
        desc="Devices currently seen as online by the router",
        thresholds=[{"color": "blue", "value": 0}]))

    panels.append(stat(2, "Offline Devices",
        'count(router_device_up{job="openwrt", router="$router"} == 0)',
        x=6, y=y, w=6, h=4, unit="short",
        desc="Devices with known leases that are currently offline",
        thresholds=[
            {"color": "green", "value": 0},
            {"color": "yellow", "value": 5},
        ]))

    panels.append(stat(3, "DHCP Leases",
        'count(dhcp_lease{job="openwrt", router="$router"})',
        x=12, y=y, w=6, h=4, unit="short",
        desc="Total DHCP leases (active + recently expired)",
        thresholds=[{"color": "blue", "value": 0}]))

    panels.append(stat(4, "Packet Loss %",
        'packet_loss{job="openwrt", router="$router"}',
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
            tgt('count(router_device_up{job="openwrt", router="$router"} == 1)', "Online",  "A"),
            tgt('count(router_device_up{job="openwrt", router="$router"} == 0)', "Offline", "B"),
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
            'topk(10, sum by(src)(node_nat_traffic{job="openwrt", router="$router"}))',
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
            tgt('rate(node_network_receive_bytes_total{job="openwrt", router="$router", device="$wifi24_interface"}[$__rate_interval])',
                "2.4 GHz RX", "A"),
            tgt('rate(node_network_transmit_bytes_total{job="openwrt", router="$router", device="$wifi24_interface"}[$__rate_interval])',
                "2.4 GHz TX", "B"),
            tgt('rate(node_network_receive_bytes_total{job="openwrt", router="$router", device="$wifi5_interface"}[$__rate_interval])',
                "5 GHz RX", "C"),
            tgt('rate(node_network_transmit_bytes_total{job="openwrt", router="$router", device="$wifi5_interface"}[$__rate_interval])',
                "5 GHz TX", "D"),
        ],
        x=0, y=y, w=24, h=8, unit="Bps",
        desc="Traffic on the configured WiFi AP interfaces."))
    y += 8

    # ── WiFi client quality ──────────────────────────────────────────────────
    panels.append(row_panel(12, "WiFi Clients", y))
    y += 1

    panels.append(ts(13, "WiFi Client Signal",
        targets=[tgt(
            'hostapd_station_signal_dbm{job="openwrt", router="$router"}',
            "{{station}} {{ssid}}", "A",
        )],
        x=0, y=y, w=12, h=8, unit="dBm",
        desc="Per-client signal reported by hostapd_stations. Client MAC addresses are exposed as labels.",
        calcs=["lastNotNull", "min"]))

    panels.append(ts(15, "WiFi Client RX/TX Rate",
        targets=[
            tgt('rate(hostapd_station_receive_bytes_total{job="openwrt", router="$router"}[$__rate_interval])',
                "RX {{station}} {{ssid}}", "A"),
            tgt('rate(hostapd_station_transmit_bytes_total{job="openwrt", router="$router"}[$__rate_interval])',
                "TX {{station}} {{ssid}}", "B"),
        ],
        x=12, y=y, w=12, h=8, unit="Bps",
        desc="Per-station traffic from hostapd_stations. The station label is a client MAC address."))
    y += 8

    panels.append(table(14, "Connected WiFi Stations",
        targets=[tgt(
            'hostapd_station_signal_dbm{job="openwrt", router="$router"}',
            "", "A", fmt="table", instant=True,
        )],
        x=0, y=y, w=8, h=8,
        desc="Currently connected WiFi stations from hostapd. Availability depends on the optional hostapd_stations collector.",
        transforms=[
            {"id": "organize", "options": {
                "excludeByName": {"Time": True, "__name__": True, "job": True, "router": True},
                "renameByName": {
                    "station": "Station",
                    "ssid": "SSID",
                    "vif": "Interface",
                    "bssid": "BSSID",
                    "frequency": "Frequency",
                    "channel": "Channel",
                    "Value": "Signal dBm",
                },
            }},
        ],
        sort_col="Signal dBm", sort_desc=True))

    panels.append(table(16, "WiFi Connected Duration",
        targets=[tgt(
            'hostapd_station_connected_seconds_total{job="openwrt", router="$router"}',
            "", "A", fmt="table", instant=True,
        )],
        x=8, y=y, w=8, h=8,
        desc="Connected duration by station from the optional hostapd_stations collector.",
        transforms=[
            {"id": "organize", "options": {
                "excludeByName": {"Time": True, "__name__": True, "job": True, "router": True},
                "renameByName": {
                    "station": "Station",
                    "ssid": "SSID",
                    "vif": "Interface",
                    "frequency": "Frequency",
                    "channel": "Channel",
                    "Value": "Connected Seconds",
                },
            }},
        ],
        sort_col="Connected Seconds", sort_desc=True))

    panels.append(table(17, "WiFi Inactive Seconds",
        targets=[tgt(
            'hostapd_station_inactive_seconds{job="openwrt", router="$router"}',
            "", "A", fmt="table", instant=True,
        )],
        x=16, y=y, w=8, h=8,
        desc="Inactive seconds by station. High values can indicate idle or poor-quality clients.",
        transforms=[
            {"id": "organize", "options": {
                "excludeByName": {"Time": True, "__name__": True, "job": True, "router": True},
                "renameByName": {
                    "station": "Station",
                    "ssid": "SSID",
                    "vif": "Interface",
                    "frequency": "Frequency",
                    "channel": "Channel",
                    "Value": "Inactive Seconds",
                },
            }},
        ],
        sort_col="Inactive Seconds", sort_desc=True))
    y += 8

    panels.append(bargauge(18, "WiFi Clients by Frequency",
        targets=[tgt(
            'count by(frequency, channel) (hostapd_station_signal_dbm{job="openwrt", router="$router"})',
            "{{frequency}} MHz ch {{channel}}", "A",
        )],
        x=0, y=y, w=24, h=5, unit="short",
        desc="Client counts grouped by hostapd frequency/channel labels when available."))
    y += 5

    # ── Device tables ─────────────────────────────────────────────────────────
    panels.append(row_panel(20, "Device Details", y))
    y += 1

    panels.append(table(21, "All Devices — Online Status",
        targets=[tgt(
            'router_device_up{job="openwrt", router="$router"}',
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
            'dhcp_lease{job="openwrt", router="$router"}',
            "", "A", fmt="table", instant=True,
        )],
        x=0, y=y, w=24, h=10,
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

    return make_dashboard(
        uid="openwrt-devices",
        title="OpenWRT — Devices",
        description="LAN device tracking via router_device_up: online/offline counts, NAT traffic per device, DHCP leases, WiFi AP throughput.",
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
    cron_cmd_filter = '!~ "^USER [^ ]+ pid [0-9]+ cmd "'

    LOKI_TEMPLATING = {"list": [
        {
            "name": "DS_LOKI", "type": "datasource", "query": "loki",
            "refresh": 1, "includeAll": False, "options": [], "regex": "",
            "current": {"text": "Loki", "value": "loki"},
            "hide": 0, "label": "Loki",
        },
        {
            "name": "router", "type": "custom", "query": "openwrt",
            "current": {"text": "openwrt", "value": "openwrt"},
            "hide": 0, "label": "Router",
        },
    ]}

    # Stats row
    panels.append(loki_stat(1, "Errors (1h)",
        f'count_over_time({{job="openwrt-syslog", router="$router"}} {cron_cmd_filter} |~ "(?i)error|err" [1h])',
        x=0, y=y, w=4, h=4, desc="Log lines containing 'error' in the last hour, excluding cron command-start noise",
        thresholds=[{"color": "green", "value": 0}, {"color": "yellow", "value": 1}, {"color": "red", "value": 10}]))

    panels.append(loki_stat(2, "Warnings (1h)",
        f'count_over_time({{job="openwrt-syslog", router="$router"}} {cron_cmd_filter} |~ "(?i)warn" [1h])',
        x=4, y=y, w=4, h=4, desc="Log lines containing 'warn' in the last hour, excluding cron command-start noise",
        thresholds=[{"color": "green", "value": 0}, {"color": "yellow", "value": 1}, {"color": "orange", "value": 20}]))

    panels.append(loki_stat(3, "DHCP Events (1h)",
        'count_over_time({job="openwrt-syslog", router="$router"} |= "DHCP" [1h])',
        x=8, y=y, w=4, h=4, desc="DHCP-related log events in the last hour",
        thresholds=[{"color": "blue", "value": 0}]))

    panels.append(loki_stat(4, "Firewall Drops (1h)",
        'count_over_time({job="openwrt-syslog", router="$router"} |~ "DROP|REJECT" [1h])',
        x=12, y=y, w=4, h=4, desc="Firewall drop/reject events in the last hour",
        thresholds=[{"color": "green", "value": 0}, {"color": "yellow", "value": 10}, {"color": "red", "value": 100}]))

    panels.append(loki_stat(5, "Total Log Lines (1h)",
        'count_over_time({job="openwrt-syslog", router="$router"} [1h])',
        x=16, y=y, w=4, h=4, desc="Total log lines received from the router in the last hour",
        thresholds=[{"color": "gray", "value": 0}]))

    panels.append(loki_stat(6, "Kernel Messages (1h)",
        'count_over_time({job="openwrt-syslog", router="$router"} |= "kernel" [1h])',
        x=20, y=y, w=4, h=4, desc="Kernel log messages (interface changes, OOM, driver events)",
        thresholds=[{"color": "blue", "value": 0}]))
    y += 4

    panels.append(loki_stat(13, "Failed SSH Logins (1h)",
        'count_over_time({job="openwrt-syslog", router="$router"} |~ "(?i)failed password|login failed" [1h])',
        x=0, y=y, w=6, h=4, desc="Failed SSH login attempts seen in router syslog",
        thresholds=[{"color": "green", "value": 0}, {"color": "yellow", "value": 1}, {"color": "red", "value": 10}]))
    y += 4

    # Log rate over time
    panels.append(loki_ts(7, "Log Rate by Syslog Severity",
        f'sum by(message_severity) (rate({{job="openwrt-syslog", router="$router"}} {cron_cmd_filter}[$__rate_interval]))',
        x=0, y=y, w=24, h=7, desc="Rate of non-cron log lines over time, grouped by syslog severity. BusyBox cron command-start records are intentionally excluded."))
    y += 7

    # All logs
    panels.append(loki_logs(8, "All System Logs",
        '{job="openwrt-syslog", router="$router"}',
        x=0, y=y, w=24, h=14,
        desc="Full log stream from OpenWRT's logd. Use the search bar to filter by keyword."))
    y += 14

    # Specialized log panels
    panels.append(loki_logs(9, "DHCP Events",
        '{job="openwrt-syslog", router="$router"} |= "DHCP"',
        x=0, y=y, w=12, h=10,
        desc="DHCP lease assignments, renewals, and releases. Shows which devices got IPs and when."))

    panels.append(loki_logs(10, "Firewall Events (DROP / REJECT)",
        '{job="openwrt-syslog", router="$router"} |~ "DROP|REJECT"',
        x=12, y=y, w=12, h=10,
        desc="Firewall blocked connections. Enable firewall logging with 'option log 1' in /etc/config/firewall."))
    y += 10

    panels.append(loki_logs(11, "Kernel Messages",
        '{job="openwrt-syslog", router="$router"} |= "kernel"',
        x=0, y=y, w=12, h=10,
        desc="Kernel events: network interface state changes, driver errors, OOM events."))

    panels.append(loki_logs(12, "Error & Warning Events",
        f'{{job="openwrt-syslog", router="$router"}} {cron_cmd_filter} |~ "(?i)error|warn|fail|critical"',
        x=12, y=y, w=12, h=10,
        desc="Log lines containing error, warning, fail, or critical keywords, excluding cron command-start records."))
    y += 10

    panels.append(loki_logs(14, "Failed SSH Login Events",
        '{job="openwrt-syslog", router="$router"} |~ "(?i)failed password|login failed"',
        x=0, y=y, w=24, h=8,
        desc="SSH authentication failures from dropbear or sshd."))

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
