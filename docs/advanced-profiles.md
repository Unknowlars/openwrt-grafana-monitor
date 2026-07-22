# Advanced Router Profiles

The default setup is `core`. It preserves the existing exporter and helper
surface. Optional profiles add focused collectors without making the base
install depend on nftables JSON, usteer, or Netifyd.

## Profiles

| Profile | Adds | Main availability metric |
|---|---|---|
| `core` | Existing official collectors and repo helper scripts | `up` and `node_scrape_collector_success` |
| `traffic` | Read-only nftables per-device byte and packet counters | `openwrt_device_traffic_collector_available` |
| `wifi_mesh` | Radio settings, 802.11r/k/v flags, and usteer local metrics | `openwrt_wifi_mesh_collector_available` |
| `dpi` | Bounded Netifyd application/protocol snapshot metrics | `openwrt_dpi_collector_available` |
| `clients` | Unified per-client identity plus nlbwmon MAC-keyed service traffic | `openwrt_client_inventory_collector_available`, `openwrt_client_traffic_collector_available` |
| `full` | All optional profiles | All optional-profile metrics above |

Enable a profile during router setup. `OPENWRT_MONITOR_PROFILE` also accepts a
comma-separated list of profile names, so `traffic,wifi_mesh` enables both
without pulling in everything `full` would:

```sh
OPENWRT_MONITOR_PROFILE=full \
TRAFFIC_LAN_INTERFACE=br-lan \
sh /tmp/openwrt/setup.sh 192.168.0.100
```

The setup script detects `opkg` on OpenWrt 24.10 and `apk` on OpenWrt 25.12.
It does not run `apk upgrade`; firmware upgrades remain sysupgrade or
attended-sysupgrade operations.

## Traffic collector

The traffic profile installs `/etc/nftables.d/openwrt-device-traffic.nft`.
It creates dynamic timeout sets in the `inet fw4` table and counts forwarded
LAN traffic in both directions. The rules have an accept policy and are
placed before the normal forwarding policy, so they observe traffic without
changing the firewall decision. Broadcast and multicast are excluded.

The Lua collector reads `nft -j list set` output and joins addresses to
`/tmp/dhcp.leases`. Device labels use the DHCP hostname first and a bounded
`ip_...` fallback. There is no MAC address in the metric identity, which
keeps series churn lower than a raw flow exporter.

The collector requires `lua-cjson` and the router's nftables JSON support. If
either is absent, it exports availability `0` and the dashboard renders the
profile as unavailable.

This is now the only source for "top traffic" panels on the Devices and
Operations dashboards. They previously ranked by `node_nat_traffic`, the
required `nat_traffic` collector's raw `src`/`dest` conntrack byte counts —
which has no cardinality bound (a full src × dest cross product) and cannot be
safely collapsed at the Alloy layer without silently under-reporting clients
with more than one active destination. `node_nat_traffic` is now dropped
before it reaches Prometheus (`alloy/config.alloy`); it is still exported by
the router (the required package is unchanged) but Grafana never sees it. The
"who did each client talk to" question this metric used to answer for
external IPs has no bounded replacement yet and is deferred to the
traffic-attribution phase in `docs/client-topology-and-netflow-plan.md`.

## WiFi mesh collector

The `wifi_mesh` profile adapts the low-cardinality parts of the
`openwrt-dethrash` signal model: radio channel, frequency, transmit power,
802.11r/k/v configuration, and usteer local load/roam/client values. It does
not enable station hearing maps or per-client MAC metrics by default. Those
can be large on a mesh and should be added only with an explicit retention and
cardinality decision.

The collector depends on `ubus`, `iwinfo`, and the optional usteer service. A
router without usteer still gets radio and wireless configuration metrics.

## DPI collector

The `dpi` profile reads Netifyd's local
`/var/run/netifyd/status.json` snapshot. It exports active flows, device count,
top application bytes and flows, and top protocol flows. Application and
protocol series are capped at 25 per scrape and labels are sanitized. Snapshot
values are gauges because they can decrease when flows expire.

This profile does not install a privileged Go exporter or expose the Netifyd
socket remotely. Install and configure Netifyd separately when DPI is wanted;
without a readable snapshot the availability metric is `0`.

## Client inventory collector

The `clients` profile installs `client_inventory.lua`, a single MAC-keyed
identity source that replaces the ad hoc joins the dashboards previously did
across `dhcp_lease`, `uci_dhcp_host`, `wifi_station_*`, and
`hostapd_station_*` (which disagree on label names and on MAC case). See
`docs/client-topology-and-netflow-plan.md` §0.1 and §1.1 for the full
rationale.

Identity comes from `ubus call luci-rpc getHostHints`, which OpenWrt already
builds by merging the neighbour table, `/etc/ethers`, the DHCP leasefile,
reverse DNS, and UCI static leases. The collector adds two things that call
does not know: which radio/SSID a client is associated with (from
`ubus call network.wireless status` joined with `iwinfo.assoclist` and the
`wireless.wifi-iface` UCI section, the same calls `wifi_dethrash.lua` and
upstream `wifi_stations.lua` already make) and the client's UCI network zone,
static-lease status, and IPv6 address count.

Requires `rpcd-mod-luci` (for `getHostHints`), `libubus-lua`, `libiwinfo-lua`,
and `libuci-lua`. If `rpcd-mod-luci` or `libubus-lua` is missing, the
collector falls back to parsing `/tmp/dhcp.leases` directly for identity, at
the cost of AP/SSID/band attribution and roaming visibility -- a genuine
functional gap, not something the fallback can paper over. If only
`getHostHints` itself fails (for example a transient rpcd hiccup) while the
ubus socket is otherwise reachable, the fallback still applies to identity,
but the AP/SSID/band join runs over the same live connection and is
unaffected.

MAC addresses are the primary key everywhere in this collector: lowercase,
colon-separated, deliberately different from `dhcp_lease`/`uci_dhcp_host`'s
uppercase convention (see plan §9.7) -- do not join this collector's output
against those metrics without normalising case first.

`getHostHints` performs reverse-DNS lookups as one of its sources. On a home
router with dnsmasq answering for the local domain this is harmless, but if
your resolver forwards unanswered local names upstream, this can leak local
hostnames/IPs to it — see
[openwrt/luci#4089](https://github.com/openwrt/luci/issues/4089).

Bounded by `CLIENT_INVENTORY_MAX` (default 256): clients beyond the cap are
not exported and `openwrt_client_inventory_truncated` is set to `1`. The
first-seen timestamp store (`/etc/openwrt-client-seen`, used for new-device
alerting) is capped the same way, with least-recently-seen eviction.

The generated Clients dashboard (`build_openwrt_clients_dashboard.py`) uses
this identity data directly, including MAC-keyed traffic accounting. Verify the
collector directly:

```sh
LAN_IP="$(uci get network.lan.ipaddr 2>/dev/null)"
wget -qO- "http://$LAN_IP:9100/metrics" | grep -E '^openwrt_client_(info|up|inventory)'
```

## Per-client nlbwmon traffic

The `clients` profile installs `nlbwmon`, replaces its default protocol file
with a deliberately small service set (`https`, `http`, `dns`, `quic`, `ssh`,
`smb`, `ntp`, `imaps`, `rtp`, and `other`), and runs
`openwrt-monitor-client-traffic.sh` each minute. It exports bounded counters:

- `openwrt_client_bytes_total{mac,direction,service}`
- `openwrt_client_packets_total{mac,direction,service}`
- `openwrt_client_connections_total{mac,service}`

MACs are normalized to lowercase colon-separated values. `service` is only a
trimmed protocol bucket, never a raw port or remote endpoint. IPv4 and IPv6
records are summed before export, so the exposition has one sample per label
set. The helper validates the nlbwmon JSON schema and service names before
writing any traffic samples; failure replaces the entire textfile with
`openwrt_client_traffic_collector_available 0` rather than retaining partial
or plausible wrong data.

nlbwmon counters are cumulative for its accounting period, one month by
default. A period rollover is a normal Prometheus counter reset: use `rate()`
or `increase()` and do not attempt to offset it in the helper.

### Flow offload makes traffic accounting unreliable

nlbwmon reads conntrack-netlink counters. Software flow offload can bypass
normal firewall counter updates, and hardware flow offload can bypass the
kernel datapath entirely. With either offload mode enabled, nlbwmon may
under-report or appear near zero. This affects the existing nftables traffic
profile too; it is not a nlbwmon-only limitation.

The Clients dashboard checks the existing
`openwrt_flow_offload_enabled{mode="sw"|"hw"}` metric. If either mode is `1`,
every nlbwmon traffic panel suppresses values and shows `Accounting unreliable
- flow offload enabled` instead of displaying zeros. Do not treat an empty
traffic chart in this state as idle traffic.

If accurate conntrack-derived accounting is required, disable both modes:

```sh
uci set firewall.@defaults[0].flow_offloading='0'
uci set firewall.@defaults[0].flow_offloading_hw='0'
uci commit firewall
/etc/init.d/firewall restart
```

OpenWrt's flowtable counter synchronization is a possible software-offload
mitigation, but it has a throughput cost and is not configured by this repo.

## Dashboard and validation

Generate the advanced dashboard into both supported locations:

```sh
python3 build_openwrt_advanced_dashboard.py
```

The generated files are:

- `grafana-dashboard-exports/openwrt-advanced-v2.json` for manual import
- `grafana/provisioning/dashboards/openwrt-advanced-v2.json` for Docker provisioning

The classic dashboards and `openwrt-operations-v2.json` remain separate
artifacts. Missing optional profiles are expected and are represented as
`Not collected`, not as healthy zeroes. Verify on the router with:

```sh
LAN_IP="$(uci get network.lan.ipaddr 2>/dev/null)"
wget -qO- "http://$LAN_IP:9100/metrics" | grep -E '^openwrt_(device_traffic|wifi_mesh|dpi)_collector_available'
nft -j list set inet fw4 openwrt_device_upload
```

Do not enable software or hardware flow offload when relying on nlbwmon or
nftables traffic counters; both can bypass the observation path.

## Validating the exposition

Prometheus does **not** fail a scrape that contains the same series twice: it
keeps the first sample, drops the later one, and records it as a duplicate. The
failure is therefore invisible from the dashboard — a panel simply reads a
plausible wrong number. Check any router before trusting its data:

```sh
python3 tests/check_exposition.py --url http://192.168.0.1:9100/metrics
```

The full offline suite, plus the live check when a router is reachable:

```sh
sh tests/run_all.sh
ROUTER_METRICS_URL=http://192.168.0.1:9100/metrics sh tests/run_all.sh
```

Two rules keep this class of bug out of the helper scripts:

- Never stage a temp file inside `/var/prometheus`. The textfile collector reads
  every file in that directory, so a temp left behind by a crashed run is
  scraped as a second copy of every metric in it. The helpers stage under
  `/tmp` and `mv` into place, and clean up temps leaked by earlier versions.
- Include enough labels to keep sibling objects distinct. On a multiqueue WAN
  device every child qdisc is reported with handle `0:` and is distinguished
  only by its parent, so `openwrt_tc_qdisc_*` carries a `parent` label.

## Reading the profile health tiles

A collector's own `*_collector_available` flag is not proof that the profile
works — a collector can report itself available and then abort before exporting
anything. The dashboard tiles therefore require both that flag **and**
`node_scrape_collector_success` for the same collector. To see the exporter's
own view of which collectors are failing:

```sh
wget -qO- http://192.168.0.1:9100/metrics | grep 'node_scrape_collector_success.* 0$'
```
