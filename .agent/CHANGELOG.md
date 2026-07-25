# Agent changelog

## 2026-07-25

- Changed: Expanded the generated NetFlow v2 dashboard from 31 to 48 panels now
  that live Akvorado/GeoIP data is available.
  - `build_openwrt_netflow_dashboard.py` now adds a stronger hero row, peak
    bitrate and ASN coverage tiles, source-port and destination-port rankings,
    service-class buckets, local service ports, source/destination AS rankings,
    source/destination country rankings, AS and country matrices, and city-level
    enrichment when a city database is present.
  - Country grouping now strips Akvorado/ClickHouse `FixedString(2)` NUL bytes
    so missing values do not render as `\0\0`.
  - Top-N subqueries now keep the router filter inside the subquery so
    multi-router selections do not leak global top ports/ASNs into a scoped
    panel.
- Reason: The first live GeoIP checks proved `SrcAS`, `DstAS`, country, and port
  columns are populated, so the dashboard could move from "pipeline bring-up"
  coverage to a richer operator/demo view that showcases the actual flow data.
- Validation: `python3 build_openwrt_netflow_dashboard.py` regenerated both
  dashboard copies with matching SHA-256
  `4a046b214251b834cae8344725b38f49bcd7bda64f504fe890ef6e99e760a368`;
  `python3 -m py_compile build_openwrt_netflow_dashboard.py` passed;
  `python3 -m unittest tests.test_netflow_config tests.test_setup_package_selection`
  passed (11 tests); `docker compose config` passed; `sh tests/run_all.sh`
  passed, including byte-identical generated dashboard copies. Live ClickHouse
  spot checks returned rows for source countries, source ASNs, source ports,
  service buckets, and the AS conversation matrix.
- Remaining risk: The JSON has not been rendered in a live Grafana browser
  session or screenshotted in this changelog entry. Hardware flow offload remains
  enabled on the router, so byte totals are still lower bounds.

- Changed: Fixed first NetFlow deploy failures found on 2026-07-25.
  - `docker-compose.yml` now mounts committed `akvorado/geoip/` at
    `/usr/share/GeoIP` for `akvorado-orchestrator`. Akvorado watches that
    directory even with `geoip.optional: true`, so an absent directory made the
    orchestrator restart with `cannot watch database directory`.
  - `openwrt/setup.sh` now selects the conntrack CLI package by package manager:
    `apk` installs `conntrack` directly, while `opkg` tries `conntrack-tools`
    then `conntrack`. This avoids the scary but non-fatal OpenWrt 25.12/APK
    `conntrack-tools (no such package)` message.
  - Added `tests/test_setup_package_selection.py` and updated the NetFlow,
    router setup, advanced profile, and repository-map docs.
- Changed: Fixed the next live NetFlow blocker from the same deploy. Akvorado
  received packets and the outlet decoded NetFlow v9 records, but rejected all
  flows with `input and output interfaces missing`. `openwrt/setup.sh` now reads
  each capture interface's ifIndex from sysfs and `openwrt/netflow/softflowd.config`
  renders `option interface '<ifindex>:<iface>'`, which softflowd uses to fill
  both NetFlow v9 interface fields.
- Reason: The monitoring-host profile failed live because the GeoIP directory
  did not exist, and the router setup printed a misleading package error even
  though setup completed. After those were fixed, the flow pipeline still
  stopped before ClickHouse because softflowd had no interface indexes in its
  export records.
- Validation: `python3 -m unittest tests.test_setup_package_selection` passed
  (2 tests); `python3 -m unittest tests.test_netflow_config` passed (9 tests);
  `sh -n openwrt/setup.sh openwrt/scripts/*.sh` passed; `docker compose
  --profile netflow config` rendered the `/usr/share/GeoIP` bind mount; and
  `docker compose --profile netflow run --rm --no-deps akvorado-orchestrator
  orchestrator --check /etc/akvorado/akvorado.yaml` exited 0.
- Remaining risk: Restarting the live netflow profile and re-running router
  setup were not performed by this changelog entry.

- Added: NetFlow tier via Akvorado, behind a `netflow` Docker Compose profile
  (off by default; adds Kafka + ClickHouse, taking the stack from ~1 GB to
  ~5-6 GB RAM). New `akvorado/akvorado.yaml` (validated against
  `quay.io/akvorado/akvorado:2.4.1` with `orchestrator --check --dump`),
  `akvorado/exporters.yaml.example` (operator copy is gitignored; the
  orchestrator refuses to start without it, deliberately, because an
  unresolvable ifIndex makes Akvorado discard every flow silently), and
  `akvorado/clickhouse/`. Retention cut from upstream's carrier defaults to
  raw 7d / 1m 7d / 1h 1y. `default-sampling-rate: 1` set because Akvorado
  rejects flows carrying no sampling rate and softflowd does not reliably
  advertise one.
- Added: `netflow` router profile in `openwrt/setup.sh` — installs `softflowd`,
  renders `openwrt/netflow/softflowd.config` per capture interface (WAN by
  default, `NETFLOW_INTERFACES` to widen), installs
  `openwrt/scripts/openwrt-monitor-netflow-health.sh` on a 1-minute cron, and
  enables the service. The template overrides two stock package defaults that
  fail silently: `enabled '0'` (starts nothing) and `sampling_rate '100'`
  (1-in-100 sampled byte counts). Carries the mandatory pcap filter excluding
  the collector endpoint, without which softflowd captures and re-exports its
  own NetFlow packets.
- Changed: softflowd template gained `option timeout 'maxlife=60'`
  (`NETFLOW_TIMEOUTS`) after checking softflowd's own defaults. A flow is only
  exported when it expires, and the built-in defaults are tcp/general 1h and
  maxlife **one week** (`softflowd.h DEFAULT_MAXIMUM_LIFETIME = 3600*24*7`), so
  an ongoing transfer would have contributed nothing to the dashboard until it
  ended and then landed as one spike stamped at expiry time — every throughput
  graph wrong in both shape and placement. `tests/test_netflow_config.py`
  asserts a bounded maxlife so this cannot silently regress.
- Changed: verified the softflowd UCI option names against the official
  OpenWrt `softflowd.init`. `filter`, `timeout`, `sampling_rate`, `max_flows`,
  `track_ipv6` and `export_version` all map as assumed; the ruralroots fork's
  `bpf_filter` naming does **not** apply to the packaged version. No change
  needed, but the pcap filter silently doing nothing was the risk.
- Changed: corrected two invented Akvorado metric names in the dashboard
  against upstream's troubleshooting guide —
  `akvorado_outlet_core_flows_total` → `akvorado_outlet_core_forwarded_flows_total`
  and `akvorado_outlet_core_flows_errors_total` →
  `akvorado_outlet_core_received_flows_total` plus a regex-matched error
  breakdown. Added `akvorado_inlet_flow_input_udp_in_dropped_packets_total`
  (kernel receive-buffer drops, which upstream states make byte and packet
  counts unreliable) and a "Collector Error Reasons" panel that surfaces
  Akvorado's own `error` label, naming `metadata missing` (the predicted
  ifIndex failure) directly.
- Changed: NetFlow capture defaults to the LAN bridge (`br-lan`,
  `TRAFFIC_LAN_INTERFACE`), not the WAN device. WAN capture is post-SNAT, so
  every outbound flow would carry the router's public address as its source and
  per-client attribution -- the main reason to collect per-flow data at all --
  would be lost. Capturing both would double-count every packet. Prompted by an
  operator's known-good softflowd config, which used `br-lan`.
- Changed: the dashboard derives internal/external and direction from
  `SrcNetRole`/`DstNetRole` instead of `InIfBoundary`. softflowd sets
  `if_index_in` and `if_index_out` to the SAME value -- the ifIndex of its one
  capture device (`netflow9.c`: `dc[0]->if_index_in = dc[0]->if_index_out =
  htonl(ifidx)`) -- so interface boundary is constant across the whole dataset
  and any filter on it matches every row or none. `validate_dashboard()` now
  rejects any query mentioning `IfBoundary`; verified the guard fires.
  "Throughput by Boundary" became "Throughput by Direction"
  (outbound/inbound/local).
- Changed: `akvorado.yaml` sets `schema.enabled: [TCPFlags]`. That column is
  `Disabled: true` by default in Akvorado's schema
  (`common/schema/definition.go`), so the TCP-flags panel queried a column that
  did not exist. Confirmed enabled via `orchestrator --check --dump`.
- Corrected: earlier comments described the missing pcap filter as a
  "self-amplifying" feedback loop. It is not -- every export packet shares one
  5-tuple, so they collapse into a single flow entry at fixed trivial cost. The
  filter is accounting hygiene (keep monitoring traffic out of top-talker
  panels), not blowup prevention. Comments in `softflowd.config` and
  `tests/test_netflow_config.py` corrected.
- Added: `openwrt_netflow_*` health metrics — exporter liveness via the
  softflowctl control socket, `openwrt_netflow_ifindex` (what Akvorado must be
  told), and the two counters that make flow data quietly wrong rather than
  obviously broken: `pcap_packets_dropped_total` (libpcap ring overflow) and
  `flows_force_expired_total` (flow-table overflow truncating byte counts).
- Added: `build_openwrt_netflow_dashboard.py` (34 panels, 4 tabs). Mostly
  ClickHouse SQL — flow records never enter Prometheus — with a PromQL
  "Pipeline Health" tab. Its `validate_dashboard()` asserts every ClickHouse
  `sum()`/`sumIf()` over `Bytes`/`Packets` is multiplied by `SamplingRate`
  (19 aggregates covered); omitting it under-reports silently by exactly the
  sampling factor.
- Changed: `docker-compose.yml` sets `GF_PLUGINS_PREINSTALL` for the ClickHouse
  datasource plugin. otel-lgtm has no `docker-entrypoint.sh`, so
  `GF_INSTALL_PLUGINS` is **not** honoured; `/otel-lgtm/run-grafana.sh` composes
  with `GF_PLUGINS_PREINSTALL` instead. Verified by inspecting the image.
- Changed: `alloy/config.alloy` scrapes Akvorado inlet/outlet and ClickHouse as
  `job="akvorado"` / `job="clickhouse"` — deliberately not `job="openwrt"`,
  which every existing dashboard filters on.
- Changed: `grafana/provisioning/datasources/datasources.yaml` provisions a
  ClickHouse datasource unconditionally (the provisioning mount cannot be made
  conditional on a compose profile). Unreachable and harmless with the profile
  down.
- Changed: `docs/client-topology-and-netflow-plan.md` §3.6, §10.4 and M11
  marked superseded. The flowlogs-pipeline/Loki collector design was replaced;
  the §3.5 softflowd analysis and the M10 CPU gate still stand, and **M10 has
  still not been run**.
- Changed: rewrote `docs/kubernetes-monitoring-setup.md`, which had drifted far
  enough to be actively wrong. Fixes: the install command copied only
  `setup.sh`, which the installer now rejects outright ("expects the whole
  openwrt/ directory"), so step 2 failed for anyone following it; the
  ServiceMonitor had no `metricRelabelings`, so a Kubernetes deployment got
  neither the `node_nat_traffic` drop (unbounded src x dst cross-product) nor
  the mac/bssid/station lowercasing that the Clients, Topology and WiFi Client
  Detail joins require — this is the same divergence STATUS.md records between
  `vm.k8s.home.arpa` and the local stack; the syslog relabel used
  `labelmap __syslog_(.+)`, the exact PID-into-stream-label anti-pattern
  `alloy/config.alloy` documents rejecting, and had no
  `__syslog_message_hostname` -> `router` rule so multi-AP logs all collapsed
  onto one router; the dashboard list named four dashboards when there are ten;
  and the `v1 Endpoints` object it told users to create is deprecated in
  Kubernetes 1.33+. Added multi-router guidance, profile selection, and a
  NetFlow/Akvorado section. Verified: all three YAML blocks parse, the four
  metricRelabelings match `alloy/config.alloy` one-for-one, the embedded Alloy
  config passes `alloy fmt`, and every metric name cited exists in the repo.
- Note: `softflowd` captures via libpcap, so traffic forwarded by the switch
  ASIC under hardware flow offload is invisible to it and flow totals are
  incomplete on offloading hardware. Left visible rather than worked around:
  the dashboard's "Flow Data Complete" tile reads from the existing
  `openwrt_flow_offload_enabled{mode="hw"}`, and `NETFLOW_DISABLE_HW_OFFLOAD=1`
  is opt-in.

- Changed: Reworked the topology node graph for multi-router deployments.
  `topology.lua` now detects gateway vs downstream AP, so only a gateway emits
  `internet`/`modem:`/`router:`/`port:` nodes; node ids became
  `router:<lan-ip>` and `bss:<bssid>` so separate exporters name shared things
  identically; every node and edge carries `authority`; and a new
  `openwrt_topology_infra_mac` metric publishes each box's own interface MACs.
  The dashboard reconciles across routers in PromQL: prefer first-hand series,
  drop routers that appear as each other's DHCP clients along with the edges
  pointing at them, and drop the gateway's guessed `lan:` edge when an AP
  reports a real association. Added the switch-port tier from `bridge fdb`, the
  upstream-modem hop for double NAT, vendor names and icons from a generated
  IEEE OUI table, signal-coloured association edges, the layered layout,
  semantic arcs, live B/s on both frames, and node data links. Mission Control
  panel 707 was updated to match. **Breaking:** node id shapes changed, so both
  routers must be updated together.
- Changed: `alloy/config.alloy` lowercases the `mac`, `station` and `bssid`
  labels at ingest, so upstream uppercase-MAC collectors join this
  repository's lowercase ones. `station` had to be normalised alongside `mac`
  or the operations dashboard's WiFi Client Detail join would have emptied.
- Added: `openwrt/lua/` for shared `require`-able modules (`oui.lua`,
  generated `oui_data.lua`), installed to `/usr/lib/lua/` rather than the
  collectors directory, where every file is loaded as a collector. Added
  `build_oui_table.py` to regenerate the vendor table from the IEEE MA-L
  registry; vendor mappings are never hand-written.
- Added: `tests/test_topology_promql.sh` and `tests/topology_exposition.lua`,
  which run the dashboard's shipped node-graph queries against a throwaway
  VictoriaMetrics container over exposition the real collector produces for a
  simulated gateway plus dumb AP. This is the only coverage of the
  cross-router reconciliation, which lives in PromQL rather than Lua.
- Validation: `tests/run_all.sh`, full unittest discovery, shell and Lua
  syntax, `docker compose config`, and `alloy fmt`/`alloy validate` all passed.
  Reconciliation verified end to end against a real PromQL engine, and
  confirmed to fail when the infra-MAC suppression is mutated out.
- Deployed: both routers updated together with `openwrt_run_repo_setup
  confirm=true` (profile `full`), exporters restarted, Alloy restarted.
  `ip-bridge` installed on both, so the switch-port tier is live. Live
  verification: role detection correct, 0 colliding node ids, 0 dangling edge
  endpoints, clients named by vendor, and the client on the second AP's radio
  now drawn on its association instead of as wired off the gateway.
- Fixed after first deployment: the upstream router answers ARP on the WAN
  interface, so `getHostHints` listed it like a LAN client and it rendered
  twice -- once as `modem:` and once as a client. The collector now excludes
  the WAN nexthop's MAC from the client list. Caught by live inspection, and
  now covered by a mutation-checked assertion in `tests/test_topology.lua`.
- Docs: corrected `docs/openwrt-setup.md`, whose manual-install path copied
  only `openwrt/collectors/*.lua` and so would have left the OUI modules
  missing (silent loss of vendor names) -- and whose installed-files lists had
  been missing `client_inventory.lua`/`topology.lua` since the clients profile
  landed. Also refreshed the topology row in
  `skills/grafana-dashboards/SKILL.md` (9 -> 18 elements) and the `clients`
  profile row in `docs/advanced-profiles.md`.
- Remaining risk: `vm.k8s.home.arpa` is scraped by `kube-prometheus-stack`,
  not by this repo's Alloy, so the MAC-lowercasing rule applies only to the
  local otel-lgtm stack. Each store is internally consistent and no existing
  join breaks, but casing differs between them. Grafana UI not
  re-screenshotted.

- Changed: Follow-up live fixes for MCP/router issues found after `R13`.
  Added safe `conntrack_sources` and `inode_sources` MCP diagnostics, made
  per-client conntrack fall back from `getHostHints` to DHCP leases/ARP, made
  inode metrics fall back from `df -iP` to `stat -f`, and made setup install
  `coreutils-stat` opportunistically when no inode source is present.
- Validation: focused MCP/collector/setup checks, full unittest discovery,
  shell syntax, Compose config, `tests/run_all.sh`, generated-dashboard drift
  check, `git diff --check`, and live exposition checks all passed. The fixes
  were deployed to both configured routers with bounded MCP setup.
- Remaining risk: Grafana UI was not screenshot-verified in this follow-up.

- Changed: Remediated read-only live issues found through the OpenWrt MCP pass.
  MCP metrics helpers now resolve `network.lan.ipaddr` before fetching the
  router-local exporter, and the `wifi` diagnostic redacts common wireless
  secret fields before returning `wifi status`. The clients conntrack helper
  now falls back to `/proc/net/nf_conntrack`/`ip_conntrack` when the `conntrack`
  CLI is absent and reports WiFi association events independently of conntrack
  availability. Docs and focused tests updated.
- Validation: focused MCP policy and client-conntrack tests passed; see the
  completion report for broader static/live checks.
- Remaining risk: live routers and the running MCP container still need an
  authorized deployment/rebuild before these source changes affect production
  MCP/tool output.

- Changed: Implemented `R13` from
  `docs/CODE-REVIEW-REMEDIATION-PLAN.md`. MCP setup profile validation now
  rejects `full` combined with any other profile before staging files or opening
  the setup SSH command, matching `openwrt/setup.sh`. `tests/test_mcp_policy.py`
  covers both `full,traffic` and `traffic,full`; `docs/mcp-ssh.md` documents the
  accepted profile shapes.
- Validation: static/offline only; see the R13 completion report in-session.
- Remaining risk: none known for the local validation asymmetry; no live setup
  run was needed or performed.

- Changed: Implemented `R12` from
  `docs/CODE-REVIEW-REMEDIATION-PLAN.md`. `openwrt_run_repo_setup` now uses a
  setup-specific 600 s default timeout via `OPENWRT_MCP_SETUP_TIMEOUT`, without
  raising the shared fast command default. Setup command timeouts return
  `timed_out=true`, exit code 124, and an explicit partial-configuration warning
  that names `openwrt_monitoring_status` as the next read-only check. Compose,
  `.env.example`, `docs/mcp-ssh.md`, and `tests/test_mcp_policy.py` updated.
- Validation: static/offline only; see the R12 completion report in-session.
- Remaining risk: no live router setup was run, so real slow-link behavior and
  operator recovery flow were not exercised.

- Changed: Implemented `R11` from
  `docs/CODE-REVIEW-REMEDIATION-PLAN.md`. MCP sidecar defaults to SSH
  `RejectPolicy` with operator-managed `known_hosts`
  (`OPENWRT_MCP_KNOWN_HOSTS`, compose mount of `./known_hosts`). Explicit
  `OPENWRT_MCP_INSECURE_HOST_KEYS=1` restores `AutoAddPolicy` with a warning
  log on every connection. Actionable host-key failure errors; docs and
  `.env.example` updated; `tests/test_mcp_policy.py` extended. Key-based auth
  still not supported (noted only).
- Validation: see the R11 completion report in-session.
- Remaining risk: existing MCP deployments need a populated `known_hosts`
  before SSH tools work; insecure override is available but not recommended.
  No live MCP/SSH validation was run.

- Changed: Implemented `R10` from
  `docs/CODE-REVIEW-REMEDIATION-PLAN.md`. `openwrt-monitor-client-conntrack.sh`
  resets `ssid` and `ifname` each interface iteration so a missing wireless
  `config` cannot attribute roams to the previous SSID. Extended
  `tests/test_client_conntrack.sh` (missing-config fixture + stale-SSID
  assertion) and made the offline jshn mock fail-close on absent keys. Plan
  checkbox ticked.
- Validation: see the R10 completion report in-session.
- Remaining risk: trigger depends on a live `network.wireless status` entry
  lacking `config` (not observed on a router this pass); fix is unconditionally
  safe.

- Changed: Implemented `R9` from
  `docs/CODE-REVIEW-REMEDIATION-PLAN.md`. `openwrt-monitor-wan-info.sh` stages
  the metric temp file under `/tmp`, traps cleanup of `$tmp_file` and
  `$metric_tmp`, sweeps pre-fix `"$metric_file".[0-9]*` leftovers, and honors
  `OPENWRT_MONITOR_TEXTFILE_DIR` (environment-contract change; same override
  every other textfile helper already uses). `/lib/functions/network.sh` is
  sourced only when present. Added `tests/test_wan_info.sh`, wired into
  `tests/run_all.sh`, and ticked the plan checkbox.
- Validation: see the R9 completion report in-session (focused shell test,
  unittest, `sh -n`, `docker compose config`, `tests/run_all.sh`, graphify).
- Remaining risk: static/offline only; no live router cron run was performed.

- Changed: Implemented `R8` from
  `docs/CODE-REVIEW-REMEDIATION-PLAN.md`. `openwrt/setup.sh` now skips the
  nlbwmon protocols-file install and service enable/restart when
  `/etc/init.d/nlbwmon` is absent, logging a warning instead of aborting after
  the earlier optional-package warning. Added `tests/test_setup_nlbwmon_optional.sh`,
  wired it into `tests/run_all.sh`, documented the degraded nlbwmon behavior in
  `docs/advanced-profiles.md`, and ticked the plan checkbox.
- Validation: `sh -n openwrt/setup.sh tests/test_setup_nlbwmon_optional.sh`,
  `sh tests/test_setup_nlbwmon_optional.sh`, `sh tests/test_setup_legacy_crontab.sh`,
  `python3 -m unittest discover -s tests -p 'test_*.py'` (15 tests OK),
  `sh -n openwrt/setup.sh openwrt/scripts/*.sh`, `docker compose config`, and
  `sh tests/run_all.sh` all passed. `run_all.sh` reported every generated
  dashboard copy unchanged and byte-identical; Lua checks ran.
- Remaining risk: static-only so far. A real router without nlbwmon was not used,
  and no setup/service run was performed.

- Changed: Implemented `R7` from
  `docs/CODE-REVIEW-REMEDIATION-PLAN.md`. `openwrt-monitor-client-conntrack.sh`
  now caps `openwrt_client_conntrack_entries` with `CLIENT_CONNTRACK_MAX`
  (default 256), reads the knob from `/etc/openwrt-grafana-monitor.conf`,
  keeps the busiest clients first, emits `openwrt_client_conntrack_truncated`,
  and preserves deterministic MAC-sorted exposition. `docs/advanced-profiles.md`
  documents the new knob, and the plan checkbox is ticked.
- Validation: `sh tests/test_client_conntrack.sh`, `python3 -m unittest discover
  -s tests -p 'test_*.py'` (15 tests OK), `sh -n openwrt/setup.sh
  openwrt/scripts/*.sh`, `docker compose config`, and `sh tests/run_all.sh` all
  passed. `run_all.sh` reported every generated dashboard copy unchanged and
  byte-identical; Lua checks ran. `graphify update .` completed and rebuilt the
  code graph.
- Remaining risk: static-only. No router was queried for a large real
  getHostHints table, no setup run was performed, and no Grafana/UI validation
  was performed. The related topology.lua cap mentioned in R7's cross-reference
  remains open and was deliberately not folded in.

- Changed: Re-verified and locally validated Batch A (`R1`-`R4`) from
  `docs/CODE-REVIEW-REMEDIATION-PLAN.md`; no additional behavior changes were
  made in this pass.
- Validation: targeted `R2`, `R3`, and `R4` tests passed;
  `python3 -m unittest discover -s tests -p 'test_*.py'` ran 15 tests OK;
  `sh -n openwrt/setup.sh openwrt/scripts/*.sh`, `docker compose config`, and
  `sh tests/run_all.sh` all passed. `docker image inspect grafana/alloy:latest
  --format '{{json .Config.Cmd}}'` confirmed the image CMD still carries
  `--storage.path=/var/lib/alloy/data`, and `run_all.sh` reported all generated
  dashboard artifacts unchanged.
- Remaining risk: R1's `curl http://localhost:1234/` and
  `docker compose exec alloy ls /var/lib/alloy/data` checks need a running stack
  and were not run; R2's full validation needs an authorized router with a
  legacy crontab. No live-system or Grafana/UI validation was performed.

- Changed: Corrected `R4`'s severity and rationale in
  `docs/CODE-REVIEW-REMEDIATION-PLAN.md` and
  `docs/CODE-REVIEW-FINDINGS.md`, and rewrote the matching comments in
  `openwrt/scripts/openwrt-monitor-wan-quality.sh` and
  `tests/test_wan_quality.sh`. Recorded live deployment findings in
  `.agent/STATUS.md`. No behavior change to any collector.
- Reason: an authorized read-only live session **disproved R4's central claim**.
  The staged `openwrt_wan_quality.prom.<pid>` is not scraped: 144 polls of the
  router's metrics endpoint at 1-second resolution over 340 s captured one
  confirmed `wan-quality` run and found zero duplicated series, while the
  exporter demonstrably does read `.prom` files (that is the only path
  `openwrt_wan_probe_*` reaches the exposition). The textfile collector globs
  `*.prom` only. So R4 is leftover-file housekeeping (P2), not a
  silently-wrong-metrics P0. The applied fix is unchanged and still correct;
  only the stated motivation was wrong. The same reasoning in the pre-existing
  comments of `openwrt-monitor-filesystem.sh`, `-sqm.sh`,
  `-firewall-counters.sh`, `-client-traffic.sh`, and in `R9`, is wrong for the
  same reason — flagged, not edited (out of scope).
- Validation (live, read-only, authorized; nothing mutated): both routers
  reachable; `tests/check_exposition.py --url` reports no duplicate series on
  either (2806 and 1926 samples). R1's second defect is now **live-confirmed** —
  `curl http://localhost:1234/` against the still-running pre-fix container
  returns `http=000`, so the Alloy UI is genuinely unreachable as the plan
  predicted. R6 is live-confirmed **inert on this deployment**: the only real
  SSIDs are `peach_24ghz`/`peach_5ghz` plus `""`, all inside the sanitize class,
  so no label value changes; `wifi_iface_ieee80211*` carries a populated
  `ifname`, proving the UCI/iwinfo SSID match still resolves; usteer is not
  installed, so the `ap`-label change is inert too. R3's collector is healthy
  with 158 traffic series on main, so its empty-period path is a real but
  not-currently-hit edge case. Static suite re-run after the comment edits.
- Remaining risk: R1's *fix* is still unverified — confirming the UI and the
  WAL volume needs the container recreated, which was not authorized. Six
  deployment-level problems were found that have no remediation task yet, two of
  them severe (openwrt-new is not scraped at all; no syslog reaches Loki because
  both routers point at the wrong IP) plus a credential disclosure in
  `openwrt_diagnostic wifi`. All are recorded in `.agent/STATUS.md`; none was
  fixed, since all are config/deployment changes or MCP code outside the current
  batch.

- Changed: Implemented `R6` from `docs/CODE-REVIEW-REMEDIATION-PLAN.md` and
  ticked its checkbox. SSID label values are now sanitized on every emitting
  path with one shared character class (`[A-Za-z0-9._-]`, everything else `_`).
  `client_inventory.lua` and `topology.lua` sanitize at their single
  iface-build source; `wifi_dethrash.lua` gained a `sanitize()` helper applied
  to its iwinfo SSID, its UCI SSID, and the usteer SSID feeding the `ap` label.
  Added a `wlan3` interface named `Lab Net"5G` to the shared
  `tests/fixtures/wireless_status.json`, with matching assertions in
  `tests/test_client_conntrack.sh` and `tests/test_client_inventory.lua`, and an
  "SSID label values are sanitized" subsection in `docs/advanced-profiles.md`.
- Reason: the shell helper sanitized SSID while the Lua collectors emitted the
  raw ubus value, so one physical SSID appeared under two different label values
  — `openwrt_client_info` and `openwrt_wifi_assoc_events_total` are rendered on
  one Clients dashboard page and any join or `ssid=` filter across them silently
  matched only one family. A raw `"` in an SSID additionally corrupted the
  exposition line; the pre-fix run emits literally `ssid="Lab Net"5G"`.
- **This changes label values**, which `AGENTS.md` normally forbids. Justified
  per the task: the two paths already disagreed, so some consumer was already
  broken; the work was to pick the correct value and make both agree. Only
  Lua-sourced values change, and only for SSIDs containing a character outside
  the class. No provisioned alert rule matches a literal SSID (checked), and
  dashboards group by whatever the label holds, so no generator, dashboard, or
  provisioning edit was needed. Upgrade note recorded in
  `docs/advanced-profiles.md`.
- **Plan corrections (applied in place):** (1) step 3, "adjust the `tr -c`
  class", was **unnecessary and not done** — the Lua and shell classes were
  already byte-identical, verified over `My Home`, `Say"Hi`, `back\slash`,
  `it's`, `Café-5G`, `Ünïcode`, `ok.name-1_2`, `!!!`, including one `_` per byte
  of multi-byte UTF-8; editing it would have introduced a divergence. (2) The
  named use sites were replaced by their upstream source: `topology.lua` builds
  ssid ids at five places, not the two listed, so patching only those would have
  made its node and edge ids disagree. (3) Two unnamed sites were also fixed —
  `wifi_dethrash.lua:83` had to be, because `:86` compares it against the
  iwinfo-sourced SSID to resolve `ifname`, and `:103` had the identical
  corrupt-the-line exposure via the `ap` label.
- Validation: `python3 -m unittest discover -s tests -p 'test_*.py'` (15 tests,
  OK), `luac5.1 -p` on all collectors, `sh -n openwrt/setup.sh
  openwrt/scripts/*.sh`, `docker compose config`, and `sh tests/run_all.sh`
  (ALL CHECKS PASSED). Lua checks ran, not skipped. No generated dashboard
  drift. The new assertions were confirmed to fail against the pre-fix
  collectors while the shell test kept passing — which is exactly the divergence
  the task describes. `tests/test_topology.lua`'s printed node/edge count moved
  from 11/10 to 12/11 because the shared fixture gained an interface; that count
  is reported, not asserted.
- Remaining risk: static-only, as with the rest of this batch — no router was
  touched, so the sanitized values are confirmed from fixtures rather than live
  ubus output. A non-UTF-8 or locale-dependent SSID byte sequence was exercised
  only as UTF-8. `R7` and `R10` remain open and both touch
  `openwrt-monitor-client-conntrack.sh`; `R10`'s stale-`$ssid` region is
  adjacent to the line `R6` left unchanged and was deliberately not folded in.

- Changed: Implemented `R5` from `docs/CODE-REVIEW-REMEDIATION-PLAN.md` and
  ticked its checkbox. `alloy/config.alloy` no longer uses a wildcard
  `labelmap` over `__syslog_(.+)`; three explicit rules promote
  `message_severity`, `message_facility`, and `message_app_name`, each guarded
  with `regex = "(.+)"`. The `__syslog_message_hostname` → `router` rule is
  unchanged. Added `tests/test_alloy_syslog_labels.py` and a "Syslog stream
  labels" subsection to `docs/troubleshooting.md`.
- Reason: `__syslog_message_proc_id` is the process PID for OpenWrt logd
  rfc3164 frames, so promoting it made every dnsmasq/hostapd/netifd/odhcpd
  restart mint a new Loki stream — unbounded stream cardinality on the log
  path, invisible from any dashboard. `message_msg_id`, `message_hostname`
  (redundant with `router`), and `__syslog_connection_*` are no longer promoted
  either.
- **Plan correction:** `R5`'s prescribed snippet renamed the fields to bare
  `severity` / `facility` / `app_name`. That was **wrong**. `labelmap` produces
  `message_`-prefixed names and the dashboards select on `message_severity` and
  `message_app_name` at 12 call sites across `build_dashboards.py`,
  `build_openwrt_mission_control.py`,
  `build_openwrt_operations_dashboard.py`, and the generated provisioning JSON;
  the bare form would have silently returned no data in every log panel. The
  plan has been corrected in place with the reasoning and the audit result.
  Because the promoted names match what consumers already query, **no
  generator, dashboard, or provisioning change was required** and no label the
  repo consumes was dropped.
- Validation: `python3 -m unittest discover -s tests -p 'test_*.py'` (15 tests,
  OK — 4 new), `sh -n openwrt/setup.sh openwrt/scripts/*.sh`,
  `docker compose config`, and `sh tests/run_all.sh` (ALL CHECKS PASSED) all
  passed; Lua checks ran, not skipped. `alloy fmt` inside `grafana/alloy:latest`
  parses the config (exit 0) and its only diff from the file is tabs-vs-spaces
  across the whole pre-existing file, so the repo's 2-space style was left
  alone rather than reformatted. The new test was confirmed to fail against
  both the previous config and the plan's bare-name snippet. No generated
  dashboard drift.
- Remaining risk: **static-only.** `R5`'s two live pre-checks (the Alloy
  `loki_source_syslog` metrics dump and `curl .../loki/api/v1/labels`) need a
  running stack and were not authorized, so the label set was enumerated from
  consumer grep and documented rfc3164 fields rather than observed from a live
  Loki — a label used only in an ad-hoc Explore query would not appear in that
  audit. On deploy, the label-set change ends existing streams and starts new
  ones; historical logs stay queryable under their old labels, so panels
  spanning the changeover may show a per-stream discontinuity. Documented in
  `docs/troubleshooting.md`. `R6`-`R13` and `M1`-`M2` remain unimplemented.

- Changed: Implemented Batch A of `docs/CODE-REVIEW-REMEDIATION-PLAN.md`
  (`R1`-`R4`), the four outright-breakage tasks, and ticked their checkboxes.
  - `R1` `docker-compose.yml`: the `alloy` service now passes
    `--server.http.listen-addr=0.0.0.0:12345` and
    `--storage.path=/var/lib/alloy/data` explicitly on the `exec` line, mounts a
    new `alloy-data` named volume at `/var/lib/alloy/data`, and binds the UI
    port to host loopback (`127.0.0.1:1234:12345`).
  - `R2` `openwrt/setup.sh`: legacy-crontab removal no longer aborts the
    installer when `grep -v` selects no lines, and cleans up the staged
    `$CRONTAB_FILE.clean`.
  - `R3` `openwrt/scripts/openwrt-monitor-client-traffic.sh`: `$ROWSFILE` is
    created unconditionally, so an empty nlbwmon result set writes fresh output
    instead of aborting and leaving the previous period's `.prom` in place.
  - `R4` `openwrt/scripts/openwrt-monitor-wan-quality.sh`: stages the temp file
    in `/tmp` instead of the textfile dir, adds an `EXIT` trap, and sweeps
    pre-existing `.prom.<pid>` leftovers once per run.
  - Tests: added `tests/test_wan_quality.sh` and
    `tests/test_setup_legacy_crontab.sh`, extended `tests/test_client_traffic.sh`
    with the empty-result-set case, and wired both new tests into
    `tests/run_all.sh`.
  - Docs: `docs/monitoring-host-setup.md` gained the Alloy UI reachability /
    loopback-binding explanation and the `alloy-data` WAL volume;
    `docs/CODE-REVIEW-FINDINGS.md` gained a `openwrt-monitor-wan-quality.sh`
    stub noting that script was outside its 2026-07-23 pass and pointing at R4.
- Reason: All four were data-loss or silently-wrong-metrics defects rather than
  hardening: an ephemeral remote_write WAL, an installer that dies mid-run on
  the upgrade path it exists to serve, a stale `.prom` reporting
  `collector_available 1` with a previous accounting period's counters, and a
  15-second-per-5-minute window exposing every WAN-quality series twice.
- Validation: `python3 -m unittest discover -s tests -p 'test_*.py'` (11 tests,
  OK), `sh -n openwrt/setup.sh openwrt/scripts/*.sh`, `docker compose config`,
  and `sh tests/run_all.sh` (ALL CHECKS PASSED) all passed. Lua checks **ran**,
  not skipped — `luac5.1` and `lua5.1` were both present. No generated dashboard
  drift: `run_all.sh` reported every dashboard `unchanged` and `git status`
  showed no JSON modifications. Each of the three new/extended tests was
  additionally run against the pre-fix script and confirmed to fail, so none is
  vacuous.
- Remaining risk: `R1` and `R2` are **static-only**. `R1`'s `curl` and
  `docker compose exec` checks need a running stack and `R2`'s real validation
  needs a router with a legacy crontab; neither was authorized, so the UI
  reachability and WAL persistence are confirmed by rendered configuration
  rather than observed behavior. `R1` also changes the Alloy UI from a
  nominally all-interfaces mapping to loopback-only — an operator who was
  reaching it remotely (they were not, since it never worked) would need the
  documented SSH tunnel. Batches B-E remain unimplemented.

- Changed: Added `docs/CODE-REVIEW-REMEDIATION-PLAN.md`, a prescriptive fix plan
  and task list (`R1`-`R13`, `M1`-`M2`) from a second full-repo review, and
  linked it from `docs/repository-map.md`.
- Reason: The 2026-07-23 `CODE-REVIEW-FINDINGS.md` is deliberately report-only.
  This pass found 13 further issues plus 2 metric gaps not covered there, and an
  implementing agent needs prescribed fixes, tests, and verification commands.
- Validation: Documentation only, no behavior change. `git diff --check`,
  `python3 -m unittest discover -s tests -p 'test_*.py'`,
  `sh -n openwrt/setup.sh openwrt/scripts/*.sh`, and
  `docker compose config --quiet` passed. Four claims in the plan were
  reproduced locally; commands and output are in its Appendix A.
- Remaining risk: All 15 tasks are unimplemented. `R1`'s original claim about
  the Alloy image `CMD` was wrong and is corrected in the plan — do not re-derive
  it from the first review summary.

- Changed: Added shared root agent instructions, bounded `.agent` handoff files,
  a repository map, an audit/plan for agent-readiness work, and a current README
  dashboard map.
- Reason: Reduce repeated discovery, centralize durable constraints, and make
  generated/source boundaries easier for coding agents to find.
- Validation: `python3 -m unittest discover -s tests -p 'test_*.py'`,
  `sh -n openwrt/setup.sh openwrt/scripts/*.sh`, `docker compose config --quiet`,
  `git diff --check`, and `sh tests/run_all.sh` passed.
- Remaining risk: Tracked `.env` contains private values and requires separate
  human-approved remediation.
