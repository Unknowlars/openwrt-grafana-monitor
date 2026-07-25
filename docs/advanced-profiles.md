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
| `clients` | Unified per-client identity, bounded conntrack occupancy, nlbwmon MAC-keyed service traffic, and Loki-first WiFi roaming | `openwrt_client_inventory_collector_available`, `openwrt_client_traffic_collector_available`, `openwrt_client_conntrack_collector_available` |
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
static-lease status, and IPv6 address count. WiFi clients use the network name
reported by `network.wireless status`; wired clients resolve their ARP device
against `network.interface` UCI `device`/`ifname`. If a wired device cannot be
resolved, its `network="unknown"` label is explicit rather than assuming it is
trusted `lan`.

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

## Per-client conntrack and WiFi roaming

The `clients` profile runs `openwrt-monitor-client-conntrack.sh` once per
minute. It uses `getHostHints` only to map known local IPv4 addresses back to
the existing lowercase `mac` identity, then counts each `conntrack -L` row
once for every matching client. It exports one bounded gauge per known client:

The helper prefers the `conntrack` package (`conntrack -L`) and falls back to
`/proc/net/nf_conntrack` or `/proc/net/ip_conntrack` when the CLI is absent.
Client identity still prefers `getHostHints`, but falls back to DHCP leases and
ARP when the LuCI host-hints path is unavailable or cannot be parsed.
If no conntrack source is available, only the conntrack metrics fail closed;
the WiFi association-event companion still runs from `network.wireless status`
and `logread` when those sources are available. **Live audit, 2026-07-25:**
both reference routers had the helper deployed but reported
`openwrt_client_conntrack_collector_available 0`; the fallback keeps that from
being a CLI-packaging-only outage on routers with readable procfs conntrack
state.

The MCP `conntrack_sources` diagnostic reports only source availability and
row/byte counts for this path. It deliberately does not return raw conntrack
rows, host hints, client addresses, or MAC addresses.

- `openwrt_client_conntrack_entries{mac}`
- `openwrt_client_conntrack_truncated`

There are no remote-IP, port, IPv6-address, domain, or vendor labels. For 60
clients this adds 60 series plus its availability gauge. The association
counter adds a separate availability gauge. A known busy client should be
spot-checked on the router with `conntrack -L | grep <ip> | wc -l`, or against
`/proc/net/nf_conntrack` when the CLI is absent, before the number is treated as
trustworthy. Flow offload invalidates byte accounting; whether it changes
*entry occupancy* is a separate router-specific live gate, so this repo does
not infer it from the traffic result.

Bounded by `CLIENT_CONNTRACK_MAX` (default 256), matching the inventory
collector's default cap. When `getHostHints` has more known clients than the
cap, the helper keeps the clients with the highest current conntrack count,
drops the idle tail first, and emits `openwrt_client_conntrack_truncated 1`.
Set `CLIENT_CONNTRACK_MAX=<number>` in `/etc/openwrt-grafana-monitor.conf` only
when the router and Prometheus storage budget can tolerate the extra per-client
series.

Hostapd's `AP-STA-CONNECTED` and `AP-STA-DISCONNECTED` messages already reach
Loki through syslog. The Clients dashboard keeps that raw, per-client detail
only in Loki. The Prometheus companion is deliberately limited to
`openwrt_wifi_assoc_events_total{ap,ssid,event}` and a collector-availability
gauge: roughly `APs x SSIDs x 2` (about 12 series for two APs and three SSIDs),
with no MAC label. A no-event single-AP deployment renders an empty Loki
timeline and zero event count without error.

### SSID label values are sanitized

Every collector that emits an `ssid` label value — `client_inventory.lua`,
`topology.lua`, `wifi_dethrash.lua`, and
`openwrt-monitor-client-conntrack.sh` — replaces any character outside
`[A-Za-z0-9._-]` with `_`. An SSID of `Lab Net"5G` is emitted as
`ssid="Lab_Net_5G"` on **every** path.

Two reasons: an SSID is operator-supplied free text reaching a label *value*, so
a `"` or `\` in it would corrupt the exposition line outright; and the shell and
Lua paths must agree, because the Clients dashboard joins and filters across
metrics from both (`openwrt_client_info` from Lua, `openwrt_wifi_assoc_events_total`
from the shell helper). If the two disagree, one physical SSID appears under two
different names and any `ssid=` filter or join silently matches only one family.

> **Upgrade note.** Before this was fixed the Lua collectors emitted the raw
> SSID while the shell helper sanitized. If an SSID contains a character outside
> the class, its `ssid` label value **changes** on the Lua-sourced metrics
> (`openwrt_client_info`, the topology node/edge ids, the `wifi_mesh` series)
> at the next scrape. Historical series keep the old value, so a panel spanning
> the change shows both. Dashboards need no edit — they group by whatever the
> label holds — but a hand-written query pinning a literal SSID with a space or
> quote in it must be updated. No provisioned alert rule in
> `grafana/provisioning/alerting/` matches on a literal SSID.

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
`openwrt-monitor-client-traffic.sh` each minute. If the `nlbwmon` package is
unavailable, setup skips the protocol-file install and service restart instead
of aborting; the traffic helper then reports
`openwrt_client_traffic_collector_available 0` until the package is installed.
It exports bounded counters:

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

**Live audit finding, 2026-07-23:** `openwrt_flow_offload_enabled` itself was
found to be present in the exposition's `# TYPE` line but absent as an actual
sample on ~90-97% of scrapes on both reference routers, with no error
anywhere -- the UCI read in `flow_offload_state()` (client_inventory.lua) was
failing almost every time, silently. This was hardened (retry, and a new
always-emitted `openwrt_flow_offload_read_success` gauge) but the exact UCI
failure mode was not isolated -- no live shell session was available to trace
it further this session. Check `openwrt_flow_offload_read_success` before
trusting an absence of `openwrt_flow_offload_enabled` as "offload is off".

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

`openwrt-monitor-inodes.sh` uses `df -iP` when the router supports it and falls
back to `stat -f` inode totals on BusyBox builds where `df` omits inode support.
Setup installs `coreutils-stat` opportunistically when neither source is already
usable.
If neither source is available, `openwrt_filesystem_inode_collector_available`
stays `0` and no inode series are emitted.
The MCP `inode_sources` diagnostic reports only whether those source commands
are present and usable.

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
