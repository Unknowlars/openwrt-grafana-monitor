# Current status

## Current goal

Work the remediation backlog in `docs/CODE-REVIEW-REMEDIATION-PLAN.md`
(tasks `R1`-`R13`, `M1`-`M2`) without changing metric names, label sets, or
dashboard UIDs except where a task explicitly says to.

Current pass: Batch A only (`R1`-`R4`) was re-verified against the source and
validated locally on 2026-07-25. No live stack start, router setup run, router
service restart, firewall change, commit, push, or staging was performed.

## Live deployment findings (2026-07-25, authorized read-only session)

Verified against both routers via the MCP sidecar plus direct reads of
`:9100/metrics` and the local stack's Prometheus/Loki APIs. **Nothing was
mutated.** These are deployment/config problems, not code defects, and none has
a task in the remediation plan yet.

1. **openwrt-new is not being scraped at all.** `.env` sets only
   `ROUTER_IP`/`ROUTER_NAME`, no `ROUTER_TARGETS`, so the legacy single-target
   fallback is active. Prometheus has exactly one target, `192.168.0.1:9100`,
   with `router="openwrt"`. `192.168.0.2` serves 1926 healthy samples that reach
   nothing.
2. **No syslog reaches Loki — zero `{job="openwrt-syslog"}` streams over 30
   days.** The routers point at `log_ip` `192.168.0.221` (main) and
   `192.168.0.223` (new); the monitoring host is `192.168.0.247`. Neither
   matches, and the two routers disagree with each other. Every log panel is
   empty in production. This also makes `R5` currently moot in practice, though
   still correct as a fix.
3. **Router identity is inconsistent across signals.** Metrics carry
   `router="openwrt"` (file-SD) while collectors emit `ap="OpenWrt"` (main) and
   `ap="openwrt-ap1"` (new) from the router hostname, and syslog would carry
   `router=` those same hostnames. A dashboard `$router` filter cannot match
   metrics and logs simultaneously. main's `log_hostname` is still the default
   `OpenWrt`.
4. **`conntrack` and `wifi_assoc_events` collectors report unavailable on both
   routers** (`openwrt_client_conntrack_collector_available 0`,
   `openwrt_wifi_assoc_events_collector_available 0`). So
   `openwrt_client_conntrack_entries` — the metric `R7` exists to cap — is not
   currently emitted anywhere. Fail-closed is working correctly; the dependency
   is missing. Matches `CODE-REVIEW-FINDINGS.md` §1.1 "conntrack-tools is never
   installed, but the clients profile depends on it". `filesystem_inode` is also
   0 on both.
5. **Flow offload is active on main** (`openwrt_flow_offload_enabled{mode="sw"} 1`
   and `{mode="hw"} 1`), so its per-client byte accounting is untrustworthy per
   the standing repo caveat. On openwrt-new the gauge emits **no series at all**
   while `openwrt_flow_offload_read_success 1` — absence rather than an explicit
   `0`, so a panel shows "No data" instead of "offload off".
6. **MCP sidecar defects found incidentally** (all read-only tools):
   - `openwrt_diagnostic wifi` returns the **WPA passphrase in cleartext** in
     `config.key`. It should be redacted; this is a credential-disclosure bug in
     an allowlisted read-only tool.
   - `openwrt_ssh_check` reports `ok:false` / exit 127 on healthy routers
     because it runs `hostname`, which does not exist on these images
     (`ash: hostname: not found`) even though `ssh_ok=1`.
   - `openwrt_metrics_sample`, `openwrt_diagnostic collector_success`, and
     `monitoring_status`'s "Metrics health sample" all return **empty with
     `ok:true`** while the endpoint serves 231 KB when fetched directly. Likely
     fetching `127.0.0.1:9100` when `listen_interface='lan'` binds the LAN
     address; either way it fails silently instead of erroring.
7. Live exposition is clean: `check_exposition.py --url` reports no duplicate
   series on either router (2806 and 1926 samples).

## Confirmed findings

- Two open review documents, both current:
  - `docs/CODE-REVIEW-FINDINGS.md` — 2026-07-23, report-only, ~120 findings.
  - `docs/CODE-REVIEW-REMEDIATION-PLAN.md` — 2026-07-25, prescriptive, 15 tasks.
    Excludes everything in the first document; neither supersedes the other.
- Four issues are outright breakage, not hardening (Batch A): Alloy's WAL is
  ephemeral because the compose `command` override drops the image's
  `--storage.path`; `setup.sh` dies under `set -eu` on the legacy-crontab
  upgrade path; `openwrt-monitor-client-traffic.sh` leaves a stale `.prom`
  reporting `available 1` when nlbwmon returns no rows; and
  `openwrt-monitor-wan-quality.sh` stages a partial exposition file inside
  `/var/prometheus` for 15+ seconds per run.
- The Alloy UI port mapping (`1234:12345`) has never worked — Alloy's default
  `--server.http.listen-addr` is `127.0.0.1:12345` and nothing sets it. This is
  pre-existing, **not** caused by the `command` override.
- `.env` is tracked and contains non-placeholder private values. Do not copy
  values from it into docs, prompts, logs, or agent files.
- README dashboard count/tree lags the current generators and provisioning
  outputs.

## Completed

- Earlier task (agent readiness): root `AGENTS.md`, simplified `CLAUDE.md`,
  `.agent/` handoff files, `docs/repository-map.md`, `.gitignore` fix so
  validation files under `tests/` are visible, README dashboard map.
- Added `docs/CODE-REVIEW-REMEDIATION-PLAN.md` and linked it from
  `docs/repository-map.md`.
- **Batch A (`R1`-`R4`) implemented and statically validated, uncommitted in the
  worktree.** All four plan anchors were re-verified against `HEAD` (`92d5fa7`,
  one commit past the plan's `205c17a` basis — no source files changed between
  them) and none contradicted the plan.
  - `R1` — `docker-compose.yml`: `--server.http.listen-addr=0.0.0.0:12345` and
    `--storage.path=/var/lib/alloy/data` passed explicitly on the `exec` line,
    new `alloy-data` named volume, UI port re-bound to `127.0.0.1:1234:12345`
    (the plan's preferred option, since the UI has no auth).
  - `R2` — `openwrt/setup.sh`: `|| true` on the inverted `grep`, plus an
    unconditional `rm -f "$CRONTAB_FILE.clean"` for the stray staged file. The
    `mv` is deliberately **not** guarded on `[ -s ... ]`.
  - `R3` — `openwrt-monitor-client-traffic.sh`: `: > "$ROWSFILE"` after the
    trap. Chosen semantics for an empty accounting period: `available 1` with
    zero per-client series, stated in a comment at the creation site.
  - `R4` — `openwrt-monitor-wan-quality.sh`: stages in `/tmp`, added
    `trap 'rm -f "$TMPFILE"' EXIT` and a one-time
    `rm -f "$OUTFILE".[0-9]*` self-healing sweep.
  - New tests: `tests/test_wan_quality.sh`, `tests/test_setup_legacy_crontab.sh`
    (extracts the real block out of `setup.sh` rather than copying it — there is
    still no general `setup.sh` harness). Extended
    `tests/test_client_traffic.sh` with the empty-result-set case. All three
    were confirmed to **fail** against the pre-fix scripts before being kept.
    Both new tests are wired into `tests/run_all.sh`.
  - Validation refreshed this pass: targeted R2/R3/R4 shell tests passed;
    `python3 -m unittest discover -s tests -p 'test_*.py'` ran 15 tests OK;
    `sh -n openwrt/setup.sh openwrt/scripts/*.sh`, `docker compose config`, and
    `sh tests/run_all.sh` all passed. `run_all.sh` reported every generated
    dashboard copy unchanged and byte-identical; Lua syntax/runtime checks ran
    because `luac5.1` and `lua5.1` were present.

- **`R5` implemented (Batch B), uncommitted.** `alloy/config.alloy`: the
  wildcard `labelmap` over `__syslog_(.+)` is replaced by an explicit
  three-rule allowlist, so `__syslog_message_proc_id` (a PID) is no longer a
  Loki stream label. `__syslog_message_msg_id`, `message_hostname` (redundant
  with `router`), and `__syslog_connection_*` also stop being promoted.
  - **The plan's prescribed snippet for `R5` was wrong and has been corrected
    in place.** It renamed the fields to bare `severity` / `facility` /
    `app_name`, but `labelmap` yields the `message_`-prefixed names and the
    dashboards select on `message_severity` / `message_app_name` — 12 call
    sites across three generators plus provisioning JSON. The bare form would
    have returned no data in every log panel, silently. The `message_` prefix
    is load bearing; the promoted names are unchanged from what consumers
    already query, so **no dashboard or generator edit was needed.**
  - New `tests/test_alloy_syslog_labels.py` ties the two halves together: no
    `labelmap`/PID promotion, and every `message_*` label any generator selects
    on must actually be promoted. Confirmed to fail against both the old config
    and the plan's bare-name snippet.
  - `docs/troubleshooting.md` gained a "Syslog stream labels" subsection with
    the promoted set, the PID rationale, and the stream-identity upgrade note.

- **`R6` implemented (Batch B), uncommitted.** SSID label values are now
  sanitized on every path with one shared character class.
  - Applied at each collector's **single iface-build source**
    (`client_inventory.lua` `wifi_ifaces()`, `topology.lua` equivalent) rather
    than at the individual use sites the plan named. `topology.lua` builds ssid
    ids at five places, not the two listed; patching only those would have made
    its node ids and edge ids disagree with each other.
  - `wifi_dethrash.lua` gained a `sanitize()` helper (it had none) applied to
    both SSID sources. The UCI one at `:83` **had** to be included: `:86`
    compares it against the iwinfo value to resolve `ifname`, so sanitizing one
    side only would break that match for any SSID containing a space. Also
    sanitized the usteer SSID feeding the `ap` label — same defect, not named in
    the plan, `"<router>/<ssid>"` shape preserved.
  - **No shell change was needed and none was made.** The plan's step 3 said to
    adjust the `tr -c` class; the classes were already identical (verified
    byte-for-byte over 8 inputs including multi-byte UTF-8). Editing it would
    have *introduced* a divergence. Plan corrected in place.
  - `tests/fixtures/wireless_status.json` was already shared by all three
    affected tests, so a `wlan3` named `Lab Net"5G` was added there; both
    `test_client_conntrack.sh` and `test_client_inventory.lua` now assert
    `Lab_Net_5G` with cross-reference comments so the pair cannot drift.
    Confirmed to fail pre-fix, with the raw value rendering as the corrupt
    `ssid="Lab Net"5G"`. `test_topology.lua`'s *printed* count moved 11/10 →
    12/11 (new SSID node + radio edge); it is reported, not asserted.
  - `docs/advanced-profiles.md` gained an "SSID label values are sanitized"
    subsection with the upgrade note.

- **`R7` implemented (Batch B), uncommitted.**
  `openwrt-monitor-client-conntrack.sh` now sources
  `/etc/openwrt-grafana-monitor.conf`, adds `CLIENT_CONNTRACK_MAX` with default
  256, validates the knob, caps emitted `openwrt_client_conntrack_entries`
  series after sorting by descending count, and emits
  `openwrt_client_conntrack_truncated` on successful scrapes. The final
  exposition is still sorted by MAC for deterministic output.
  - `tests/test_client_conntrack.sh` now builds a `CLIENT_CONNTRACK_MAX + 10`
    host fixture with a small cap, asserts exactly the capped number of
    conntrack series, asserts the busiest clients survive, and asserts the
    idle/lower-count tail is dropped with truncation set to 1.
  - `docs/advanced-profiles.md` documents `CLIENT_CONNTRACK_MAX` next to the
    inventory cap. The related topology cap mentioned in the R7 cross-reference
    was left open; it is a different file/task class and was not folded into
    this change.

## Next action

`R8` is the next unchecked remediation task. It touches `openwrt/setup.sh` and
cannot be fully live-validated without an authorized router setup run; keep it
static-only unless the operator explicitly authorizes live setup/service
actions.

## Blockers

- `R8` and `R11` cannot be fully validated without a router. Per `AGENTS.md`,
  `setup.sh` runs, service restarts, and firewall changes are not routine
  validation and need explicit operator authorization plus `confirm=true`.
  Report them as static-only until then.
- `R2` is landed but **static-only**: the extracted-block test proves the
  `grep -v` exit-1 path no longer aborts, but a real upgrade run on a router
  with a legacy crontab has not been done and was not authorized.
- `R5`'s two live pre-checks (Alloy's `loki_source_syslog` metrics dump and
  `curl http://localhost:3100/loki/api/v1/labels`) were **not** run — no
  authorized stack. The promoted label set was derived from consumer grep plus
  `loki.source.syslog`'s documented rfc3164 fields, not observed from a live
  Loki. A label used only in an operator's ad-hoc Explore query would not have
  shown up in that audit. Worth running the `/labels` query around deploy.
- `R1` is landed but **static-only**: `docker compose config` renders as
  intended (`host_ip: 127.0.0.1` on 1234, `alloy-data` mounted at
  `/var/lib/alloy/data`). The plan's `curl http://localhost:1234/` and
  `docker compose exec alloy ls /var/lib/alloy/data` checks need a running
  stack and were not run — starting one was not authorized. **These are the
  checks that would actually confirm the UI is reachable and the WAL persists;
  until they run, R1 is verified by configuration only.**
- Secret remediation for tracked `.env` needs human approval and likely
  credential rotation.
- Human review still pending on whether to stage the newly visible validation
  files under `tests/`.

## Relevant files

- `docs/CODE-REVIEW-REMEDIATION-PLAN.md`
- `docs/CODE-REVIEW-FINDINGS.md`
- `docker-compose.yml`, `alloy/config.alloy`
- `openwrt/setup.sh`
- `openwrt/scripts/openwrt-monitor-client-traffic.sh`
- `openwrt/scripts/openwrt-monitor-wan-quality.sh`
- `openwrt/scripts/openwrt-monitor-client-conntrack.sh`
- `openwrt/scripts/openwrt-monitor-wan-info.sh`
- `mcp_server/core.py`, `mcp_server/server.py`
