# OpenWrt Grafana Monitor — Code Review Findings

> **Audience:** the next agent that will act on this. This document is
> **report-only**: it describes what was found, what is suspected, and what is
> missing. It does **not** prescribe specific fixes — those decisions are left
> to the implementing agent. Every finding carries a `file:line` reference so
> the location can be verified directly.
>
> **Scope:** full repository review — router-side collectors & helper scripts,
> `setup.sh`, the Alloy + `docker-compose` monitoring host, the four dashboard
> builders, the docs, and the tests.
>
> **Target hardware tested in the repo:** ASUS RT-AX53U (MediaTek MT7621,
> OpenWrt 24.10.3). Repo also targets OpenWrt 25.12/apk.
>
> **Review date:** 2026-07-23.
>
> **Method:** three parallel deep-read passes (router-side scripts, monitoring
> host + builders, docs + tests) plus manual verification of the highest-impact
> claims against the source.

---

## How to read this document

Findings are grouped by area, then by file. Each finding has:

- A **severity** tag: `[P0]` likely-correctness bug / data loss, `[P1]` real
  but bounded issue, `[P2]` robustness/polish, `[P3]` nice-to-have/enhancement.
- A short title.
- A `file:line` anchor.
- A description of what was observed and why it matters.

Conventions the repo already follows (observed and respected below, used as
the implicit style baseline — see also
`docs/client-topology-and-netflow-plan.md` §14 "Conventions"):

- Fail-closed: every collector emits a `..._available 0` gauge when its
  dependency is missing, instead of emitting nothing.
- Stage temp files **outside** the textfile dir, then atomic `mv`.
- Lowercase MACs everywhere.
- `pcall(require, ...)` for optional Lua modules.
- `..._seconds` suffix for **durations**, not for absolute timestamps.
- `openwrt_*` prefix for repo-owned metrics; legacy alias metrics kept for
  older dashboards.

---

# Part 1 — Router side (`openwrt/`)

## 1.1 `openwrt/setup.sh`

### [P0] UCI `textfile_dir` is never configured — silent loss of all custom metrics
`openwrt/setup.sh:411-413`
The exporter is configured only for `listen_interface` and `listen_port`. The
textfile collector reads from the directory set by
`prometheus-node-exporter-lua.main.textfile_dir`. setup.sh never sets it.
Every helper writes to `/var/prometheus/*.prom` (default of
`OPENWRT_MONITOR_TEXTFILE_DIR`, see e.g. `openwrt-monitor-wifi-radio.sh:6`,
`openwrt-monitor-wan-quality.sh:6`). If the installed
`prometheus-node-exporter-lua-textfile` package's default `textfile_dir` is
not `/var/prometheus`, **none of the custom helper metrics are ever scraped**,
and there is no scrape error to surface this — the dashboards just show "no
data". Worth an explicit `uci set ...main.textfile_dir='/var/prometheus'` and
verifying `uci get ...main.textfile_dir` on a fresh install.

### [P0] Profile downgrade is not idempotent — stale collectors, cron lines, nft rule, protocols file persist
`openwrt/setup.sh:325-356, 432-446`
Collectors (`device_traffic.lua`, `wifi_dethrash.lua`, `dpi_netifyd.lua`,
`client_inventory.lua`, `topology.lua`) are `install_file`'d only when the
corresponding profile is active. `ensure_cron_line`
(`openwrt/setup.sh:119-126`) only ever appends — it never removes. The
`nlbwmon/protocols` file is installed only under `clients`
(`openwrt/setup.sh:345`). The node-exporter-lua auto-loads every `.lua` in
`/usr/lib/lua/prometheus-collectors/`. So a rerun that **narrows**
`OPENWRT_MONITOR_PROFILE` (e.g. `full` → `core`) leaves the wider profile's
collectors installed and scraped, the `clients` cron jobs still scheduled, the
customised `protocols` file in place, and the `traffic` profile's
`/etc/nftables.d/openwrt-device-traffic.nft` still loaded. The old-collector
removal block at `setup.sh:386-401` only removes the pre-split legacy scripts
(`openwrt-grafana-monitor-metrics`/`-sqm`), not the per-profile artifacts.

### [P0] `conntrack-tools` is never installed, but the `clients` profile depends on it
`openwrt/setup.sh:266-286` vs `openwrt-monitor-client-conntrack.sh:20,73`
`openwrt-monitor-client-conntrack.sh` is guarded at line 73 by
`command -v "$CONNTRACK_BIN"`. `setup.sh` installs `nlbwmon`, `libubus-lua`,
`libiwinfo-lua`, `libuci-lua`, `rpcd-mod-luci` under the `clients` profile,
but never `conntrack-tools`. Result: on a fresh `clients`-profile install the
conntrack collector permanently fail-closes
(`openwrt_client_conntrack_collector_available 0`).

### [P1] `pkg_install_required` aborts the whole setup on the first missing/offline package
`openwrt/setup.sh:73-78, 233`
Required packages install under `set -eu`. If any required package is
unavailable (mirror offline, package renamed in a newer feed), `opkg`/`apk`
returns non-zero and `set -e` aborts setup.sh with no per-package diagnostic.
Optional installs are wrapped in `if ! pkg_install_optional; then log WARNING`
(`setup.sh:237-293`), but required installs are not.

### [P1] `/etc/init.d/firewall restart` runs unconditionally on every traffic-profile rerun
`openwrt/setup.sh:457-460`
Even when the substituted nft file is byte-identical to the one already on
disk, the firewall is fully restarted, which flushes and reloads all nft
rules and can briefly drop in-flight connections. There is also no error
handling: if `firewall restart` fails (e.g. an interface-name typo from a
hand-edited conf survived validation), `set -e` aborts setup.sh after helpers
are installed and cron already scheduled — the system is left
half-configured.

### [P1] UCI configuration calls are unguarded and not batched
`openwrt/setup.sh:411-413, 480-481, 503-507`
`uci set system.@system[0].cronloglevel=...` and the `log_ip`/`log_remote`/
`log_port`/`log_proto`/`log_hostname` block assume `system.@system[0]`
exists. If the section is absent, `uci set` fails and `set -e` aborts. These
are five separate `uci set` + `uci commit` pairs; could be one `uci -q batch`.

### [P1] Five `/etc/init.d/* restart` calls under `set -e` with no per-service diagnostic
`openwrt/setup.sh:459, 465-466, 484-485, 510`
`prometheus-node-exporter-lua restart`, `cron restart`, `log restart`, etc.
all abort setup.sh anonymously on failure (e.g. if `listen_interface=lan`
doesn't resolve because the user renamed the interface). A failing restart
should print which service failed.

### [P2] Operator-supplied env values written into a sourced shell config without escaping
`openwrt/setup.sh:310-318`
`cat >/etc/openwrt-grafana-monitor.conf <<EOF` writes `KEY="$VALUE"` with no
escaping of `$VALUE`. Values come from operator env vars (`PING_TARGET`,
`DNS_PROBE_HOST`, `DNS_PROBE_TIMEOUT`, `TRAFFIC_LAN_INTERFACE`,
`CLIENT_INVENTORY_MAX`). Helpers source this with `. "$CONF"` (e.g.
`openwrt-monitor-packet-loss.sh:8`, `openwrt-monitor-wan-quality.sh:16`). A
value containing `$(...)`, backticks, or `"` would be executed on source.
`TRAFFIC_LAN_INTERFACE` is the only one validated (`setup.sh:328-330`).

### [P2] `pkg_installed` for opkg uses an unanchored grep
`openwrt/setup.sh:79-93`
`opkg list-installed | grep -q "^$1 "` — `grep -F` (or anchoring the line
end too) would be safer; package names are unlikely to be regex-special but
the fork can be avoided. Also `pkg_installed` is never used to skip
already-installed optionals, so every rerun re-installs every optional
package (`setup.sh:288-293`), wasting bandwidth.

### [P2] `sleep 2` before the metrics-endpoint check is a fixed guess
`openwrt/setup.sh:488`
If the exporter isn't up in 2 s, the "metrics endpoint not responding yet"
warning fires even though it would have come up at 3 s. A short retry loop
would be more correct.

### [P2] `<ROUTER_IP>` placeholder embedded in a URL on `uci get` failure
`openwrt/setup.sh:490-494`
If `uci get network.lan.ipaddr` fails, `LAN_IP='<ROUTER_IP>'` and the
printed `METRICS_URL='http://<ROUTER_IP>:9100/metrics'` is unusable if a
wrapper tries to `wget` it.

### [P2] `fetch_url` prefers wget, but the "endpoint not responding" diagnostic is misleading when neither wget nor curl exists
`openwrt/setup.sh:108-117, 493-498`
On a stripped install with neither, `fetch_url` returns 1 and prints
"not responding yet" even though the endpoint may be up. The diagnostic
should say "neither wget nor curl available to test".

### [P3] `topology.lua` silently bundled into the `clients` profile
`openwrt/setup.sh:343-356`
`topology.lua` rides along with `clients` (commented inline at
`setup.sh:348-352`). An operator wanting only topology must take all the
clients machinery; there is no documented `topology` profile name.

### [P3] Profile doc inconsistency
`README.md:81` lists `clients|traffic|wifi_mesh|dpi|full` (omits `core`);
`docs/openwrt-setup.md:133` lists `core|traffic|wifi_mesh|dpi|full` (omits
`clients`). `setup.sh:24,194,200` accept both `core` and `clients`. See
Part 4 for the full doc discrepancy.

---

## 1.2 `openwrt/collectors/`

### `client_inventory.lua`

**[P1] `/etc/openwrt-client-seen` rewritten on every scrape → flash wear**
`client_inventory.lua:19, 261-306, 363`
The seen-store lives in `/etc` (overlay) and is rewritten on every scrape via
`save_seen_store` (`client_inventory.lua:261-274`, invoked unconditionally at
`client_inventory.lua:363`). Default node-exporter scrape interval is ~15 s →
~5760 overlay writes/day. The `/etc` choice is for cross-boot persistence and
same-filesystem atomic-rename correctness (`client_inventory.lua:264-265`),
but on NAND/UBIFS or NOR squashfs+overlay this is avoidable wear. Storing in
`/tmp` loses `first_seen` across reboot (arguably fine) or only writing when
the mac set actually changed would both help.

**[P2] Temp-file name collision within the same second**
`client_inventory.lua:266`
`local tmp = SEEN_FILE .. ".tmp." .. tostring(os.time())`. Two scrapes within
the same second (parallel manual scrapes, scrape storms) collide and clobber
each other before `os.rename`.

**[P2] `openwrt_client_lease_expiry_seconds` is a timestamp, not a duration**
`client_inventory.lua:440, 413-420`
The value is the absolute epoch expiry (`tonumber(exp)` from
`/tmp/dhcp.leases`). The `_seconds` suffix convention is for durations;
absolute timestamps should be `..._expires_at` or
`..._expiry_timestamp_seconds`. A dashboard author computing
`time() - openwrt_client_lease_expiry_seconds` works, but the name alone
reads as a duration and is a footgun. Same issue class as `dhcp_lease` in
`dnsmasq.lua` (see E5).

**[P2] `openwrt_client_ipv6_addresses` is a count, not addresses**
`client_inventory.lua:441, 422`
Emits the count of IPv6 addresses per client. The name implies the addresses
themselves. `openwrt_client_ipv6_address_count` would be clearer.

**[P2] `arp[ip]` may be the boolean `true`, silently breaking `networks[arp[ip]]`**
`client_inventory.lua:134, 378`
`reachable[ip] = device or true` sets the value to `true` when the device
column wasn't captured. At `client_inventory.lua:378`,
`networks[arp[ip]]` becomes `networks[true]` → `nil` → falls through to
`"unknown"`. A wired client whose ARP device isn't parseable gets
`network="unknown"` silently.

**[P2] `is_up` for wired clients lags real liveness**
`client_inventory.lua:405-411`
`is_up` for wired = `arp[ip]` is truthy. ARP entries can linger for minutes
after a client disconnects (kernel neighbour cache timeout), so
`openwrt_client_up` for wired clients lags real liveness. The lua collector
emits no HELP (the framework emits an empty one), so there is nowhere for this
caveat to live for a dashboard author.

**[P3] `up`/`lease_expiry`/`ipv6`/`first_seen` lose connection/network labels**
`client_inventory.lua:389-425`
`info` emits the full 12-label set; `up`, `lease_expiry`, `ipv6`,
`first_seen` emit only `{mac=...}`. Good for cardinality, but `up` loses
`connection`/`network` which would otherwise let you alert on "all wifi
clients down".

**[P2] `update_seen_store` failure path fabricates `first=now` for every mac**
`client_inventory.lua:364-367`
If `pcall(update_seen_store, ...)` fails (e.g. `/etc` read-only), the fallback
sets `first=now` for every currently-seen mac, so
`openwrt_client_first_seen_seconds` reports "just first-seen" for everyone on
every scrape — a misleading regression on a transient error.

### `device_status.lua`

**[P1] `router_device_up` and `router_device_status` are perfectly redundant**
`device_status.lua:2-3, 33-36`
Both gauges, identical label sets, identical values (`metric_up(labels, up)`
*and* `metric_status(labels, up)` on line 34-35). Doubles sample count and
TSDB size for no information gain.

**[P2] Hostnames containing `"` or `\` are not sanitised in the shell helper**
`device_status.lua:14-22` vs `openwrt-monitor-device-status.sh:16-29`
The helper writes `device=$hostname` raw from `/tmp/dhcp.leases`. dnsmasq
allows arbitrary hostnames. The lua `metric()` helper escapes label values
(confirmed), so no scrape break, but unusual unicode hostnames produce label
values that Grafana variable matching may dislike.

**[P3] Partially-written lines are silently skipped**
`device_status.lua:25-31`
A line `device=foo` (no `up`/`status`) yields `up=nil` and the whole line is
skipped. A mid-write race produces zero output for that device — silent. The
helper's atomic `mv` mitigates this at the file level, but a malformed line
doesn't surface.

### `device_traffic.lua`

**[P1] No IPv6 traffic accounting**
`device_traffic.lua:37,113-130`, `nftables/openwrt-device-traffic.nft:5,13,24-25`
The nft sets are `type ipv4_addr`, matching only `ip saddr`/`ip daddr`. IPv6-
only or IPv6-preferring clients (Android privacy extensions, many mobile
hosts) produce zero counters in these sets while `openwrt_client_info` says
the device has no traffic.

**[P2] Hardcoded `inet fw4` table name**
`device_traffic.lua:37`
`nft -j list set inet fw4 <set>`. Always `fw4` on OpenWrt today, but breaks
silently on a custom table or a future `fw5`.

**[P2] Counters reset whenever an nft set element expires (24h timeout)**
`device_traffic.lua:143-144`, `nftables/openwrt-device-traffic.nft:6,14`
The dynamic sets have `timeout 24h`. An element evicted and re-created later
starts its counter at 0; Prometheus interprets the 0→reset as a counter
reset. `rate()` handles it; `increase()` over a window spanning the reset is
unreliable. No HELP emitted (lua collector), so there is nowhere to document
this.

**[P2] Software flow offloading can cause undercount**
`device_traffic.lua` overall, `nftables/openwrt-device-traffic.nft:18-25`
The counter rule lives in the `forward` hook. With software flow offloading
(which `client_inventory.lua:177-188` detects and exports as
`openwrt_flow_offload_enabled`), established connections can be offloaded to
a fastpath that bypasses the nftables forward chain → counters undercount.
The traffic metrics themselves don't note this.

**[P3] `interface` label is the raw config string**
`device_traffic.lua:107,124`
If an operator hand-edits `/etc/openwrt-grafana-monitor.conf` to
`TRAFFIC_LAN_INTERFACE=br-lan,eth0`, the label value becomes
`"br-lan,eth0"` (valid label value but confusing). setup.sh only validates
single names, so this only happens via manual edit.

**[P3] `entry_value` assumes `counter` exists; if absent, the set reports `available 1` with zero series**
`device_traffic.lua:75-97, 113-114`
If the set was installed without `counter` (older .nft file, manual edit),
`counter` is nil → every element is silently skipped → no metrics, no error,
but `available 1` was already returned.

### `dnsmasq.lua`

**[P0] `require "ubus"` is not pcall-wrapped — collector load crashes the whole exporter scrape**
`dnsmasq.lua:1`
Every other collector here uses `local ok, ubus = pcall(require, "ubus")`.
This file does `local ubus = require "ubus"` unconditionally. If `libubox-
lua`/`ubus` is ever missing during a scrape, the **entire** node-exporter-lua
scrape returns an error, breaking every other collector too.

**[P0] Metric names built from ubus keys are not sanitised — can break the scrape**
`dnsmasq.lua:35-37`
`metric("dnsmasq_" .. name, "counter", nil, value)`. If a future dnsmasq ubus
returns a key containing `.`, `-`, space, or reserved suffixes, the resulting
`dnsmasq_<weird>` has illegal Prometheus-name characters and Prometheus
**rejects the entire scrape**. No `gsub("[^%w_]", "_")` is applied.

**[P2] MAC uppercased here, lowercased everywhere else**
`dnsmasq.lua:18`
`labels.mac = string.upper(mac)`. `client_inventory.lua:43-48`,
`openwrt-monitor-client-traffic.sh`, `openwrt-monitor-client-conntrack.sh`
all use lowercase MACs. So `dhcp_lease{mac="AA:BB:..."}` cannot be directly
`OR`-joined with `openwrt_client_info{mac="aa:bb:..."}` without a
`label_replace` case-fold. The OpenWrt convention is lowercase.

**[P2] `value` is passed unchecked — could be a table**
`dnsmasq.lua:36`
`metric(..., value)` with `value` being whatever `pairs(metrics)` yields.
`tonumber(value) or 0` would be safer.

**[P2] `dhcp_lease` is a gauge holding an absolute expiry timestamp, name conveys no unit**
`dnsmasq.lua:28, 20`
`metric("dhcp_lease", "gauge", labels, tonumber(expires))` — `expires` is an
epoch timestamp. Name gives no hint of unit, doesn't follow the `openwrt_*`
prefix or `_seconds` suffix convention used by the rest of the repo.

**[P2] `seen_leasefile` keyed by leasefile path; UCI-conn failure silently drops custom leasefiles**
`dnsmasq.lua:31-58`
The fallback `scrape_leasefile("/tmp/dhcp.leases", lease_metric)` runs only if
`seen_leasefile["/tmp/dhcp.leases"]` is unset. If `uci get` fails (conn nil),
`seen_leasefile` is empty and only the default-leasefile fallback runs —
UCI-custom leasefiles get silently dropped. The `if conn then` block swallows
connection failures without setting any flag.

**[P2] Dashboards assume modern flat-key dnsmasq ubus metrics**
`dnsmasq.lua:35-37`
The dashboards query `dnsmasq_dns_queries_forwarded`, etc. The lua collector
iterates `for name, value in pairs(metrics)` and emits `dnsmasq_<name>` with
whatever key dnsmasq returns. On older OpenWrt (≤21) the ubus metrics object
returned nested keys like `dns.queries.forwarded` (with dots) → illegal
Prometheus metric name and the whole scrape is rejected. No guardrail or
note.

### `dpi_netifyd.lua`

**[P1] `openwrt_dpi_application_bytes` emitted as `gauge` — likely should be `counter`**
`dpi_netifyd.lua:119, 97-106`
Netifyd's `status.json` `applications` byte counts are cumulative per
accounting window. Exporting them as gauges loses `rate()`/`increase()`
usefulness and the `_bytes` name without `_total` for a cumulative value is
misleading. If the underlying values reset on netifyd restart, counter +
`rate()` is appropriate.

**[P2] Top-25 selection churns the series set — looks like counter resets**
`dpi_netifyd.lua:15, 99-112`
Only the top 25 series are exported per scrape (`MAX_SERIES`). When an app
pushes into or out of the top 25 between scrapes, its series appears/
disappears. For counters (see above) an app falling out and re-entering looks
like a counter reset of arbitrary magnitude.

**[P2] `status_age` staleness guard is bypassed entirely without `nixio`**
`dpi_netifyd.lua:32-43`
Falls back to `return nil` → `age` is nil → `if age and age > MAX_STATUS_AGE`
is false → netifyd is reported available forever. `luci-lib-nixio` is only
optionally installed under the `wifi_mesh` profile (`setup.sh:261-263`), not
under `dpi`. So a `dpi`-only profile commonly has no nixio and never gets the
dead-netifyd detection.

**[P3] `os.time()` on a router with no RTC can be ~1970 until NTP syncs**
`dpi_netifyd.lua:34-43, 30-43`
Then `os.time() - stat.mtime` would be massively negative → the
`age > MAX_STATUS_AGE` check is false → netifyd reported available. Worth
bounding `age` to `>= 0`.

### `packet_loss.lua`

**[P1] No fail-closed availability metric**
`packet_loss.lua:1-6`
Every other collector in the repo exports a `..._available` gauge. This one
doesn't, so a dashboard can't distinguish "0% loss" from "collector dead". A
missing or unparseable `/tmp/packetloss.out` emits nothing at all.

**[P1] Metric name `packet_loss` — no prefix, no unit**
`packet_loss.lua:4`
Every other OpenWrt-specific metric is prefixed `openwrt_` (or is an
intentional legacy alias). `packet_loss` violates the convention and has no
unit suffix. The value is a percentage
(`openwrt-monitor-packet-loss.sh:14`).

**[P2] `tonumber(data[1])` with no validation**
`packet_loss.lua:3-5`
If `packetloss.out` ever contains a non-numeric first token (see S1: the file
format is ` 0 packet loss` with leading space and trailing text; the lua
relies on "first whitespace token is the value", which is undocumented and
fragile), `tonumber` returns nil and `metric(..., nil)` throws (the framework
catches per-metric, so no `packet_loss` emitted, no error visible).

### `topology.lua`

**[P1] No client cap — unbounded node/edge cardinality**
`topology.lua:168-292` vs `client_inventory.lua:352-354`
`client_inventory.lua` caps emitted clients at `CLIENT_INVENTORY_MAX`
(default 256) and exports `openwrt_client_inventory_truncated`.
`topology.lua` has **no equivalent cap** — every host in `getHostHints`
becomes a `client:` node and a `lan:`/`assoc:` edge. On a busy router
(100+ IoT clients, hundreds of transient neighbours) the topology collector
emits an unbounded set of `openwrt_topology_node{...}` and
`openwrt_topology_edge{...}` series every scrape. Inconsistent with
client_inventory's own cardinality discipline.

**[P2] Router/AP/internet node values are fixed `1` regardless of WAN state**
`topology.lua:217-219`
The `internet`, `router:`, `ap:` nodes are emitted with value 1 unconditionally.
The `internet` node value being `1` is misleading when WAN is down — a
dashboard rendering node size by value shows a fat Internet node even with no
connectivity.

**[P2] `assoc_ok` false → no `lan:` edge for wired clients; pure-wired router renders disconnected clients**
`topology.lua:281-291`
`elseif assoc_ok then add_edge(...,"lan:"..mac, router_id, client_id, 1)`. If
`assoc_ok` is false (iwinfo missing, wifi disabled), wired clients get no
edge at all — a router with no wifi has zero client edges, producing a node
graph with a router and a cloud of disconnected clients.

**[P3] SSID node value and AP→SSID edge value are the same station count**
`topology.lua:246-247`
Both `add_node(..., ssid_id, {...}, ssid_station_counts[ssid_id] or 0)` and
`add_edge(..., "radio:...", ap_id, ssid_id, ssid_station_counts[ssid_id] or
0)` use the same count. Semantics ("clients on this SSID" vs "clients on
this radio link") are conflated.

**[P3] `reachable_ips` does not capture the device name, so wired edges have no `device`/`interface` label**
`topology.lua:78-96, 287-291`
Unlike `client_inventory.lua:122-140` which captures the device column,
`topology.lua`'s `reachable_ips` only stores `true`. The `lan:` edge has
labels `{id, source, target}` and no `device`/`interface` label — a node
graph panel can't show "wired via eth0" vs "wired via wlan0-bridge".

### `wifi_dethrash.lua`

**[P1] `available({}, 1)` set *before* `ubus.connect()` succeeds**
`wifi_dethrash.lua:25-42`
Line 31 sets `available({}, 1)` immediately. Line 41 `local connection =
ubus.connect()`. Line 42 `if not connection then return end` — returns
without revising `available`. So a router where `ubus` is present but
`connect()` fails reports `openwrt_wifi_mesh_collector_available 1` and emits
zero series — the exact anti-pattern the other collectors fixed.

**[P2] Weaker iwinfo kind check than sibling collectors**
`wifi_dethrash.lua:51-53`
`local ok_kind, kind = pcall(iwinfo.type, ifname); local wifi = kind and
iwinfo[kind]`. If `pcall` fails, `kind` is the error string and
`iwinfo[<err string>]` is indexed → nil. Functionally OK, but
`client_inventory.lua:231-232` and `topology.lua:131-132` use the stricter
`ok_kind and kind and iwinfo[kind]` which doesn't index on failure.
Inconsistent.

**[P1] Substantial metric-name and unit overlap with `openwrt-monitor-wifi-radio.sh`**
`wifi_dethrash.lua:33-39` vs `openwrt-monitor-wifi-radio.sh:21-32`
This collector emits `wifi_radio_txpower_dbm`,
`wifi_radio_txpower_offset_dbm`, `wifi_radio_channel`,
`wifi_radio_frequency_mhz`, `wifi_iface_ieee80211r_enabled/_k/_v`. The
helper emits `openwrt_wifi_channel`, `openwrt_wifi_frequency_hz`,
`openwrt_wifi_tx_power_dbm`, `openwrt_wifi_noise_dbm`,
`openwrt_wifi_quality_percent`, `openwrt_wifi_station_connected_seconds`.
Two parallel suites of radio metrics with different name prefixes
(`wifi_radio_*` vs `openwrt_wifi_*`), inconsistent units
(`wifi_radio_frequency_mhz` in MHz vs `openwrt_wifi_frequency_hz` in Hz),
different label sets (`{device,ifname,ssid}` vs `{ifname}`), and inconsistent
tx-power spelling (`txpower` vs `tx_power`). Dashboards and alerts have to
know which to use.

**[P3] usteer `local_info` ap label can be ugly when SSID is nil**
`wifi_dethrash.lua:95-109`
`name = ap .. "/" .. (details.ssid or node)`. If `details.ssid` is nil and
`node` is a long opaque id, the label is `"router/0x12345..."`.

### `wan_info.lua`

**Verified correct.** `wan_info.lua:4-12` parses `wanip=`, `publicip=`,
`hostname=` from `/tmp/wanip.out` and emits `wan_info{...}` with all three
labels. The Operations dashboard "WAN Identity" panel
(`build_openwrt_operations_dashboard.py:821`) renames
`hostname`/`publicip`/`wanip` — all three labels exist. No bug.

The only observation: `wan_info` (gauge, value 1) is an `info`-style metric
that doesn't carry a unit suffix, which is correct for an info metric. Good.

---

## 1.3 `openwrt/scripts/`

### `openwrt-monitor-client-conntrack.sh`

**[P0] `conntrack` binary never installed by setup.sh** (see setup.sh A3 above).
`openwrt-monitor-client-conntrack.sh:20,73`

**[P2] `jshn` array indexing only takes the first IPv4 per client**
`openwrt-monitor-client-conntrack.sh:88`
`json_get_var ip 1` reads `ipaddrs[1]`. jshn is 1-based (confirmed libubox).
A multi-homed client with a second IPv4 is undercounted — its other IPs
aren't in `$HOSTSFILE`, so conntrack flows to the second IP aren't
attributed.

**[P2] Conntrack attribution counts reply-direction and NAT-translation fields**
`openwrt-monitor-client-conntrack.sh:106-118`
`conntrack -L` rows print both original direction
(`src=client dst=remote`) and reply direction (`src=remote dst=client`). The
awk matches the client IP in any field of either direction. A flow where the
client IP appears only as `daddr` in a NAT translation (port-forwarded
inbound) is attributed. Reasonable definition of "conntrack occupancy", but
undocumented.

**[P1] `/etc/openwrt-wifi-assoc-events` rewritten every minute → flash wear**
`openwrt-monitor-client-conntrack.sh:16, 163, 183`
Same as B1. State lives in `/etc`, rewritten every minute (cron `*/1 * * * *`,
`setup.sh:434`). ~1440 overlay writes/day. The atomic-rename staging
(`EVENTSTMP` next to `EVENTSFILE`, `:17,182-183`) is correct for
same-filesystem rename but doesn't help with wear.

**[P2] First scrape after install reports inflated counts**
`openwrt-monitor-client-conntrack.sh:164-181`
On first run, `$EVENTSFILE` is `touch`'d empty (`:163`), so `counter` starts
empty and every historical hostapd line still in the logread ring increments
the counters. Subsequent scrapes are correct. A `seen` warm-up that only
counts events after the first run would avoid the install-time spike.

### `openwrt-monitor-client-traffic.sh`

**[P1] A single bad row poisons the whole collector**
`openwrt-monitor-client-traffic.sh:119-124`
`valid_mac "$mac" || fail_closed`, `valid_number … || fail_closed`,
`service=$(service_bucket "$layer7") || fail_closed`. If nlbwmon emits one
record with a malformed MAC (e.g. `00:00:00:00:00:00` from a multicast
aggregate, or a MAC with leading whitespace), the whole helper fails closed
and discards all valid records → `available 0` → dashboard shows zero
per-client traffic for that minute.

**[P1] Unknown `layer7` value → `fail_closed` instead of bucketing to `other`**
`openwrt-monitor-client-traffic.sh:70-72, 122`
`service_bucket` returns 1 for any layer7 name not in its list, so
`service=$(...) || fail_closed` dies. The trimmed `nlbwmon/protocols` file
(`setup.sh:345`) covers only ~9 buckets; nlbwmon can still produce other
layer7 names if the user added entries. Unknown should map to `other`.

**[P3] `nlbw -c json` stderr is suppressed, hiding rotation/db errors**
`openwrt-monitor-client-traffic.sh:79`
`2>/dev/null` hides the actual nlbw error which would help debugging.

**[P3] Schema check is brittle to nlbwmon version drift**
`openwrt-monitor-client-traffic.sh:94-101`
The check rejects if `actual_columns != expected_columns`. A future nlbwmon
that inserts a `family` column or renames `rx_bytes` makes the helper fail
closed. Reasonable trade-off, but no version-gating.

### `openwrt-monitor-device-status.sh`

**[P1] Ping every DHCP client every minute → cron overlap on medium networks**
`openwrt-monitor-device-status.sh:16-30` (`while ping -c1 -W1`)
For N lease entries the script serially pings each with a 1 s timeout. At
N=60 that's up to 60 s of the minute. Cron is `*/1 * * * *` (`setup.sh:418`).
With >60 clients (or slow/unreachable IPs that take the full `-W 1`), the
next minute's invocation starts before this one finishes. No `flock`/lockfile
guard — the final `mv` is last-writer-wins. CPU saturation risk on low-end
MT7621 with many clients.

**[P2] WiFi/mobile clients often don't answer ICMP → false "offline"**
`openwrt-monitor-device-status.sh:20-26`
iOS and Android default to ignoring ICMP echo from arbitrary sources. They'll
show `up=0` even when associated and active. `client_inventory.lua`'s
`openwrt_client_up` (from hostapd assoc) is a better wifi liveness signal.

**[P2] No `trap` to clean up `/tmp/device-status.out.$$` on crash**
`openwrt-monitor-device-status.sh:6, 16`
Every other helper that stages a tmp file installs
`trap 'rm -f "$TMPFILE"' EXIT`. This one doesn't.

### `openwrt-monitor-dhcp-pool.sh`

**[P0] TMPFILE is staged *inside* OUTDIR — violates the repo's own convention**
`openwrt-monitor-dhcp-pool.sh:8-9`
`TMPFILE="$OUTFILE.$$"`, `METRICFILE="$TMPFILE.metrics"` — both in
`/var/prometheus/`. The sibling helpers (e.g.
`openwrt-monitor-filesystem.sh:8-12`,
`openwrt-monitor-firewall-counters.sh:8-12`) explicitly justify staging
**outside** OUTDIR ("a temp file left there by a crashed run is scraped as a
second copy of every metric below"). A crash between line 21
(`: > "$METRICFILE"`) and line 104 (`mv`) leaves
`openwrt_dhcp_pool.prom.<pid>` and `openwrt_dhcp_pool.prom.<pid>.metrics` in
`/var/prometheus/`, and the textfile collector scrapes them as **duplicate**
`openwrt_dhcp_pool_*` series (Prometheus keeps the first, silently drops the
duplicate). `setup.sh:406` (`rm -f /var/prometheus/*.prom`) only runs on
initial setup, so the stale file persists across scrapes until the next
setup rerun.

**[P1] No `rm -f "$OUTFILE".[0-9]*` cleanup at start, no `trap` on exit**
`openwrt-monitor-dhcp-pool.sh:8-14`
Every sibling does `rm -f "$OUTFILE".[0-9]*` to scrub leftovers and installs
an EXIT trap. This one does neither. Compounds the P0 above.

**[P2] `pool_utilization` is global but `openwrt_dhcp_pool_size` is per-interface**
`openwrt-monitor-dhcp-pool.sh:34-36, 69-73, 95-98`
`pool_total` sums all interfaces, `lease_active` counts all leases, then
`openwrt_dhcp_pool_utilization_percent` is one global number. But
`openwrt_dhcp_pool_size{interface=...}` suggests per-interface granularity.
A dashboard wanting per-interface utilization has to derive it.

**[P3] `lease_remaining_min=0` on an all-static router may false-alarm**
`openwrt-monitor-dhcp-pool.sh:19, 46-65`
All-static → `lease_remaining_min=0`, `lease_active=0`. Dashboards alerting
`min==0` would fire. Semantics defensible ("0 = no expiring leases") but
undocumented.

### `openwrt-monitor-filesystem.sh`

**[P3] Only emits `overlay_*` compat alias for an existing `/overlay`**
`openwrt-monitor-filesystem.sh:32-45`
A read-only squashfs-only router (no overlay partition) gets no
`overlay_bytes_total`/`overlay_bytes_used`. The compat alias is for older
dashboards per `setup.sh:420` — those dashboards see no data on overlay-less
devices.

**[P2] `df -kP` on `/tmp` reports tmpfs (RAM), lumped under `openwrt_filesystem_*`**
`openwrt-monitor-filesystem.sh:32, 35-48`
`/tmp` on OpenWrt is tmpfs (RAM). Reporting its size/used/avail as
"filesystem" is correct but a dashboard alerting "disk full" on `/tmp` is
actually alerting "RAM full". The `mount` label disambiguates but reusing
`openwrt_filesystem_*` for both persistent and volatile mounts lumps them.

### `openwrt-monitor-firewall-counters.sh`

**[P0] iptables-based; useless on nftables-native OpenWrt (25.12/apk and many 24.10 setups)**
`openwrt-monitor-firewall-counters.sh:76-88`
Only `iptables`/`ip6tables` are probed. On OpenWrt 24.10+ with fw4 the
firewall is nftables; `iptables` may be `iptables-nft` (translation, no real
counters) or absent entirely (`available=0`). The collector has no
`nft -j list counters` path. On the documented target 25.12/apk
(nftables-native) this collector reports `available 0` and zero series for
almost every router.

**[P2] `available=1` set if either iptables or ip6tables exists, but a table listing failure is swallowed**
`openwrt-monitor-firewall-counters.sh:23, 73-90`
`collect_iptables` pipes `"$cmd" -t "$table" -L -v -x -n 2>/dev/null` to awk.
If the command fails for one table (e.g. `raw` table doesn't exist),
`2>/dev/null` hides it and awk produces nothing — but `available=1` was
already set. A router with iptables present but no `raw`/`mangle`/`nat`
tables silently emits nothing for those tables while reporting available.

### `openwrt-monitor-inodes.sh`

**[P3] Same `/tmp` is tmpfs caveat**
`openwrt-monitor-inodes.sh:33-45` (see filesystem N2).

**[P2] `df -iP` availability check on `/` only, data loop over `/overlay /tmp`**
`openwrt-monitor-inodes.sh:30, 33`
If `df -iP /` succeeds but `df -iP /overlay` fails (no overlay), the per-mount
`2>/dev/null` swallows it and nothing is emitted for that mount — fine, but
`available=1` was already declared. `available=1` with zero inode series is
possible. Same shape as the firewall-collector issue.

### `openwrt-monitor-ipv6-health.sh`

**[P1] Hardcoded `wan6` interface name**
`openwrt-monitor-ipv6-health.sh:34, 37-44`
`ifstatus wan6`. The IPv6 WAN logical interface is *commonly* `wan6` but not
always (`wan_6`, `wwan6`, `isp6` on multi-WAN or mwan3 setups). On a router
with a differently-named IPv6 interface the script reports `wan6_up=0` and
`prefix_valid_seconds=0` even though IPv6 is fully working. `sqm`
(`openwrt-monitor-sqm.sh:23`) already uses `network_find_wan` for v4
auto-detection — the v6 path should similarly parameterise via env/UCI
(`OPENWRT_IPV6_IFACE` default `wan6`) or use `network_find_wan6`.

**[P2] `/tmp/hosts/odhcpd` line count overcounts "DHCPv6 leases"**
`openwrt-monitor-ipv6-health.sh:58-60, 73-74, 81`
`dhcpv6_lease_count="$(wc -l < /tmp/hosts/odhcpd)"`. That hosts file contains
SLAAC host entries, DHCPv6 leases, and IPv4-mapped DNS entries for some
odhcpd configurations. The metric is structurally "lines in odhcpd hosts
file", not "DHCPv6 leases". Should filter to IPv6 (e.g. lines containing `:`
or `AAAA`) or use `ubus call dhcp.leases`.

**[P2] `global_addr_count` counts deprecated IPv6 addresses**
`openwrt-monitor-ipv6-health.sh:30`
`ip -6 addr show scope global | awk '/inet6 / {c++}'` counts every global v6
address including deprecated ones (SLAAC privacy addresses past
`preferred_lft`). A "global address health" dashboard should distinguish
preferred vs deprecated.

**[P3] No trap, TMPFILE outside OUTDIR — partial safety**
`openwrt-monitor-ipv6-health.sh:12, 16, 49`
TMPFILE is in `/tmp` (outside OUTDIR) — good practice, no double-scrape
risk. No EXIT trap; a crash leaves a stale tmp in `/tmp`. Less bad than the
dhcp-pool issue, but inconsistent with the trap-setting helpers.

### `openwrt-monitor-link-health.sh`

**[P2] `speed` sysfs returns `unknown_link_speed` / negative on unsupported devices**
`openwrt-monitor-link-health.sh:49-54`
For wifi, bridge, tun, ifb devices `cat …/speed` returns `-1` or EINVAL
(busybox cat to stderr, captured by `2>/dev/null`, so `speed_mbps` → 0). A
bona fide 100 Mbps link that momentarily reports `-1` during carrier
transition would be reported as 0 bps.

**[P3] `operstate` for a bridge is `up` while `carrier` may be `1` even for empty bridges**
`openwrt-monitor-link-health.sh:66-69`
A bridge with no enslaved ports still has `operstate=up` →
`openwrt_link_up{device=br-foo}=1`. Misleading for "is the link actually
working" but correct for the literal "link up" semantic.

### `openwrt-monitor-packet-loss.sh`

**[P2] `/tmp/packetloss.out` file format is " 0 packet loss", relying on undocumented "first token" parsing**
`openwrt-monitor-packet-loss.sh:14` vs `packet_loss.lua:2-5`
ping output `5 packets transmitted, 5 received, 0% packet loss`; with
`awk -F ', '`, `$3` is `" 0% packet loss"`; `gsub(/%/, "")` → `" 0 packet
loss"`. The file contains a line like ` 0 packet loss` (leading space,
trailing text). The lua collector takes the first whitespace token → `"0"` →
`tonumber("0")` = 0. Works, but the format is garbage and the lua's reliance
on "first token is the value" is undocumented and fragile.

**[P2] Ping missing entirely → `100` reported indistinguishably from total loss**
`openwrt-monitor-packet-loss.sh:14-18`
If `ping` is absent (busybox stripped) the pipeline produces empty output,
`loss=""` → set to `100`, and `packet_loss=100` exported. A dashboard alerts
"total packet loss" when the real problem is "bin missing". No
`command -v ping` check; also no availability metric (see packet_loss.lua G2).

**[P3] No trap; crash leaves `/tmp/packetloss.out.<pid>`**
`openwrt-monitor-packet-loss.sh:12-13, 20-21`

### `openwrt-monitor-service-health.sh`

**[P2] Hardcoded service list — missing `sqm`, `nlbwmon`, `usteer`**
`openwrt-monitor-service-health.sh:42-54`
The list has `cron, dnsmasq, dropbear, hostapd, log, network, odhcpd, rpcd,
uhttpd, tailscale, netifyd`. Missing:
- `sqm` — `clients` profile and SQM dashboards care.
- `nlbwmon` — `clients` profile depends on it.
- `usteer` — `wifi_mesh` profile.

Adding `nlbwmon` and `sqm` would let dashboards alert on the daemons the
helpers depend on.

### `openwrt-monitor-softnet.sh`

**[P3] `set -- $line` clobbers positional parameters at script scope**
`openwrt-monitor-softnet.sh:22, 65`
Not a bug here (no prior use), but if anyone reuses the script with
positional args, this eats them. Worth wrapping in a function.

**[P3] Only first 3 softnet fields used — `backlog`, `flow_limit_drop` available**
`openwrt-monitor-softnet.sh:64-74`
The softnet line has 10 fields; the script uses `processed`, `dropped`,
`times_squeezed` only. `softnet_backlog_len`, `flow_limit_drop` are
additional useful metrics.

### `openwrt-monitor-sqm.sh`

**[P1] Only queries the WAN (egress) device — ingress (ifb) stats never collected**
`openwrt-monitor-sqm.sh:18-30, 61, 79`
`network_find_wan` resolves the egress device; `tc -s qdisc show dev
"$wan_dev"` only shows qdiscs on it. SQM/CAKE installs qdiscs on **both** the
egress device and an intermediate functional block (`ifb4<wan>`) for ingress.
So `sqm_dropped_packets_total{direction="ingress"}` is **never** emitted
because `direction=(dev ~ /^ifb/)` is only evaluated for `$wan_dev`, which is
never an ifb. Real correctness gap for ingress queueing visibility — the
whole point of SQM on asymmetric links.

**[P2] `backlog` parsing fragile to tc output drift**
`openwrt-monitor-sqm.sh:127-143`
Modern `tc` prints `backlog 0b 0p` (suffix glued); OpenWrt ships that form,
so `sub(/b$/,"")` → `"0"`. A future tc that prints `1234bytes` (no space, no
abbreviated suffix) would break: `sub(/b$/,"")` on `"1234bytes"` →
`"1234byte"`, not `1234`.

### `openwrt-monitor-wan-info.sh`

**[P3] Public-IP lookup hits external services on every 5-min cron tick**
`openwrt-monitor-wan-info.sh:23-28`, `setup.sh:421` (cron `*/5`)
Two external endpoints (`checkip.amazonaws.com`, `api.ipify.org`) are pinged
every 5 minutes. Reasonable for change detection, but if both are unreachable
(public_ip="unknown") the `last_public_ip_file` is not updated, so a
subsequent successful lookup correctly flags a change. If the router's WAN is
down, these are 5-min DNS+TCP timeouts that block the helper. Not severe
given `-q` and short default, but worth a `--timeout` flag.

**[P3] `last_public_ip_file` written unescaped to `/tmp`; not a security issue but a TOCTOU on `cat`**
`openwrt-monitor-wan-info.sh:34, 38`

### `openwrt-monitor-wan-quality.sh`

This script was **not covered by this document's 2026-07-23 pass**. It was
reviewed on 2026-07-25 and its findings live in
`docs/CODE-REVIEW-REMEDIATION-PLAN.md` as **R4** (temp file staged inside the
textfile dir for a 15+ second window every 5 minutes, with no `trap` and no
leftover sweep). R4 is fixed; regression coverage is
`tests/test_wan_quality.sh`.

R4 was originally filed as a duplicate-series `[P0]`. A live read-only session
on 2026-07-25 **disproved that**: the textfile collector globs `*.prom` only, so
the staged `.prom.<pid>` is never scraped and no series is double-exposed. The
real defect is leftover-file accumulation in a tmpfs directory (P2). See the
live-correction block in R4 for the measurement. **The same "scraped as a second
copy" reasoning in this document's staging findings, and in the comments at
`openwrt-monitor-filesystem.sh:8-12`, `openwrt-monitor-sqm.sh:8-11`,
`openwrt-monitor-firewall-counters.sh:8-11`, is wrong for the same reason** and
should be re-checked before being cited.

### `openwrt-monitor-wifi-radio.sh`

**[P2] `openwrt_wifi_station_connected_seconds{station=...}` uppercases the MAC**
`openwrt-monitor-wifi-radio.sh:136`
`station = toupper($2)`. `client_inventory.lua`/conntrack use lowercase
(and dnsmasq uppercases — see E4). So this collector emits uppercased MACs
here too, inconsistent with client_inventory's lowercase.

**[P2] Metric name overlap with `wifi_dethrash.lua`**
`openwrt-monitor-wifi-radio.sh:21-32` vs `wifi_dethrash.lua:33-39`
Same finding as I3 above — two parallel radio metric suites.

### `openwrt/nftables/openwrt-device-traffic.nft`

**[P1] IPv4-only** (see device_traffic D1 above).

**[P2] `ct state established update` only catches established flows**
`nftables/openwrt-device-traffic.nft:24-25`
New (un-established) flows in the forward direction aren't counted until they
reach established state. Short-lived flows (DNS, NTP, SYN-flooded) may close
before hitting established and never be counted.

**[P3] `priority filter - 1` runs before the main fw4 filter chain**
`nftables/openwrt-device-traffic.nft:19`
Intentional (per the inline comment), but means a `policy drop` in fw4's main
chain doesn't reduce the device-traffic counters — they count pre-drop.
Already correct by design choice.

### `openwrt/nlbwmon/protocols`

Verified: trimmed ~9-service bucket file installed by `setup.sh:345`. Source
of the `layer7` names that `openwrt-monitor-client-traffic.sh:70-72`
requires; names outside this set cause fail-closed (see K2).

---

# Part 2 — Monitoring host (`alloy/`, `docker-compose.yml`, `.env.example`)

## 2.1 `alloy/config.alloy`

### [P1] `node_nat_traffic` drop rule uses unanchored regex; matches `node_nat_traffic_total` too if it appears
`alloy/config.alloy:55-58`
The drop regex `"node_nat_traffic"` is not anchored. Today no sibling metric
collides, but a future `node_nat_traffic_*` series would be dropped too.

### [P1] Syslog `router`-label rewrite ordering is fragile
`alloy/config.alloy:79-94`
Rule `labelmap __syslog_(.+)` (line 79-82) creates a `message_hostname`
public label **before** rule (90-94) reads `__syslog_message_hostname` to
overwrite `router`. Alloy strips `__`-prefixed labels at the end of the
relabel chain, so the source label is still readable on the second rule
today — shipping behaviour is correct. Fragile to future reordering of these
two rules.

### [P2] `message_severity` label may not match the dashboard regex for logd rfc3164 frames
`alloy/config.alloy:103-130` vs dashboards
(`build_openwrt_operations_dashboard.py:832-838`)
Dashboards filter
`message_severity=~"error|err|crit|critical|alert|emergency|emerg"`.
For OpenWrt `logd` rfc3164 frames, `message_severity` is synthesised from the
`<PRI>` numeric facility/severity and may populate as short tokens
(`crit`, `warning`) or numeric values. If Alloy normalises to numeric
severities, those filters miss every line. Worth confirming actual severity
label spelling against a running ingestion.

### [P2] `SCRAPE_INTERVAL` has no in-config default
`alloy/config.alloy:32`
`scrape_interval = sys.env("SCRAPE_INTERVAL")`. Default lives only in
`docker-compose.yml:120`. For ad-hoc `alloy run config.alloy`, an unset env
yields empty and the component fails.

### [P3] Cardinality-bounding relabel only drops `node_nat_traffic`; nothing fails closed for future unbounded series
`alloy/config.alloy:52-60`
A future `hostapd_station_*` MAC-indexed counter or `nft_counter_*` with
port/peer labels (warned about in `client-topology-and-netflow-plan.md`)
would not be bounded. Worth a comment-level note or an allowlist-by-omission
that fails closed.

## 2.2 `docker-compose.yml`

### [P1] `depends_on: service_healthy` against an image with no explicit compose `healthcheck`
`docker-compose.yml:121-123`
Alloy depends on `otel-lgtm` being healthy. The compose file declares no
`healthcheck:` block for `otel-lgtm`; the behaviour depends entirely on the
image's `HEALTHCHECK` instruction. If the upstream image ever changes/removes
that, the `service_healthy` condition waits forever and Alloy never starts.

### [P1] Read-only provisioning mount contradicts `allowUiUpdates: true`
`docker-compose.yml:15` vs `grafana/provisioning/dashboards/dashboards.yaml:8`
`./grafana/provisioning` is mounted `:ro`. `dashboards.yaml` sets
`allowUiUpdates: true`. A user editing a provisioned dashboard through
Grafana's UI gets a permission-denied on persisting; any UI change is
silently discarded on container restart. The two settings contradict each
other.

### [P2] Privileged syslog port (514) without explicit `cap_add: [NET_BIND_SERVICE]`
`docker-compose.yml:32-33`
Port 514 < 1024. Default Docker capability set includes `NET_BIND_SERVICE`
on most installs, but rootless setups or hosts with raised
`net.ipv4.ip_unprivileged_port_start` can silently fail. With
`SYSLOG_PORT>1024` override it's a non-issue, but the documented default
(`.env.example:33`) is 514.

### [P2] Floating image tags destroy reproducibility
`docker-compose.yml:3` (`grafana/otel-lgtm:latest`), `docker-compose.yml:28`
(`grafana/alloy:latest`)
A re-pull can change runtime semantics — especially impactful for the
v2beta1 dashboard schema which requires Grafana 12+.

### [P2] `TZ` hardcoded; `GF_SECURITY_ADMIN_USER` and `GF_USERS_DEFAULT_THEME` not in `.env.example`
`docker-compose.yml:17,19,21`
`TZ=Europe/Copenhagen` is environment-specific but not surfaceable. The admin
user and default theme cannot be overridden by the operator.

### [P2] `ENABLE_LOGS_*` flags naming is confusing
`docker-compose.yml:22-25`
`ENABLE_LOGS_GRAFANA=true` while `ENABLE_LOGS_LOKI=false` etc. The Alloy
path writes to Loki directly (`alloy/config.alloy:138-142`), so the
`ENABLE_LOGS_*` flags are not the Alloy path — consistent end-to-end, but
the variable names imply false disablement of Loki that doesn't actually
affect this stack's log ingestion. Worth a confirming read of the otel-lgtm
flag contract.

### [P3] No `read_only: true` filesystems / no unprivileged `user:`
`docker-compose.yml`
The alloy container only writes `/tmp/openwrt-targets.json`. Mounting tmpfs
at `/tmp` and marking the rest read-only would harden the container.

### [P3] No `logging:` driver cap; long-running syslog container logs accumulate
`docker-compose.yml`

### [P2] Default home dashboard is the v1 overview with metric mismatches
`docker-compose.yml:20`
`GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH` points at `openwrt-overview.json`
(the v1 builder output). Operators land on the v1 dashboard that has the
"Static Reservations" panel wrong-metric and the hardcoded `router=openwrt`
variable (see Part 3).

### [P2] File-SD target file regenerated only on container startup
`docker-compose.yml:41,75,114`
`/tmp/openwrt-targets.json` is written by the entrypoint before `alloy run`.
Adding/removing routers requires a full alloy container restart even though
`discovery.file` refreshes on its own schedule. Worth documenting.

## 2.3 `.env.example`

### [P1] `MONITORING_HOST_IP` is documented but consumed by no compose service or Alloy config
`.env.example:26`
It's a router-side operational hint (the router's remote-syslog target),
but neither `docker-compose.yml` nor `alloy/config.alloy` reads it. Could be
labelled documentation-only.

### [P2] `SCRAPE_INTERVAL` has no "must be a Prometheus duration" note
`.env.example:21`
A value like `30 seconds` fails loader parsing at `config.alloy:32`.

### [P2] `TZ` and `GF_USERS_DEFAULT_THEME` are real compose env vars with no `.env.example` exposure
`docker-compose.yml:17,21`

### [P2] `ROUTER_TARGETS` override claim is partial
`.env.example:11-14`
Says `ROUTER_TARGETS` overrides `ROUTER_IP`/`ROUTER_NAME`/`ROUTER_METRICS_PORT`
"for metric scraping". But `ROUTER_NAME` is still consumed as the syslog
`router` attribution fallback via `sys.env("ROUTER_NAME")` at
`alloy/config.alloy:115,128`. The override is partial and should be
documented.

### [P2] `SYSLOG_PORT=514` default + low-port bind not flagged
`.env.example:33` (see docker-compose 2c above)

### [P3] No `ENABLE_LOGS_*` or any otel-lgtm knob surfaceable
`docker-compose.yml:22-25` are pinned; operators who want Prometheus or
Tempo log pipelines cannot toggle them via `.env`. May be intentional but
undocumented.

---

# Part 3 — Dashboard builders

## 3.1 `build_dashboards.py` (the 4 classic dashboards)

### [P0] "Static Reservations" panel queries the wrong metric
`build_dashboards.py:558-566`
Panel `stat(24, "Static Reservations", ...)` has
`expr = 'max(time() - node_textfile_mtime_seconds{job="openwrt", router="$router"})'`
and description "Age in seconds of the oldest helper-generated textfile
metric." The panel title and the actual metric are unrelated. The router
emits `openwrt_dhcp_static_hosts_total` (`openwrt-monitor-dhcp-pool.sh:92-101`)
and `uci_dhcp_host` (via `prometheus-node-exporter-lua-uci_dhcp_host`).
A "Static Reservations" tile labels a freshness counter.

### [P0] v1 `router` template variable is hardcoded to `openwrt`; multi-router breaks
`build_dashboards.py:218-222`
`{"name": "router", "type": "custom", "query": "openwrt", "current": {... value:"openwrt"}}` — a fixed custom variable, not `label_values(...)`. The v2
builders (`build_openwrt_operations_dashboard.py:978`) correctly use
`label_values(node_load1{job="openwrt"}, router)` with `multi=True`/
`includeAll=True`. v1 dashboards regress multi-router correctness the rest of
the repo supports (`ROUTER_TARGETS`).

### [P1] Hardcoded `version: 1` overwrites saved versions; no determinism check; no overlap detection
`build_dashboards.py:263, 1584-1587, 252-256`
Every run overwrites any saved version — combined with `allowUiUpdates: true`
(see 2.2) this is a silent "you will lose changes" trap. JSON is written
without `sort_keys` and without the idempotency assertion the v2 builders use
(e.g. `build_openwrt_operations_dashboard.py:1121-1123`). `make_dashboard`
only asserts `x + w <= 24`; no pairwise rectangle-overlap check (the v2
builders do this at `build_openwrt_operations_dashboard.py:1065-1078`).

### [P1] Cross-dashboard nav links omit all 4 v2 dashboards
`build_dashboards.py:274-279, 1561-1564`
v1 dashboard links only point to overview/network/devices/logs. The four v2
dashboards (operations, advanced, clients, topology) never appear as nav
links. Users navigating from overview have no shortcut to topology, clients,
advanced, or operations.

### [P2] Hardcoded `pluginVersion: "12.4.0"`
`build_dashboards.py:96, 138, 167, 207, 1393, 1422, 1442`
Mis-renders on Grafana != 12.4.

### [P2] v1 `make_dashboard` provides no overlap detection
`build_dashboards.py:252-256` (see above)

### [P3] `loki_ts` defined but never used by any panel
`build_dashboards.py:1396`
Dead code.

### [P3] Python builtin shadow names
`build_dashboards.py:70` (`def stat(id, ...)` shadows builtin `id`),
`:178` (`def bargauge(..., min=0, ...)` shadows `min`).

### [P3] Docstring at top says "Generated from live metrics at http://192.168.0.1:9100/metrics"
`build_dashboards.py:3`
Hardcodes "192.168.0.1" in a stale docstring.

## 3.2 v2 builders (`build_openwrt_operations_dashboard.py`, `build_openwrt_advanced_dashboard.py`, `build_openwrt_topology_dashboard.py`, `build_openwrt_clients_dashboard.py`)

### [P1] Heavy code duplication across the v2 builders
`build_openwrt_operations_dashboard.py:999-1098`,
`build_openwrt_advanced_dashboard.py:190-266`,
`build_openwrt_topology_dashboard.py:197-307`,
`build_openwrt_clients_dashboard.py:368-471`
`layout_refs`, `grid_items_by_tab`, `iter_strings`, and an ~80-loc
`validate_dashboard` rectangle-overlap routine are duplicated across the four
files. `build_openwrt_advanced_dashboard.py` and
`build_openwrt_topology_dashboard.py` import some helpers from
`build_openwrt_operations_dashboard.py`, but each re-implements its own
validator and layout tracer, with subtle drift (e.g.
`build_openwrt_advanced_dashboard.py:262-267` uses its own allowed-globals
set defined inline, while others import it).

### [P1] Operations dashboard "WAN" hero hard-filters `target="internet"` and `target="gateway"`
`build_openwrt_operations_dashboard.py:721,739`
`:721` `(min(openwrt_wan_probe_success{PROM_FILTER, target=~"internet|resolver"}) ...)` assumes both targets exist.
`:739` `openwrt_wan_probe_packet_loss_percent{PROM_FILTER, target="gateway"}` assumes gateway exists.
If the operator's config produces only `target="gateway"` (no
`internet`/`resolver`), the hero renders 0/Unavailable. There's an optional
tab handling missing targets, but the Overview hero doesn't fall back to
"any available target". A default-config router shows zero on the second-
most-important overview tile.

### [P2] `wifi_network_noise_dbm` (operations v2) vs `openwrt_wifi_noise_dbm` (network v1) — inconsistent sourcing
`build_openwrt_operations_dashboard.py:748` (queries `wifi_network_noise_dbm` from the external `wifi` collector) vs `build_dashboards.py:1102` (queries `openwrt_wifi_noise_dbm` from the custom script). Same quantity, two different metric sources on two dashboards. If only one is installed, the other panel is empty.

### [P2] `WIFI_CLIENT_COUNT` primary branch aggregation labels key mismatch
`build_openwrt_operations_dashboard.py:649-653`
The primary branch `count by(job, router, vif) (hostapd_station_signal_dbm…)`
aggregates by `vif`, but `hostapd_station_*` typically uses `ifname`/
`station`, not `vif`. The fallback uses `count by(job, router, vif)`. If
hostapd emits `ifname` not `vif`, the primary branch produces zero output.
Worth verifying against the installed `hostapd_stations` exporter's
actual labels.

### [P3] `nft_counter_bytes` not queried; only packets (`nft_counter_packets`)
`build_openwrt_operations_dashboard.py:861` queries `sum by(name)
(rate(nft_counter_packets{...}))`. The emit side also exposes
`nft_counter_bytes`; the bytes view isn't queried.

### [P3] Both v1 and v2 deploy to the same provisioning folder; no folder separation
`grafana/provisioning/dashboards/` — 8 dashboards in one flat folder, no
tagging scheme, `foldersFromFilesStructure: false`. End users see 8
dashboards with overlapping functionality (overview vs operations, network
vs advanced). UX issue.

## 3.3 Metric/label mismatches between emit-side and dashboards

### [P1] `sqm_*`, `dns_probe_*`, `gateway_packet_loss`, `wan_public_ip_changed`, `overlay_bytes_*` emitted but queried by zero dashboards
Router emit-side (`openwrt/scripts/openwrt-monitor-sqm.sh`,
`openwrt-monitor-wan-quality.sh`, `openwrt-monitor-wan-info.sh`,
`openwrt-monitor-filesystem.sh`) emits:
- `sqm_backlog_bytes`, `sqm_dropped_packets_total`, `sqm_overlimits_total`
- `dns_probe_duration_seconds`, `dns_probe_success`
- `gateway_packet_loss`
- `wan_public_ip_changed`
- `overlay_bytes_total`, `overlay_bytes_used`, `dhcpv6_lease_count`

None of these appear in any `build_dashboard*.py` query. Dead collection on
the dashboard side:
- `dns_probe_duration_seconds` / `dns_probe_success` — a per-resolver-latency
  detector.
- `gateway_packet_loss` — direct gateway loss source.
- `wan_public_ip_changed` — counter for ISP reassignments.
- `sqm_*` — the `openwrt_tc_qdisc_*` dashboards exist
  (`build_dashboards.py:1038-1056`,
  `build_openwrt_operations_dashboard.py:608,613`)
  but they read `openwrt_tc_qdisc_*` (different script). The `sqm_*` family
  is entirely uncaptured.

### [P2] v1 packets-loss vs v2 packets-loss source inconsistency
`build_dashboards.py` (overview/devices) queries `packet_loss{job="openwrt", router="$router"}` (emitted by `openwrt-monitor-packet-loss.sh`).
`build_openwrt_operations_dashboard.py:198,704` queries `openwrt_wan_probe_packet_loss_percent{...}` (emitted by `openwrt-monitor-wan-quality.sh`).
Two scripts, two metrics, two scopes. Operators comparing overview (v1) and
operations (v2) hero stats see different numbers and assume bug. v1 will also
report stale if the user installs wan-quality but skips legacy packet-loss.sh.

### [P2] dnsmasq nested vs flat key assumption (older OpenWrt breaks)
`dnsmasq.lua:35-37` (see E2/E7) — dashboards query `dnsmasq_dns_queries_forwarded`. On older OpenWrt/dnsmasq with nested ubus keys the entire scrape fails or those series are empty.

### Verified non-issues
- `node_nat_traffic` referenced in alloy drop rule but **only** the v1
  Top-Devices panel which now correctly uses `openwrt_device_traffic_bytes_total`
  (`build_dashboards.py:1182-1193`). No mismatch.
- `wan_info` `hostname`/`publicip`/`wanip` labels exist (`wan_info.lua:4-12`)
  — the Operations "WAN Identity" panel rename is correct.
- `openwrt_wifi_station_connected_seconds` is emitted and queried (operations
  panel 812).
- `dhcp_lease` IPv4-only label set matches dnsmasq.lua emit.

---

# Part 4 — Docs

## 4.1 `docs/openwrt-setup.md`

### [P0] `OPENWRT_MONITOR_PROFILE` is missing the `clients` value
`docs/openwrt-setup.md:133`
Lists `core`, `traffic`, `wifi_mesh`, `dpi`, `full`. `openwrt/setup.sh:24,194,200` also accept `clients`, and the `clients` profile is fully installed at `setup.sh:266-285,343-352,432-433`. README.md:81 and advanced-profiles.md:15 both list `clients`. Only openwrt-setup.md omits it.

### [P1] `CLIENT_INVENTORY_MAX` env var is undocumented
`docs/openwrt-setup.md:125-134`
No entry in the "Setup Options" table, though `openwrt/setup.sh:30,51,215-216` validates it. advanced-profiles.md:153 mentions it in prose.

### [P1] "Bundled files installed by the script" omits every `clients`-profile file
`docs/openwrt-setup.md:48-71` and `:286-311`
The lists claim completeness but exclude `client_inventory.lua`, `topology.lua`,
`openwrt-monitor-client-conntrack.sh`, `openwrt-monitor-client-traffic.sh`
— all installed by setup.sh at lines 344-352, 432-433.

### [P1] Cron-job summary omits the client cron jobs
`docs/openwrt-setup.md:37`
Doesn't mention `openwrt-monitor-client-traffic.sh` or
`openwrt-monitor-client-conntrack.sh` (`openwrt/setup.sh:432-433`).

### [P1] Profile list inconsistency between README and openwrt-setup.md
`README.md:81` (`clients|traffic|wifi_mesh|dpi|full`, omits `core`)
`docs/openwrt-setup.md:133` (`core|traffic|wifi_mesh|dpi|full`, omits `clients`)
Neither lists the complete set setup.sh accepts; the two main docs disagree.

### [P2] apk (25.12) required-packages list silently drops wifi
`docs/openwrt-setup.md:79-88` (24.10/opkg) installs `-wifi` and `-wifi_stations`; `:94-100` (25.12/apk) omits both. Either 25.12 lacks those packages (worth stating) or it's an oversight.

### [P3] Manual-setup cron block diverges from setup.sh
`docs/openwrt-setup.md:169-184` doesn't include the two `openwrt-monitor-client-*` jobs; the verification grep at `:244` lists `sqm_backlog_bytes` but not `openwrt_client_*`.

## 4.2 `docs/monitoring-host-setup.md`

### [P0] "Adding more routers" advice is stale/wrong for the current stack
`docs/monitoring-host-setup.md:116-128`
Instructs users to edit `alloy/config.alloy` directly and replace
`prometheus.scrape "openwrt" { targets = [ {...} ...] }`. The actual
`alloy/config.alloy:25-30` uses file-SD:
`discovery.file "openwrt_targets" { files = ["/tmp/openwrt-targets.json"] }` fed by the docker-compose entrypoint
from `ROUTER_TARGETS`. The doc's example block doesn't even match the real
scrape block structure.

### [P1] Auto-provisioned dashboard list understates reality
`docs/monitoring-host-setup.md:66`
Says "The classic OpenWrt dashboards and the v2beta1 Operations dashboard
load automatically". The provisioning dir actually loads **8** dashboards
(overview, network, devices, logs, operations-v2, clients-v2, advanced-v2,
topology-v2). Clients/Advanced/Topology silently auto-provision too.

### [P2] Syslog multi-router guidance doesn't connect to the Alloy relabel rule
`docs/monitoring-host-setup.md:131`
Suggests `uci set system.@system[0].log_hostname` per router but never
connects it to `alloy/config.alloy:84-94` (the rule that overwrites `router`
from `__syslog_message_hostname`).

## 4.3 `docs/troubleshooting.md`

### [P1] Helper output file list is incomplete
`docs/troubleshooting.md:162-167`
Lists only `/tmp/device-status.out`, `/tmp/packetloss.out`, `/tmp/wanip.out`,
and 3 prom files. The repo also writes (verified):
`/var/prometheus/openwrt_link_health.prom`, `openwrt_dhcp_pool.prom`,
`openwrt_softnet.prom`, `openwrt_ipv6_health.prom`, `openwrt_inodes.prom`,
`openwrt_firewall_counters.prom`, `openwrt_sqm.prom`, `openwrt_wifi_radio.prom`,
`openwrt_wan_quality.prom`, `openwrt_wan6_health.prom`,
`openwrt_client_traffic.prom`, `openwrt_client_conntrack.prom`. A user
debugging, say, a missing `openwrt_client_conntrack.prom` after enabling
`clients` will not find it listed.

### [P2] Manual-refresh command list is incomplete
`docs/troubleshooting.md:187-194`
Lists 6 manual-run scripts; the cron block at openwrt-setup.md:193-207 lists 15.

### [P2] "Common networking port conflict" answer incomplete
`docs/troubleshooting.md:107`
Says change `SYSLOG_PORT` and "rerun router setup", but the router's `log_port`
UCI setting also needs to point at the new port. Mentions rerunning but never
shows the uci command. Compare with `docs/kubernetes-monitoring-setup.md:463-464`.

## 4.4 `docs/kubernetes-monitoring-setup.md`

Status: **partially relevant but materially stale** (pre-M1–M6).

### [P0] Step 2 scp instruction will fail on the current setup.sh
`docs/kubernetes-monitoring-setup.md:84-85`
Instructs `scp -O openwrt/setup.sh root@192.168.0.1:/tmp/` alone. `openwrt/setup.sh:52-54,170-171` now `die()` if the sibling `collectors/` and `scripts/` directories are absent. The k8s Step 2 instruction aborts on the router. `docs/troubleshooting.md:58` explicitly calls this same mistake out as the cause of missing custom metrics — the k8s doc instructs the thing the troubleshooting doc warns against.

### [P1] Step 8 lists only 4 dashboards; repo now ships 8
`docs/kubernetes-monitoring-setup.md:508-528`
Lists only overview/network/devices/logs. The 4 v2 dashboards are silently
absent.

### [P1] Regen instructions cite the wrong builder
`docs/kubernetes-monitoring-setup.md:59-61`
Says edit `build_dashboards.py` and regenerate. `build_dashboards.py` only
produces the 4 classic dashboards. The v2 dashboards have dedicated
generators. Editing `build_dashboards.py` won't touch any v2 dashboard.

### [P2] Metric name `node_textfile` does not exist
`docs/kubernetes-monitoring-setup.md:109`
`grep -E 'node_openwrt_info|router_device_up|dhcp_lease|node_textfile'`. There is
no `node_textfile` metric — the textfile collector emits
`node_textfile_mtime_seconds` and `node_textfile_scrape_error`. Copiers get
no match and wrongly conclude textfile export is broken.

### [P1] No mention of `clients` profile, `ROUTER_TARGETS`, or topology
Entire doc predates M1–M6. No path to deploying `clients`, topology
node-graph, nlbwmon, or multi-target scrape under k8s. Never mentions
`OPENWRT_MONITOR_PROFILE`.

### [P2] Hardcoded `router="openwrt"` in ServiceMonitor/labels
`docs/kubernetes-monitoring-setup.md:135-138,189-198,215-218`
Inconsistent with the repo's new multi-router direction. Fine for single-
router k8s, but limited.

## 4.5 `docs/advanced-profiles.md`

The most accurate of the docs. Low-severity polish:
- `:235` verification grep omits `clients` collectors.
- `:55-59`, `:49-59`, `:108-115` references to alloy's `node_nat_traffic`
  drop, traffic-profile nft counters, and `getHostHints` fallback are all
  accurate.

## 4.6 `docs/client-topology-and-netflow-plan.md`

### [P0] Document header is stale and contradicts its own body
`docs/client-topology-and-netflow-plan.md:3`
> Status: proposal. Nothing in this document is implemented yet.

The body (lines 167-188 "M0 result", and explicit markers at `:1851,1880,1902-1904` "**Done (2026-07-22)**", "**Files:** `openwrt/collectors/topology.lua` (new)…") marks milestones M0–M5 as done. Confirmed in repo: `client_inventory.lua`, `topology.lua`, `openwrt-monitor-client-traffic.sh`, `openwrt-monitor-client-conntrack.sh` all exist; `alloy/config.alloy:52-60` implements the M0 `node_nat_traffic` drop; `tests/test_topology.lua` covers M5 acceptance; `build_openwrt_topology_dashboard.py` and `grafana/provisioning/dashboards/openwrt-topology-v2.json` exist; the `clients` profile is wired in `setup.sh:266-285,343-352,432-433`. The single-line header is the most inaccurate statement in the docs.

### [P1] Stale §0.2 paragraph about profile composability
`docs/client-topology-and-netflow-plan.md:119-127`
Recommends changing `OPENWRT_MONITOR_PROFILE` to a comma-separated list "in
M1, a five-line change". Already implemented (`setup.sh:94-104,187-209`).
The plan text presents it as a future recommendation.

### [P1] Stale M0 "result" paragraph inconsistency
`docs/client-topology-and-netflow-plan.md:167,188,3`
Option (2) (`node_nat_traffic` dropped entirely) is described as "decided
and measured" but line 3 still says "nothing implemented". Confusing.

### [P3] §0.3 still proposes on-router aggregation as a future option
`docs/client-topology-and-netflow-plan.md:184-188`
Option (3) "bounded on-router aggregation" is still genuinely future work.
Consistent with the repo. Fine.

### Recommended doc fix
Change the header to "Status: M0–M6 implemented (as of 2026-07-22). M7+
deferred." and mark each in-milestone section as a historical record.

---

# Part 5 — Tests

## 5.1 Coverage

| Test | Covers |
|---|---|
| `test_device_traffic.lua` | `device_traffic.lua` — availability flag, byte-counter parsing from `nft -j`, lease-hostname vs IP-fallback labels, duplicate-series detection |
| `test_dpi_netifyd.lua` | `dpi_netifyd.lua` — fresh-snapshot availability, device/flow/application counts, determinism, stale-snapshot unavailability, age metric |
| `test_client_inventory.lua` | `client_inventory.lua` — identity success path, getHostHints-ubus-up fallback, total-ubus-failure fail-close, `CLIENT_INVENTORY_MAX` truncation, first-seen persistence + LRU eviction, router's-own-MAC filtering, flow-offload metric |
| `test_topology.lua` | `topology.lua` — M5 acceptance (every edge endpoint resolves to a node; arc groups sum to 1), disappearing-client no-dangling-edge, total failure |
| `test_sqm_collector.sh` | `openwrt-monitor-sqm.sh` — multiqueue tc parent-name disambiguation, busy qdisc survives, alias metrics emitted once, no temp-file leak |
| `test_client_traffic.sh` | `openwrt-monitor-client-traffic.sh` — nlbwmon IPv4+IPv6 summed aggregation, `other` bucket fall-through, schema-mismatch fail-close, no temp leak |
| `test_client_conntrack.sh` | `openwrt-monitor-client-conntrack.sh` — per-client row-counting semantics, assoc-events dedup across runs, mac-aggregate-only counter, conntrack-command failure fail-close |
| `check_exposition.py` | Duplicate-series detection across one or more textfile / live `/metrics` outputs |
| `run_all.sh` | Orchestration: py_compile of 4 builders, deterministic byte-identity comparison for the 4 v2 dashboards, `sh -n` on setup.sh + scripts, `luac5.1 -p` on all collectors (optional), the 7 behaviour tests, optional live exposition check |

## 5.2 Coverage gaps

### [P1] Core-profile Lua collectors have no behaviour tests
`openwrt/collectors/dnsmasq.lua`, `device_status.lua`, `packet_loss.lua`,
`wan_info.lua`, `wifi_dethrash.lua` — all installed by every default `core`
setup, zero tests. Syntax-checked by `luac5.1 -p` only. Given the repo's
rule "Never ship a metric that reports a plausible wrong value", the
headline-metric synthesizers (`device_status.lua`, `wan_info.lua`) deserve
tests, especially since `dnsmasq.lua` has the P0 `require "ubus"` crash +
unsanitised-name bugs above.

### [P1] 13 of 16 helper scripts have no tests
Of the 16 `openwrt-monitor-*.sh`, only 3 are tested (sqm, client-traffic,
client-conntrack). Untested: device-status, dhcp-pool (has the P0 staging
bug), filesystem, firewall-counters (P0 iptables/nft issue), inodes,
ipv6-health (hardcoded wan6), link-health, packet-loss, service-health,
softnet, wan-info, wan-quality, wifi-radio.

### [P1] Classic dashboards are not determinism-verified
`tests/run_all.sh:28-36` byte-compares only the 4 v2 dashboards.
`build_dashboards.py` is only `py_compile`d at `:16`, never executed; its 4
classic outputs in provisioning aren't compared against fresh generation. A
change that produces subtly different classic JSON passes `run_all.sh`.

### [P2] `wifi_dethrash.lua` (wifi_mesh profile) has no test
Documented (`advanced-profiles.md:62-71`), present, but no
`test_wifi_dethrash.lua`. The wifi_mesh M5 milestone has no acceptance test.
Touching `ubus`/`iwinfo`/`usteer`, it's a likely place for an omitted-fixture
bug — see the `available({},1) before ubus.connect()` finding (I1).

### [P2] Test runtime hidden dependency on `jq`
`test_client_traffic.sh:19-39`, `test_client_conntrack.sh:33-45` shell out
to `jq` via a `jshn.sh` shim. `jq` is not declared in `run_all.sh`, not in
any doc, and not the kind of thing present on a minimal OpenWrt dev box.
`jq` missing → `set -eu` aborts with "command not found". `run_all.sh`
checks and gracefully skips `lua5.1` (`:52-59`) but not `jq`.

### [P2] `check_exposition.py` scope is narrow
Validates only duplicate-series detection. Does NOT validate:
- Mandatory HELP/TYPE comments (best practice; optional in-text)
- Metric-name legality beyond the first character
- Label-value escaping (raw newlines in a label value)
- Counter monotonicity / type-vs-value agreement
- Duplicate HELP or TYPE for the same metric

The docstring is honest about this, but the scope is worth widening,
especially in light of the dnsmasq.lua unsanitised-name P0.

### [P3] `test_client_inventory.lua` sleeps to defeat 1-second `os.time()` resolution
`test_client_inventory.lua:300-305,313,324`
`os.execute("sleep 1")` adds ~3 s to the suite. Injecting a fake clock (a
`now()` override in the collector) would be cleaner.

### [P3] `test_topology.lua` and `test_client_inventory.lua` duplicate mock scaffolding
Both redefine `package.preload["ubus"]`, `["iwinfo"]`, real_open/real_popen
shims. A shared `tests/lib/mock_openwrt.lua` would reduce drift if the
collectors' ubus method calls ever diverge. They agree today.

### [P3] `test_device_traffic.lua` and `test_dpi_netifyd.lua` shell out to `python3` to decode JSON
Couples Lua test correctness to a `json_to_lua.py` run and to `python3` on
PATH inside `io.popen`. If `lua5.1`'s `io.popen` is restricted (some
sandboxes), these fail misleadingly.

## 5.3 Fixture realism — verified

| Fixture | Real-format match |
|---|---|
| `tests/fixtures/nft-set-upload.json` | Real `nft -j list set inet fw4 <name>` shape: `metainfo` + `set` with `elem[]` of `{elem:{val,timeout,expires,counter:{packets,bytes}}}`; `dynamic`+`timeout` flags |
| `tests/fixtures/dhcp.leases` | Real dnsmasq leasefile 5-column format |
| `tests/fixtures/gethosthints.json` | Matches `ubus call luci-rpc getHostHints`; uppercase MAC deliberately included (the case the normalisation rules exist to fix) |
| `tests/fixtures/wireless_status.json` | Matches `ubus call network.wireless status`; radio0/radio1 with `interfaces[].ifname`/`config.ssid` |
| `tests/fixtures/assoclist.json` | Matches `iwinfo.assoclist`; empty interface and a post-disconnect case |
| `tests/fixtures/proc-net-arp.txt` | Real `/proc/net/arp` 6-column format |
| `tests/fixtures/tc-qdisc-mq-wan.txt` | Real `tc qdisc` text for an `mq` root with multiple `fq_codel` children distinct only by `parent :N` (exactly the bug `test_sqm_collector.sh:4-7` documents) |
| `tests/fixtures/netifyd-status.json` | Matches `/var/run/netifyd/status.json` shape; bytes value reused verbatim as assertion anchor |
| `tests/fixtures/nlbw.json` | Matches nlbwmon CSV-in-JSON; includes the schema-mismatch `bad.json` written by `test_client_traffic.sh:56`; lowercase/uppercase MAC pairs for normalisation check |
| `tests/fixtures/hostname.txt` | Trivially correct |

## 5.4 Are the Lua tests testing the collectors correctly?

Yes, with one caveat:

- Harnesses `dofile` the real collector and call `collector.scrape()` — not
  reimplementations.
- The `metric()` global is stubbed the same way the real exporter calls into
  collectors.
- `io.popen`/`io.open` are shadowed and routed to fixtures; intercepted
  command patterns match the actual collectors' reads.
- `package.preload` for `cjson.safe`/`ubus`/`iwinfo`/`uci`/`nixio` correctly
  returns mocks.
- **Caveat:** `test_device_traffic.lua` and `test_dpi_netifyd.lua` shell out
  to `python3` to mock `lua-cjson` via a `package.preload["cjson.safe"]`
  returning a Python-backed `decode`. Correct, but couples test correctness
  to a `json_to_lua.py` run and `python3` in the `io.popen` environment.

---

# Part 6 — Missing metrics / data the OpenWrt router exposes but the repo doesn't capture

These are observability signals an OpenWrt router routinely exposes that
the repo currently does not surface.

### [P1] CPU frequency / thermal-throttling counters
`/sys/devices/system/cpu/cpufreq` — `node_cpu_seconds_total{mode}` is covered but `node_cpu_frequency_*` and per-CPU throttling counters are not. Useful on MT7621 under load.

### [P1] IPv6 device-traffic accounting
`nftables/openwrt-device-traffic.nft` is IPv4-only (see device_traffic D1).
IPv6-only/preferring clients show no traffic. Companion `ip6` sets/rules
would close this.

### [P2] Disk/IO stats for USB/SD storage
No `node_disk_*` metrics collected; only `openwrt_filesystem_*`. If an external drive is attached, IO pressure metrics are missing.

### [P2] Ingress SQM/ifb stats
`openwrt-monitor-sqm.sh` only queries the egress device (see sqm V1). Ingress queueing visibility is the whole point of SQM on asymmetric links; `sqm_dropped_packets_total{direction="ingress"}` is never emitted.

### [P2] uhttpd (admin-UI) access/error counters
Most OpenWrt routers expose the admin UI via uhttpd; not captured.

### [P2] BATMAN-adv mesh counters
Common OpenWrt mesh setups expose `/sys/kernel/debug/batman_adv/*` counters. No BATMAN metrics present on either side.

### [P3] nlbwmon top remote ASNs / countries / ports
The clients dashboard explicitly defers per-remote-destination ranking to
"the traffic-attribution phase" per `client-topology-and-netflow-plan.md`
(M7, still deferred). Operators wanting external-peer visibility get nothing
currently.

### [P3] Per-client reject-reason codes from hostapd
`openwrt_wifi_assoc_events_total{event=...}` is captured, but auth/reject
reason codes from `hostapd` log lines are only in raw Loki — no numeric
extractor.

### [P3] `nft_counter_bytes` only — packets queried, bytes not
`build_openwrt_operations_dashboard.py:861` queries `nft_counter_packets`
only.

### [P3] Software-flow-offload undercount flag on the traffic panels
`openwrt_flow_offload_enabled` is emitted by `client_inventory.lua` but the
device-traffic panels don't visually mark offload-degraded counts.

### [P3] Deprecated vs preferred IPv6 address distinction
`openwrt-monitor-ipv6-health.sh:30` counts every global v6 address
including deprecated (see ipv6-health Q3).

### [P3] `softnet_backlog_len`, `flow_limit_drop`
`openwrt-monitor-softnet.sh:64-74` only uses processed/dropped/squeezed;
backlog and flow-limit drops are available.

### [P3] `node_textfile_*` not surfaced on any dashboard
The availability of the textfile collector itself (`node_textfile_mtime_seconds`, `node_textfile_scrape_error`) is emitted but not queried anywhere — useful as a "are the helpers actually being scraped" tile (cf. the misused "Static Reservations" v1 panel at build_dashboards.py:558).

---

# Part 7 — Summary / prioritisation map

A consolidated, ordered view for the implementing agent. Each row maps a
finding to its area and the files it touches. This is **not** a fix prescription;
it's a triage.

| Pri | Finding | Area | Anchors |
|---|---|---|---|
| P0 | UCI `textfile_dir` never set — all custom metrics silently lost | router | `openwrt/setup.sh:411-413` |
| P0 | `dnsmasq.lua` unpcall'd `require "ubus"` + unsanitised ubus-key metric names crash whole scrape | router | `openwrt/collectors/dnsmasq.lua:1,35-37` |
| P0 | `firewall-counters.sh` iptables-only; useless on nftables-native OpenWrt 25.12 | router | `openwrt/scripts/openwrt-monitor-firewall-counters.sh:76-88` |
| P0 | `dhcp-pool.sh` stages TMPFILE inside OUTDIR (no trap, no cleanup) → duplicate series on crash | router | `openwrt/scripts/openwrt-monitor-dhcp-pool.sh:8-14` |
| P0 | `conntrack-tools` not installed by `clients` profile | router | `openwrt/setup.sh:266-286`; `openwrt-monitor-client-conntrack.sh:20,73` |
| P0 | Profile downgrade not idempotent (stale collectors/cron/nft/protocols) | router | `openwrt/setup.sh:325-356,432-446` |
| P0 | v1 "Static Reservations" panel queries wrong metric | dashboards | `build_dashboards.py:558-566` |
| P0 | v1 `router` template variable hardcoded; multi-router breaks v1 dashboards | dashboards | `build_dashboards.py:218-222` |
| P0 | k8s doc Step 2 scp instruction aborts on current setup.sh | docs | `docs/kubernetes-monitoring-setup.md:84-85` |
| P0 | plan doc header "nothing implemented yet" contradicts its body | docs | `docs/client-topology-and-netflow-plan.md:3` |
| P0 | openwrt-setup.md omits `clients` profile value/files/cron entirely | docs | `docs/openwrt-setup.md:48-71,133,286-311` |
| P0 | monitoring-host-setup.md "Adding more routers" tells users to edit config.alloy inline | docs | `docs/monitoring-host-setup.md:116-128` |
| P1 | `device_traffic` IPv4-only nft sets | router | `device_traffic.lua`; `nftables/openwrt-device-traffic.nft:5,13,24-25` |
| P1 | `dpi_netifyd` emits counters as gauges; top-25 churn looks like resets | router | `dpi_netifyd.lua:97-119` |
| P1 | `wifi_dethrash` `available=1` set before `ubus.connect()` | router | `wifi_dethrash.lua:25-42` |
| P1 | `wifi_dethrash` vs `openwrt-monitor-wifi-radio.sh` parallel radio metric suites, naming/unit drift | router | `wifi_dethrash.lua:33-39`; `openwrt-monitor-wifi-radio.sh:21-32` |
| P1 | `topology.lua` no client cap — unbounded cardinality | router | `topology.lua:168-292` |
| P1 | `packet_loss.lua` no availability metric; no prefix; no unit | router | `packet_loss.lua:1-6` |
| P1 | `wan_info`/`client-conntrack` flash wear (every scrape / every minute on `/etc`) | router | `client_inventory.lua:261-306,363`; `openwrt-monitor-client-conntrack.sh:16,163,183` |
| P1 | `client-traffic.sh` single bad row poisons the whole collector; unknown layer7 fails closed | router | `openwrt-monitor-client-traffic.sh:70-72,119-124` |
| P1 | `ipv6-health.sh` hardcoded `wan6` iface; odhcpd line count overcounts | router | `openwrt-monitor-ipv6-health.sh:30,34,58-60` |
| P1 | `device-status.sh` cron-overlap on medium networks; no `trap` | router | `openwrt-monitor-device-status.sh:6,16-30` |
| P1 | `sqm.sh` only queries egress; ingress ifb never collected | router | `openwrt-monitor-sqm.sh:18-30,61,79` |
| P1 | `service-health.sh` missing `sqm`/`nlbwmon`/`usteer` | router | `openwrt-monitor-service-health.sh:42-54` |
| P1 | `client-conntrack.sh` first-scrape-after-install inflated counts | router | `openwrt-monitor-client-conntrack.sh:164-181` |
| P1 | setup.sh aborts on first missing required package; UCI calls unguarded; firewall restart unconditional | router | `openwrt/setup.sh:73-78,411-413,457-510` |
| P1 | Alloy `node_nat_traffic` drop regex unanchored; syslog router-label rewrite ordering fragile | host | `alloy/config.alloy:55-58,79-94` |
| P1 | docker-compose `depends_on: service_healthy` without explicit healthcheck; `:latest` tags; read-only mount vs `allowUiUpdates` | host | `docker-compose.yml:3,15,28,121-123` |
| P1 | `MONITORING_HOST_IP` documented but unused; `ROUTER_TARGETS` override claim partial | host | `.env.example:11-14,26` |
| P1 | v2 builders heavy code duplication with drift (allowed-globals etc.) | dashboards | all four `build_openwrt_*.py` |
| P1 | Operations hero hard-filters `target="internet"`/`target="gateway"`; off-spec router renders 0 | dashboards | `build_openwrt_operations_dashboard.py:721,739` |
| P1 | `sqm_*`/`dns_probe_*`/`gateway_packet_loss`/`wan_public_ip_changed`/`overlay_bytes_*` emitted but queried by zero dashboards | dashboards | emit-side: `openwrt/scripts/openwrt-monitor-*.sh`; query-side: none |
| P1 | k8s doc Step 8 lists 4/8 dashboards; regen cites wrong builder; non-existent `node_textfile` metric | docs | `docs/kubernetes-monitoring-setup.md:59-61,109,508-528` |
| P1 | troubleshooting.md helper-output-file list incomplete; manual-refresh list incomplete | docs | `docs/troubleshooting.md:162-167,187-194` |
| P1 | Core-profile Lua collectors + 13/16 helper scripts untested; classic dashboards not determinism-verified | tests | `tests/run_all.sh:16,28-36`; `tests/` |
| P2 | MAC case inconsistency (`dnsmasq` upper, `wifi-radio` toupper, rest lower) — cross-metric join breaks | router | `dnsmasq.lua:18`; `openwrt-monitor-wifi-radio.sh:136` |
| P2 | `dhcp_lease` / `openwrt_client_lease_expiry_seconds` names convey wrong unit (timestamps) | router | `dnsmasq.lua:20,28`; `client_inventory.lua:440` |
| P2 | `device_status` redundant `router_device_up` vs `router_device_status` | router | `device_status.lua:33-36` |
| P2 | `packet_loss` file format ` 0 packet loss` relies on undocumented first-token parsing | router | `openwrt-monitor-packet-loss.sh:14`; `packet_loss.lua:2-5` |
| P2 | `router_device_up` for wired clients lags liveness; `device-status.sh` wifi mobile-clients false offline | router | `client_inventory.lua:405-411`; `openwrt-monitor-device-status.sh:20-26` |
| P2 | setup.sh operator env values unescaped in sourced conf; `pkg_installed` unanchored grep; `sleep 2` guess | router | `openwrt/setup.sh:79-93,310-318,488` |
| P2 | `device_traffic.lua` hardcoded `inet fw4`; counters reset on 24h element eviction (undocumented); offload undercount | router | `device_traffic.lua:37,143-144`; `openwrt-device-traffic.nft:6,14,18-25` |
| P2 | `dpi_netifyd.lua` staleness guard bypassed without nixio (dpi profile doesn't install nixio) | router | `dpi_netifyd.lua:32-43`; `setup.sh:261-263` |
| P2 | `topology.lua` internet/router/ap node value fixed 1; wired clients disconnected if no wifi | router | `topology.lua:217-219,281-291` |
| P2 | `link-health.sh` transient false-zero on speed; `filesystem.sh`/`inodes.sh` tmpfs lumped | router | `openwrt-monitor-link-health.sh:49-54`; `openwrt-monitor-filesystem.sh:32-48`; `openwrt-monitor-inodes.sh:33-45` |
| P2 | `firewall-counters.sh` `available=1` with zero series possible; `inodes.sh` same shape | router | `openwrt-monitor-firewall-counters.sh:23,73-90`; `openwrt-monitor-inodes.sh:30,33` |
| P2 | `wifi-radio.sh` MAC toupper (inconsistent with client_inventory lowercase); parallel radio metric names | router | `openwrt-monitor-wifi-radio.sh:21-32,136`; `wifi_dethrash.lua:33-39` |
| P2 | `sqm.sh` backlog parsing fragile to tc output drift | router | `openwrt-monitor-sqm.sh:127-143` |
| P2 | dashboards: `wifi_network_noise_dbm` (v2) vs `openwrt_wifi_noise_dbm` (v1) inconsistent sourcing | dashboards | `build_openwrt_operations_dashboard.py:748`; `build_dashboards.py:1102` |
| P2 | dashboards: `WIFI_CLIENT_COUNT` primary branch `vif`/`ifname` mismatch risk | dashboards | `build_openwrt_operations_dashboard.py:649-653` |
| P2 | dashboards: v1 packets-loss vs v2 packets-loss source inconsistency; dnsmasq nested-vs-flat key assumption | dashboards/router | emit `packet_loss.lua`+`openwrt-monitor-packet-loss.sh` vs `openwrt-monitor-wan-quality.sh`; `dnsmasq.lua:35-37` |
| P2 | dashboards: v1 hardcoded `pluginVersion: "12.4.0"`; cross-nav omits v2 dashboards; `loki_ts` dead code | dashboards | `build_dashboards.py:96,...,1396,1561-1564,274-279` |
| P2 | docker-compose: low-port 514 without `NET_BIND_SERVICE`; `TZ`/theme not in `.env.example`; log flag naming; default home dashboard is v1 | host | `docker-compose.yml:3,17,20-25,32-33`; `.env.example` |
| P2 | Alloy: `message_severity` label may not match dashboard regex for rfc3164; `SCRAPE_INTERVAL` no in-config default | host | `alloy/config.alloy:32,103-130` |
| P2 | doc: monitoring-host dashboard count understated; syslog guidance doesn't connect to relabel rule; openwrt-setup apk wifi diff | docs | `docs/monitoring-host-setup.md:66,131`; `docs/openwrt-setup.md:79-100` |
| P2 | tests: `wifi_dethrash.lua` untested; `jq` undeclared test dep; `check_exposition.py` scope narrow | tests | `tests/test_*`; `tests/run_all.sh`; `tests/check_exposition.py` |
| P3 | Many minor naming/unit/polish and enhancement items across all areas | all | see Parts 1–6 above |

---

# Part 8 — Notes for the implementing agent

- Conventions the repo already follows are listed at the top of this document
  and in `docs/client-topology-and-netflow-plan.md` §14. New collectors should
  match: fail-closed `..._available`, outside-OUTDIR staging + atomic `mv`,
  lowercase MACs, `pcall(require, ...)`, `openwrt_*` prefix, `_seconds` for
  durations vs timestamps, `_total` for counters.
- The `dhcp-pool.sh` P0 (inside-OUTDIR staging) is the single most
  self-contained fix and a useful warm-up; the sibling helpers are the
  canonical template.
- After any new metric is emitted, grep every `build_*.py` and every doc for
  both the new name and any sibling already emitting a similar quantity, to
  avoid the `wifi_*` vs `openwrt_wifi_*` / `packet_loss` vs `openwrt_wan_probe
  _packet_loss_percent` / `sqm_*` vs `openwrt_tc_qdisc_*` families that exist
  today.
- The `tests/run_all.sh` discipline (byte-identity regression for v2
  dashboards, `sh -n`, `luac5.1 -p`) should be extended to `build_dashboards
  .py` output and to the 5 core Lua collectors + 13 untested helpers.
- Many of the P0/P1 findings interact: fixing setup.sh idempotency needs to
  also remove stale per-profile artifacts; fixing the firewall-counters
  iptables/nft gap should also surface a `..._available` for the nft path;
  fixing the dnsmasq require/name issues should be paired with a test.
- No files were modified during this review. Every reference above was read
  from the source tree at `/mnt/c/repo/openwrt-grafana-monitor/` on
  2026-07-23.