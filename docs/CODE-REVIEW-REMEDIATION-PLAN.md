# OpenWrt Grafana Monitor — Remediation Plan (2026-07-25 review)

> **Audience:** the agent that will implement these fixes.
>
> **This document is prescriptive.** Unlike `docs/CODE-REVIEW-FINDINGS.md`
> (report-only, 2026-07-23, ~120 findings), every task below states a concrete
> fix, the tests to add, and the command that proves it. Deviate only if the
> source contradicts the description — in which case correct this document
> rather than silently doing something else.
>
> **Scope:** findings from the 2026-07-25 full-repo pass. Every issue already
> present in `docs/CODE-REVIEW-FINDINGS.md` was deliberately excluded, so
> **this document does not supersede that one** — both are open work. Where an
> issue here is a sibling of one there, the cross-reference is noted.
>
> **Review basis:** `main` at `205c17a`, level with `gitea/main`, clean worktree
> apart from untracked `.opencode/` and `graphify-out/`. There was no PR diff;
> this was a whole-repo read.
>
> **Verification status:** every `file:line` anchor in this document was
> re-checked against the source, and the four claims marked **[reproduced]**
> were executed locally. See [Appendix A](#appendix-a--reproductions) for the
> exact commands and output. One claim from the original review was **wrong and
> has been corrected** — see [R1](#r1-p0-alloy-loses-its-wal-on-every-restart-and-the-ui-port-mapping-cannot-work).

---

## How to read this document

Severity tags match `docs/CODE-REVIEW-FINDINGS.md`:

- `[P0]` — likely-correctness bug, data loss, or silently-wrong metrics.
- `[P1]` — real but bounded issue.
- `[P2]` — robustness / polish.
- `[P3]` — enhancement.

Each task has a stable ID (`R1`…`R13`, `M1`…`M2`). Reference the ID in commit
messages and in `.agent/CHANGELOG.md` so partial progress is legible.

Repo conventions these fixes must respect (from `AGENTS.md` and
`docs/client-topology-and-netflow-plan.md` §14):

- **Fail closed.** A missing dependency emits `..._available 0`, never a
  plausible zero and never stale-but-healthy output.
- **Stage temp files outside the textfile dir**, then atomic `mv`.
- Lowercase MACs everywhere.
- POSIX shell / BusyBox only in `openwrt/`; Lua 5.1 only in collectors.
- Do not change metric names, label sets, or dashboard UIDs unless a task says
  to.

---

## Task list

Batches are ordered by dependency and blast radius. Batch A is outright
breakage; do it first. Tasks within a batch are independent and may be done in
any order or in parallel.

### Batch A — outright breakage

- [x] **R1** `[P0]` Alloy loses its `prometheus.remote_write` WAL on every restart; UI port mapping cannot work — `docker-compose.yml:36-113`
- [x] **R2** `[P0]` `setup.sh` aborts mid-install on the legacy-crontab upgrade path it exists to serve — `openwrt/setup.sh:411-415` **[reproduced]**
- [x] **R3** `[P0]` Empty nlbwmon result set leaves a stale `.prom` reporting `available 1` — `openwrt/scripts/openwrt-monitor-client-traffic.sh:159` **[reproduced]**
- [x] **R4** `[P0]` `wan-quality` stages a partial exposition file inside the textfile dir for 15+ seconds — `openwrt/scripts/openwrt-monitor-wan-quality.sh:8`

### Batch B — cardinality and label correctness

- [x] **R5** `[P0]` Syslog PID promoted to a Loki stream label — unbounded stream cardinality — `alloy/config.alloy:78-81`
- [x] **R6** `[P1]` SSID label values diverge between the shell helper and the Lua collectors — `openwrt/scripts/openwrt-monitor-client-conntrack.sh:143`
- [x] **R7** `[P1]` `openwrt_client_conntrack_entries` has no cardinality cap — `openwrt/scripts/openwrt-monitor-client-conntrack.sh:117`

### Batch C — installer and staging robustness

- [ ] **R8** `[P1]` nlbwmon init script and `protocols` dir used unconditionally after install failure was tolerated — `openwrt/setup.sh:359,367-369`
- [ ] **R9** `[P2]` `wan-info` stages inside the textfile dir with no trap — `openwrt/scripts/openwrt-monitor-wan-info.sh:10`
- [ ] **R10** `[P2]` `$ssid` not reset per interface iteration — roams may be attributed to the wrong SSID — `openwrt/scripts/openwrt-monitor-client-conntrack.sh:141`

### Batch D — MCP sidecar

- [ ] **R11** `[P1]` `AutoAddPolicy` accepts any SSH host key while sending the router root password — `mcp_server/server.py:192`
- [ ] **R12** `[P2]` 180 s timeout on `openwrt_run_repo_setup` is shorter than a real setup run — `mcp_server/core.py:222`
- [ ] **R13** `[P2]` MCP accepts a profile combination `setup.sh` always rejects — `mcp_server/core.py:21,228-238`

### Batch E — new metrics

- [ ] **M1** `[P2]` Global conntrack table saturation is not collected at all
- [ ] **M2** `[P2]` WiFi airtime / channel utilization and per-station error counters are not collected

---

# Batch A — outright breakage

## R1 `[P0]` Alloy loses its WAL on every restart, and the UI port mapping cannot work

**Anchor:** `docker-compose.yml:36-113` (the `entrypoint` / `command` override;
`exec /usr/bin/alloy run /etc/alloy/config.alloy` is the last line of the
inline script at `:113`).

**Correction to the original review.** The first pass claimed the image's `CMD`
sets `--server.http.listen-addr=0.0.0.0:12345` and that the override drops it.
That is **false**. Verified against the image:

```
Entrypoint: ["/bin/alloy"]
Cmd:        ["run","/etc/alloy/config.alloy","--storage.path=/var/lib/alloy/data"]
```

So there are **two separate defects**, with different causes:

1. **WAL is ephemeral — caused by the override.** The image `CMD` does set
   `--storage.path=/var/lib/alloy/data`. The `command:` override replaces
   `CMD` wholesale and does not pass it, so Alloy falls back to its default
   `--storage.path` of `data-alloy/` — *relative to the working directory*,
   verified from `alloy run --help`. The `prometheus.remote_write "lgtm"` WAL
   therefore lives on the container's ephemeral filesystem, and every
   `docker compose up -d` / `restart` silently discards samples that had not
   yet been flushed to `otel-lgtm`.
2. **The Alloy UI has never been reachable — pre-existing, not caused by the
   override.** Alloy's default `--server.http.listen-addr` is
   `127.0.0.1:12345` (verified from `--help`). Neither the image `CMD` nor the
   override ever set it, so Alloy binds container-loopback only. The
   `"1234:12345"` mapping at `docker-compose.yml:30` and its inline comment
   `# Alloy UI (http://localhost:1234)` describe something that cannot work.

**Also note:** restoring `--storage.path=/var/lib/alloy/data` alone does *not*
make the WAL durable, because `/var/lib/alloy` is not a volume in this compose
file — the `alloy` service mounts only `./alloy/config.alloy:ro`. Both halves
are needed.

**Fix.** In the `alloy` service:

1. Change the final line of the inline script to pass both flags explicitly:
   ```sh
   exec /usr/bin/alloy run \
     --server.http.listen-addr=0.0.0.0:12345 \
     --storage.path=/var/lib/alloy/data \
     /etc/alloy/config.alloy
   ```
2. Add a named volume so the WAL survives recreation:
   ```yaml
   volumes:
     - ./alloy/config.alloy:/etc/alloy/config.alloy:ro
     - alloy-data:/var/lib/alloy/data
   ```
   and declare `alloy-data:` alongside the existing `lgtm-data:` in the
   top-level `volumes:` block.
3. Add a short comment above the `exec` explaining that the flags are passed
   explicitly *because* overriding `command` discards the image's `CMD` — this
   is the exact trap the next person will fall into.

**Do not** switch to `entrypoint: ["/bin/alloy"]` + a flag-only `command`. The
inline script generates `/tmp/openwrt-targets.json` for file-SD discovery and
must keep running before `alloy run`.

**Verification.**

```sh
docker compose config --quiet
docker image inspect grafana/alloy:latest --format '{{json .Config.Cmd}}'
docker compose up -d alloy
docker compose logs alloy | head -30           # expect no storage.path warning
curl -sf -o /dev/null -w '%{http_code}\n' http://localhost:1234/   # expect 200
docker compose exec alloy ls /var/lib/alloy/data                   # expect WAL dirs
```

The `curl` and `exec` checks are live-system evidence — report them separately
from static validation, per `AGENTS.md`.

**Risk:** binding the Alloy UI on `0.0.0.0:12345` inside the container exposes
it on the host via the existing `1234:12345` mapping, with no authentication.
If the monitoring host is not on a trusted network, bind the host side instead:
`"127.0.0.1:1234:12345"`. Prefer that — it matches how
`openwrt-ssh-mcp` is already bound at `docker-compose.yml:132`. Mention the
choice in the operator doc.

**Docs to update:** `docs/monitoring-host-setup.md` (Alloy UI reachability and
the new volume), and the port comment in `docker-compose.yml`.

---

## R2 `[P0]` `setup.sh` aborts mid-install on the legacy-crontab upgrade path **[reproduced]**

**Anchor:** `openwrt/setup.sh:411-415`

```sh
if grep -qE 'openwrt-grafana-monitor-(metrics|sqm)' "$CRONTAB_FILE" 2>/dev/null; then
  grep -vE 'openwrt-grafana-monitor-(metrics|sqm)' "$CRONTAB_FILE" > "$CRONTAB_FILE.clean"
  mv "$CRONTAB_FILE.clean" "$CRONTAB_FILE"
  log "    removed superseded cron entries"
fi
```

**Root cause.** `grep -v` exits **1** when it selects no lines. If
`/etc/crontabs/root` contains *only* the two legacy
`openwrt-grafana-monitor-(metrics|sqm)` lines, the inverted match selects
nothing, `grep -vE` exits 1, and `setup.sh` runs under `set -eu` — so the
script dies right there. **[Reproduced](#a2--r2-legacy-crontab-abort): exit 1,
`REACHED_END` never printed.**

**Impact.** This branch exists specifically for routers upgrading from the
pre-split version. On the narrow-but-real case where the legacy lines are the
whole crontab, that router gets: collectors copied, `/var/prometheus` wiped
(`:419`), and then **nothing** — no exporter listener configured, no cron jobs
installed, no cron log level, no remote syslog — and no error message
explaining why. It looks like a successful partial install.

A second, worse variant: `$CRONTAB_FILE.clean` is written *before* the `mv`, so
an abort here can also leave a stray `/etc/crontabs/root.clean` behind.

**Fix.**

```sh
if grep -qE 'openwrt-grafana-monitor-(metrics|sqm)' "$CRONTAB_FILE" 2>/dev/null; then
  grep -vE 'openwrt-grafana-monitor-(metrics|sqm)' "$CRONTAB_FILE" \
    > "$CRONTAB_FILE.clean" || true
  mv "$CRONTAB_FILE.clean" "$CRONTAB_FILE"
  log "    removed superseded cron entries"
fi
```

`|| true` is sufficient and correct: an empty `.clean` file is the *right*
result when every line was a legacy line, and `mv` of an empty file yields an
empty crontab, which is exactly what the subsequent cron-install block expects.
Do **not** guard the `mv` on `[ -s "$CRONTAB_FILE.clean" ]` — that would skip
the removal in precisely the case this fix is about, leaving the legacy lines
in place and reintroducing the duplicate-series problem the comment at
`:395-398` describes.

Add `rm -f "$CRONTAB_FILE.clean"` to whatever cleanup path exists, or write to
`/tmp` and `mv` across — check whether `/tmp` and `/etc/crontabs` are the same
filesystem on the target before choosing, since `mv` across filesystems is not
atomic. On OpenWrt they are not (`/tmp` is tmpfs), so **keep staging alongside
the crontab** and just clean up on failure.

**Cross-reference:** `docs/CODE-REVIEW-FINDINGS.md` §1.1 already flags the
broader "`set -e` + unguarded command" pattern in `setup.sh`. This is a
concrete instance with a confirmed reproduction; fixing the general pattern
would subsume it, but fix this one regardless.

**Test to add.** There is no test harness for `setup.sh` today. Add
`tests/test_setup_legacy_crontab.sh` following the shape of
`tests/test_client_traffic.sh`: create a temp file containing only the two
legacy lines, run the extracted removal logic against it, and assert exit 0
with an empty result. If extracting the block cleanly is not practical, at
minimum add the case to `tests/fixtures/` and assert `sh -n` plus a comment
pointing at this task.

**Verification.**

```sh
sh -n openwrt/setup.sh
sh tests/test_setup_legacy_crontab.sh
```

---

## R3 `[P0]` Empty nlbwmon result set leaves a stale `.prom` reporting `available 1` **[reproduced]**

**Anchor:** `openwrt/scripts/openwrt-monitor-client-traffic.sh:17,23,130,159,163`

**Root cause.** `ROWSFILE` is declared at `:17` and registered in the `EXIT`
trap at `:23`, but it is **only created by the append at `:130`**, inside the
per-record loop. When the validated `data` array is empty the loop body never
runs, the file never exists, and the `awk -F '\t' … "$ROWSFILE"` at `:159`
fails with `cannot open file` — **[reproduced](#a3--r3-awk-missing-rowsfile):
awk exits 2**. Under `set -e` the script dies before the `mv "$TMPFILE"
"$OUTFILE"` at `:163`.

**When the result set is legitimately empty:** fresh install before nlbwmon's
first commit; immediately after an accounting-period rollover; right after a
manual `nlbw -c commit`.

**Impact — this is the inverse of fail-closed.** The previous run's
`openwrt_client_traffic.prom` stays in `/var/prometheus`, so the exporter keeps
serving `openwrt_client_traffic_collector_available 1` together with the
**previous accounting period's** per-client byte and packet counters,
indefinitely, until a run happens to produce rows again. The one code path that
should report unavailable instead reports healthy stale data — and because the
values are plausible, nothing on the dashboard indicates a problem. Contrast
with `fail_closed()` at `:36`, which the script uses correctly everywhere else.

**Fix.** Create the file unconditionally, immediately after the trap is
installed at `:23`:

```sh
: > "$ROWSFILE"
```

An empty `ROWSFILE` makes `awk` produce no sample lines, so the output becomes
headers plus `openwrt_client_traffic_collector_available 1` with zero
per-client series — which is the honest representation of "collector works,
nlbwmon has no data this period". Confirm that reading is what the dashboard
should show; if the intended semantics for "no data yet" is
`..._available 0`, call `fail_closed` instead when `ROWSFILE` is empty. **Pick
one and state it in the script's header comment** — the current behavior is
neither.

Recommended: emit `available 1` with no series. An empty accounting period is
not a collector failure, and `available 0` would make a working collector look
broken every period rollover.

**Test to add.** Extend `tests/test_client_traffic.sh` with a fixture whose
`nlbw -c json` output has an empty `data` array. Assert: exit 0, output file
written, `openwrt_client_traffic_collector_available 1` present, and **no**
`openwrt_client_bytes_total` lines. Also assert the output file was
*overwritten* — seed `$OPENWRT_MONITOR_TEXTFILE_DIR` with a previous-period
file first and confirm its series are gone.

**Verification.**

```sh
sh tests/test_client_traffic.sh
sh tests/run_all.sh
```

---

## R4 `[P0]` `wan-quality` stages a partial exposition file inside the textfile dir

**Anchor:** `openwrt/scripts/openwrt-monitor-wan-quality.sh:8`

```sh
OUTDIR="${OPENWRT_MONITOR_TEXTFILE_DIR:-/var/prometheus}"
OUTFILE="$OUTDIR/openwrt_wan_quality.prom"
TMPFILE="$OUTFILE.$$"
```

**Root cause.** `TMPFILE` is inside `OUTDIR`. Every sibling helper explicitly
refuses to do this and says why in a comment block — see
`openwrt-monitor-filesystem.sh:8-12`, `openwrt-monitor-sqm.sh:8-11`,
`openwrt-monitor-firewall-counters.sh:8-11`.

> **Live correction (2026-07-25, authorized read-only router session).** The
> duplicate-series claim below is **false on this deployment, and the `[P0]`
> severity is wrong — this is housekeeping, not silently-wrong metrics.**
>
> Measured against `openwrt-main` (OpenWrt 25.12.5 r33051, ASUS RT-AX53U,
> ramips/mt7621) running the **pre-fix** script: 144 polls of
> `http://192.168.0.1:9100/metrics` at 1-second resolution over 340 s captured
> one confirmed `wan-quality` run (detected by the gateway jitter value
> changing, at 11:10:12) and found **zero duplicated series** at any point. An
> earlier 96-poll/2 s pass agreed. `tests/check_exposition.py --url` against
> both routers also reported no duplicates (2806 and 1926 samples).
>
> The negative result is airtight rather than merely absent: the exporter
> demonstrably *does* read `.prom` textfiles, because `openwrt_wan_probe_*`
> reaches the exposition only through that path — yet during the ~13 s window
> when `openwrt_wan_quality.prom.<pid>` provably existed beside it, nothing was
> duplicated. Therefore the textfile collector globs **`*.prom` only** and never
> reads the staged `.prom.<pid>`. `docs/openwrt-setup.md:280` independently
> describes the source as `/var/prometheus/*.prom`.
>
> **What is actually wrong** is the narrower P2 the section already notes at the
> end: no `trap` and no sweep, so a crash, reboot, or `killall` mid-run strands
> a partial file in a tmpfs directory permanently. The fix as prescribed is
> still correct and was applied unchanged — staging outside the textfile dir
> matches every sibling helper and does not depend on the exporter's glob
> staying as it is.
>
> **This invalidates the same reasoning elsewhere.** The identical
> "scraped as a second copy" claim appears in the pre-existing comments at
> `openwrt-monitor-filesystem.sh:8-12`, `openwrt-monitor-sqm.sh:8-11`,
> `openwrt-monitor-firewall-counters.sh:8-11`, and
> `openwrt-monitor-client-traffic.sh:22`, and in `R9` below. Those were left
> untouched (out of scope) but are wrong for the same reason. Re-check before
> citing any of them as motivation.

**Why this file is the worst offender.** The header block is written at `:148`
and the `mv` is at `:159`, with up to three serial `ping -c 5` runs in between.
That is a **15-second-plus window, every 5 minutes**, during which
`/var/prometheus` holds both `openwrt_wan_quality.prom` and
`openwrt_wan_quality.prom.<pid>` — the latter with complete `# HELP` / `# TYPE`
lines and a partial sample set. `prometheus-node-exporter-lua`'s textfile
collector globs the directory, so both are read: every
`openwrt_wan_probe_*`, `dns_probe_*`, and `gateway_packet_loss` series is
exposed twice. Prometheus keeps the first sample, drops the second, and reports
**no scrape error** — the duplication is invisible from the dashboard.

There is also **no `trap`** and no leftover sweep, so a crash, a reboot, or a
`killall` mid-run leaves the duplicate `.prom.<pid>` in place permanently.

**Fix.** Three parts, all required:

1. Stage outside the textfile dir, matching the sibling helpers:
   ```sh
   TMPFILE="/tmp/.openwrt-monitor-wan-quality.$$"
   ```
   Note `/tmp` is tmpfs and `/var/prometheus` is typically also tmpfs on
   OpenWrt — confirm they are the same filesystem on the target so the `mv`
   stays atomic. `openwrt-monitor-client-traffic.sh:17` already stages in
   `/tmp` and `mv`s into `/var/prometheus`, so this pattern is established in
   the repo; follow it.
2. Add the trap, matching `openwrt-monitor-client-traffic.sh:23`:
   ```sh
   trap 'rm -f "$TMPFILE"' EXIT
   ```
3. Sweep pre-existing leftovers from the buggy version, once, near the top:
   ```sh
   rm -f "$OUTFILE".[0-9]*
   ```
   Without this, routers already running the current code keep their stale
   duplicate forever — the fix would not be self-healing. Add a brief comment
   saying this line exists to clean up after the pre-fix staging bug and can be
   removed once no deployed router predates the fix.

Copy the explanatory comment block from `openwrt-monitor-filesystem.sh:8-12`
so the next reader sees the same reasoning here.

**Note:** `docs/CODE-REVIEW-FINDINGS.md` has **no section for this script at
all**. Add one, or at minimum note in that document that
`openwrt-monitor-wan-quality.sh` was reviewed on 2026-07-25 and its findings
live here.

**Test to add.** A test asserting that after a run, `ls $OUTDIR` contains
exactly `openwrt_wan_quality.prom` and no `*.prom.*`. Better: assert
`tests/check_exposition.py` reports no duplicate series across the whole
directory — that is the property that actually matters, and it generalizes to
R9 and to the already-documented staging bugs.

**Verification.**

```sh
sh -n openwrt/scripts/openwrt-monitor-wan-quality.sh
python3 tests/check_exposition.py <output dir>   # confirm flag/usage first
sh tests/run_all.sh
```

---

# Batch B — cardinality and label correctness

## R5 `[P0]` Syslog PID promoted to a Loki stream label

**Anchor:** `alloy/config.alloy:78-81`

```
rule {
  action = "labelmap"
  regex  = "__syslog_(.+)"
}
```

**Root cause.** `loki.source.syslog` sets `__syslog_message_proc_id`. For
OpenWrt `logd` rfc3164 frames (`tag[pid]: message`) that field is the
**process PID**. The unfiltered `labelmap` promotes every `__syslog_*` field to
a public Loki label, so `message_proc_id` becomes a **stream label**.

**Impact.** Every restart of `dnsmasq`, `hostapd`, `netifd`, or `odhcpd` mints
a brand-new Loki stream. dnsmasq restarts on any DHCP or UCI change — and
`setup.sh:544` restarts it too, so running the installer alone rotates the
label. This is unbounded stream cardinality on the log path, the exact class of
problem the metrics path already guards against at `alloy/config.alloy:52-60`.
Loki stream churn degrades ingestion and query performance and inflates the
index far more than high-cardinality *log content* would.

`__syslog_message_msg_id` has the same problem in principle and is equally
useless as a stream label.

**Fix.** Replace the wildcard `labelmap` with an explicit rename list. Keep
only fields that are genuinely low-cardinality and useful for stream selection
— `severity`, `facility`, `app_name`:

> **Correction (applied 2026-07-25).** The snippet originally printed here
> renamed these to **bare** `severity` / `facility` / `app_name`. That is
> **wrong and would have silently broken every log panel in the repo.** A
> `labelmap` over `__syslog_(.+)` yields label names *with* the `message_`
> prefix (`__syslog_message_severity` → `message_severity`), and that is what
> the dashboards already select on — 12 call sites across
> `build_dashboards.py`, `build_openwrt_mission_control.py`,
> `build_openwrt_operations_dashboard.py`, and the generated provisioning JSON.
> Renaming to the bare form returns no data in all of them, with no error. The
> `message_` prefix is load bearing; keep it. Verified by grep before the edit,
> and `tests/test_alloy_syslog_labels.py` now fails against the bare-name
> variant.

```
rule {
  source_labels = ["__syslog_message_severity"]
  regex         = "(.+)"
  target_label  = "message_severity"
}
rule {
  source_labels = ["__syslog_message_facility"]
  regex         = "(.+)"
  target_label  = "message_facility"
}
rule {
  source_labels = ["__syslog_message_app_name"]
  regex         = "(.+)"
  target_label  = "message_app_name"
}
```

The `regex = "(.+)"` guard on each rule mirrors the `router` rule below, so a
frame missing the field is left without the label rather than getting an
empty-string one.

**Actual consumer audit (run 2026-07-25, before editing).** Only
`message_severity` and `message_app_name` are selected anywhere.
`message_proc_id`, `message_msg_id`, `message_facility`, `message_hostname`,
and `connection_*` have **no** consumers in the generators, provisioning
dashboards, or `grafana/provisioning/alerting/`. (The `severity:` keys in
`openwrt-alerts.yaml` are Grafana alert annotation labels, unrelated to the
syslog stream label.) `message_facility` was kept anyway — it is bounded and
already present, so dropping it would be an extra unrequested behavior change.
`message_hostname` is dropped as redundant: it already becomes `router`.
`__syslog_connection_*` (set per TCP connection) also stops being promoted.

Keep the existing `__syslog_message_hostname` → `router` rule at `:84-88`
exactly as-is, including its `regex = "(.+)"` non-empty guard and its comment —
that rule is correct and load-bearing for multi-router setups.

**Before changing this, enumerate what the current config actually promotes**
so no in-use label silently disappears:

```sh
docker compose exec alloy sh -c 'wget -qO- http://127.0.0.1:12345/metrics' \
  | grep loki_source_syslog
```

or query Loki for the current label set:

```sh
curl -s 'http://localhost:3100/loki/api/v1/labels' | tr ',' '\n'
```

Then grep the dashboard builders for any dependency:

```sh
rg 'message_proc_id|message_msg_id|app_name|facility|severity' \
  build_dashboards.py build_openwrt_*.py grafana/provisioning/
```

If a dashboard or alert selects on a label you are about to drop, keep it as a
**structured metadata** field or leave it in the log line rather than as a
stream label.

Note the grep above is too loose to act on directly: bare `severity` matches the
unrelated `severity:` annotation keys in
`grafana/provisioning/alerting/openwrt-alerts.yaml`, and bare `app_name` matches
nothing that is actually a syslog selector. Search for the **prefixed** forms
(`message_severity`, `message_app_name`, …) to get the real consumer list.

**Status (2026-07-25): implemented.** The static consumer audit was run and is
summarized above. The two live pre-checks in this section — the Alloy
`loki_source_syslog` metrics dump and the Loki `/labels` query — were **not**
run; both need a running stack, which was not authorized. Consequence: the
label set was enumerated from `loki.source.syslog`'s documented rfc3164 fields
and from consumer grep, **not** observed from a live Loki. If a label is in use
that appears nowhere in the repo (an operator's ad-hoc saved query, an
Explore bookmark), this change would break it silently. Worth running
`curl -s 'http://localhost:3100/loki/api/v1/labels'` against the live stack
before or shortly after deploying.

**Docs to update:** `docs/monitoring-host-setup.md` and
`docs/troubleshooting.md` if either documents the syslog label set. — Done:
neither documented it, so a new "Syslog stream labels" subsection was added to
`docs/troubleshooting.md` recording the promoted set and the stream-identity
caveat below.

**Risk:** dropping a stream label changes stream identity, so existing streams
end and new ones begin. Historical logs stay queryable under their old labels.
Say so in the operator doc.

---

## R6 `[P1]` SSID label values diverge between the shell helper and the Lua collectors

**Anchor:** `openwrt/scripts/openwrt-monitor-client-conntrack.sh:143`

```sh
ssid=$(printf '%s' "${ssid:-}" | tr -c 'A-Za-z0-9._-' '_')
```

versus the raw ubus value emitted by `openwrt/collectors/client_inventory.lua:393`,
`openwrt/collectors/topology.lua:231` and `:246`, and
`openwrt/collectors/wifi_dethrash.lua:56`.

**Impact.** An SSID of `My Home` becomes `ssid="My_Home"` in
`openwrt_wifi_assoc_events_total` but stays `ssid="My Home"` in
`openwrt_client_info`. `build_openwrt_clients_dashboard.py` renders both on one
page — panel 9 groups by `ssid` from `client_info`, panel 403 by `ssid` from
`assoc_events` — so one physical SSID appears as two different names, and any
`ssid=` template filter matches only one family. Any join or correlation
between roam events and client identity silently produces empty results.

**Secondary, on the raw path:** SSID is the only operator-supplied free text
reaching a Lua label *value* without sanitizing. Hostname **is** sanitized, at
`client_inventory.lua:389` via `sanitize()` (`:36`). An SSID containing `"` or
`\` corrupts that exposition line — malformed output, not just an ugly label.

**Fix.** Converge on one representation. **Sanitize on both paths**, and use
the Lua `sanitize()` helper's existing character class as the single source of
truth rather than inventing a third:

1. Read `sanitize()` at `client_inventory.lua:36` and confirm its replacement
   set.
2. Apply it to SSID at `client_inventory.lua:393`, `topology.lua:231`, `:246`,
   and `wifi_dethrash.lua:56`.
3. Adjust the `tr -c` class at `client-conntrack.sh:143` so shell and Lua
   produce **byte-identical** output for the same input. Verify with a shared
   fixture containing a space, a `"`, a `\`, a `'`, and a non-ASCII byte.

> **Corrections (applied 2026-07-25).**
>
> - **Step 3 was unnecessary — no shell change was made.** The two classes were
>   *already* identical: Lua `[^%w%._%-]` and shell `tr -c 'A-Za-z0-9._-'` both
>   permit exactly alphanumerics, `.`, `_`, `-`. Verified byte-for-byte over
>   `My Home`, `Say"Hi`, `back\slash`, `it's`, `Café-5G`, `Ünïcode`,
>   `ok.name-1_2`, and `!!!` — all eight agree, including one `_` per byte of a
>   multi-byte UTF-8 character. The divergence was never the class; it was that
>   the Lua collectors did not sanitize SSID **at all**. Editing the `tr` class
>   would have *introduced* a divergence.
> - **Step 2's sites were replaced by their single upstream source.** Patching
>   `topology.lua:231` and `:246` alone would have been wrong: that file also
>   builds ssid ids at `:241`, `:284`, and `:285`, so sanitizing only two of five
>   would make the node ids and edge ids disagree with each other. Both
>   collectors build their iface table in exactly one place
>   (`client_inventory.lua` `wifi_ifaces()`, `topology.lua` its equivalent, both
>   `ssid = config.ssid or ""`), so the fix is applied there and every downstream
>   use inherits it.
> - **Two sites the task did not name were also fixed.**
>   `wifi_dethrash.lua:83` (`section.ssid` from UCI) **had** to be: `:86`
>   compares it against the iwinfo-sourced SSID from `:56` to resolve `ifname`,
>   so sanitizing one side only would have broken that match for any SSID with a
>   space. `wifi_dethrash.lua:103` feeds an SSID into the `ap` label
>   (`hostname .. "/" .. ssid`) with the same corrupt-the-line exposure; only the
>   SSID component is sanitized, so the `"<router>/<ssid>"` shape is preserved.
>   `wifi_dethrash.lua` had no `sanitize()` helper, so one was added matching the
>   other two files (it already used the same class inline in `hostname()`).
> - The alert-rule check the task asks for was run: **no** provisioned rule in
>   `grafana/provisioning/alerting/` matches on a literal SSID string (the one
>   `ssid` hit there is prose in a description).

Do not "fix" this by removing the shell sanitizing — that would leave raw
quotes reaching exposition output on both paths, which is worse.

**This changes label values**, which `AGENTS.md` normally forbids. It is
justified here because the two paths already disagree, so *some* consumer is
already broken; the task is to pick the correct value and make both agree. Note
the change in the operator docs and in `.agent/CHANGELOG.md`, and check whether
any provisioned alert rule in `grafana/provisioning/alerting/` matches on a
literal SSID string.

**Tests to add.** A shared fixture SSID exercised by both
`tests/test_client_conntrack.sh` and `tests/test_client_inventory.lua`,
asserting identical emitted `ssid=` values. This is the only way the two paths
stay converged.

**Done.** `tests/fixtures/wireless_status.json` was already shared by both tests
(and by `tests/test_topology.lua`), so a `wlan3` interface named `Lab Net"5G` —
a space and a double quote — was added to it rather than creating a new fixture.
Both tests now assert the emitted value is `Lab_Net_5G`, with a cross-reference
comment in each pointing at the other so the pair cannot drift. Adding an
interface moved `test_topology.lua`'s *printed* count from 11 nodes/10 edges to
12/11 (the new SSID node and its radio edge); that count is reported, not
asserted, so no topology assertion changed. Confirmed the assertions fail
against the pre-fix collectors, with the raw value rendering as the corrupt
`ssid="Lab Net"5G"`.

---

## R7 `[P1]` `openwrt_client_conntrack_entries` has no cardinality cap

**Anchor:** `openwrt/scripts/openwrt-monitor-client-conntrack.sh:117`

```awk
END {
  for (client in mac) printf "%s\t%d\n", client, count[client] + 0
}
```

**Root cause.** The loop emits one series for **every MAC in `$HOSTSFILE`**,
which is built from `getHostHints` — including zero-count clients and long-stale
neighbour entries that have not been seen in weeks.
`openwrt/collectors/client_inventory.lua` caps the same client set at
`CLIENT_INVENTORY_MAX` (default 256, `:327`) and reports
`openwrt_client_inventory_truncated` (`:370-372`). This helper — installed by
the *same* `clients` profile, from the *same* identity source — has no
equivalent guard.

**Impact.** On a router with a large accumulated hint table, the conntrack
metric emits more series than the inventory it is meant to join against, so the
uncapped tail has no matching `openwrt_client_info` and renders as unlabeled
MACs. Unbounded growth in a per-client metric is the failure mode the inventory
cap exists to prevent.

**Fix.** Mirror the inventory cap rather than inventing a new mechanism:

1. Read `client_inventory.lua:327` and `:370-372` for the exact cap and
   truncation-reporting contract.
2. Add a `CLIENT_CONNTRACK_MAX` knob defaulting to the **same** 256, overridable
   from `/etc/openwrt-grafana-monitor.conf` like the script's other knobs.
3. Sort by descending count before truncating, so the cap drops idle clients
   rather than an arbitrary awk hash order — `sort -k1,1` at `:118` currently
   sorts by MAC; add a count-ordered pass before it.
4. Emit a truncation gauge named consistently with
   `openwrt_client_inventory_truncated`.
5. Document the new knob in `docs/advanced-profiles.md` next to
   `CLIENT_INVENTORY_MAX`.

**Cross-reference:** `docs/CODE-REVIEW-FINDINGS.md` flags the same missing cap
in `topology.lua`. Same class, different file — consider doing both together
and sharing the knob's documentation.

**Test to add.** Extend `tests/test_client_conntrack.sh` with a fixture of
`CLIENT_CONNTRACK_MAX + 10` hosts; assert exactly `CLIENT_CONNTRACK_MAX`
`openwrt_client_conntrack_entries` series and the truncation gauge set to 1.

**Status (2026-07-25): implemented.** The helper now sources
`/etc/openwrt-grafana-monitor.conf`, validates `CLIENT_CONNTRACK_MAX`, defaults
it to 256, sorts by descending count before applying the cap, and emits
`openwrt_client_conntrack_truncated` on successful conntrack scrapes. The final
exposition remains sorted by MAC for deterministic output. The related
topology.lua cap mentioned above was left open because this pass was scoped to
R7.

---

# Batch C — installer and staging robustness

## R8 `[P1]` nlbwmon init script and `protocols` dir used unconditionally after install failure was tolerated

**Anchors:** `openwrt/setup.sh:359` and `:367-369`

At `:288-290` a failed `nlbwmon` install is deliberately downgraded to a
warning:

```sh
if ! pkg_install_optional nlbwmon; then
  log "    WARNING: nlbwmon is unavailable; per-client traffic accounting will report unavailable"
fi
```

Then, in the `clients` profile block, two unguarded uses:

```sh
install_file "$SCRIPT_DIR/nlbwmon/protocols" /usr/share/nlbwmon/protocols 0644   # :359
...
/etc/init.d/nlbwmon enable                                                       # :368
/etc/init.d/nlbwmon restart                                                      # :369
```

**Root cause.** Two independent failures, both fatal under `set -e`:

- `:359` — no `ensure_dir /usr/share/nlbwmon`. That directory is created by the
  nlbwmon package, so when the package is absent `cp` fails.
- `:368` — a missing `/etc/init.d/nlbwmon` exits 127.

**Impact.** The installer aborts *after* collectors and cron lines are
installed but *before* the exporter listener, cron log level, and remote syslog
are configured (all of which follow at `:420+`). The tolerated-warning path at
`:288-290` therefore does not actually work: on a router where nlbwmon is
unavailable, setup dies anyway, in a half-configured state, with a
`cp`/not-found error rather than the intended graceful degradation.

**Fix.** Gate both on the package actually being present:

```sh
if [ -x /etc/init.d/nlbwmon ]; then
  ensure_dir /usr/share/nlbwmon
  install_file "$SCRIPT_DIR/nlbwmon/protocols" /usr/share/nlbwmon/protocols 0644
  log "==> Restarting nlbwmon to load the trimmed service buckets..."
  /etc/init.d/nlbwmon enable
  /etc/init.d/nlbwmon restart
else
  log "    WARNING: nlbwmon is not installed; skipping protocols file and service restart"
fi
```

Check whether `setup.sh` already has an `ensure_dir` helper and a
`pkg_installed` helper — reuse them rather than adding an `[ -x ]` test if a
more idiomatic check exists. (`pkg_installed` is noted in
`docs/CODE-REVIEW-FINDINGS.md` §1.1 as having an unanchored-grep bug for opkg;
if you use it here, fix that first or prefer the `[ -x ]` test.)

Also confirm the same pattern is not repeated for the other
`pkg_install_optional` packages at `:275-298` — `libubus-lua`,
`libiwinfo-lua`, `libuci-lua`, and `conntrack` are all tolerated as warnings;
verify none of them has an unguarded consumer later in the script. Treat any
you find as part of this task.

**Verification.**

```sh
sh -n openwrt/setup.sh
```

Full validation needs a router without nlbwmon; per `AGENTS.md`, do **not**
run `setup.sh` against a live router as routine validation. Report this as a
static-only fix unless the operator explicitly authorizes a live run.

---

## R9 `[P2]` `wan-info` stages inside the textfile dir with no trap

**Anchor:** `openwrt/scripts/openwrt-monitor-wan-info.sh:10`

```sh
outdir="/var/prometheus"
metric_file="$outdir/openwrt_wan_info.prom"
metric_tmp="$metric_file.$$"
```

Same defect class as R4: staged inside the textfile dir, no `trap`, no
leftover sweep. The write window is short — `:45-50` is three `printf`s — so
the duplicate-series exposure is far smaller than R4's. The real risk is a
crash or power loss between `:49` and `:50` leaving a permanent duplicate of
`wan_public_ip_changed`.

Note this script also stages `tmp_file="/tmp/wanip.out.$$"` correctly at `:7`,
so the correct pattern is already present one line above the bug.

**Fix.** Same three parts as R4: stage in `/tmp`, add
`trap 'rm -f "$tmp_file" "$metric_tmp"' EXIT`, and sweep
`rm -f "$metric_file".[0-9]*` once near the top.

This script hardcodes `/var/prometheus` rather than honoring
`OPENWRT_MONITOR_TEXTFILE_DIR` like `wan-quality.sh:6` does — which also makes
it untestable offline. Consider adding the override as part of this task so a
test can exercise it; that is a small, self-contained improvement, but flag it
in the changelog as a behavior change to an environment contract.

**Verification.** `sh -n openwrt/scripts/openwrt-monitor-wan-info.sh`, plus the
directory-cleanliness assertion from R4 if the override is added.

---

## R10 `[P2]` `$ssid` not reset per interface iteration

**Anchor:** `openwrt/scripts/openwrt-monitor-client-conntrack.sh:140-144`

```sh
json_get_var ifname ifname
if json_select config; then json_get_var ssid ssid; json_select ..; fi
json_select ..
ssid=$(printf '%s' "${ssid:-}" | tr -c 'A-Za-z0-9._-' '_')
[ -n "${ifname:-}" ] && [ -n "$ssid" ] && printf '%s\t%s\n' "$ifname" "$ssid" >> "$HOSTSFILE.ifaces"
```

**Root cause.** When `json_select config` fails, the `if` body is skipped and
`$ssid` retains the **previous interface's** value from the enclosing
`for iface_index` / `for radio` loops. That stale SSID is then written into
`$HOSTSFILE.ifaces` keyed by the current `ifname`, so every roam event on that
radio is attributed to the neighbouring SSID. `ifname` at `:140` has the same
exposure via `json_get_var`.

**Confidence: lower than the other findings.** It depends on `config` being
absent from a `network.wireless status` interface entry, which was not
confirmed against a live router. The fix is unconditionally safe and costs
nothing, so apply it regardless rather than spending a live-router session
proving the trigger.

**Fix.** Reset both at the top of the interface-index loop body, before
`json_get_var`:

```sh
ssid=""
ifname=""
```

The existing `[ -n "${ifname:-}" ] && [ -n "$ssid" ]` guard at `:144` then
correctly skips the entry instead of writing a wrong one.

**Test to add.** A `network.wireless status` fixture with two radios where the
second interface entry lacks `config`; assert the second ifname is **absent**
from the ifaces map rather than present with the first radio's SSID.

---

# Batch D — MCP sidecar

## R11 `[P1]` `AutoAddPolicy` accepts any SSH host key while sending the router root password

**Anchor:** `mcp_server/server.py:192`

```python
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
```

**Root cause.** Host keys are neither verified nor persisted — `AutoAddPolicy`
accepts whatever key is offered and, with no `load_host_keys` / `save`, forgets
it, so even trust-on-first-use detection is absent. Every connection is a fresh
blind accept.

**Impact.** A LAN attacker who can answer on the router's IP (ARP spoofing, DHCP
race, a rogue AP) harvests `OPENWRT_SSH_PASSWORD` on the first connection.
They can then feed arbitrary output back to the sidecar's tools — including
`openwrt_run_repo_setup`, whose payload is a shell script the sidecar uploads
and executes. This directly undercuts the "guarded, allowlisted" posture stated
in `AGENTS.md` and `docs/mcp-ssh.md`; the allowlist constrains *which* commands
run, not *which host* runs them.

**Fix.** Prefer `RejectPolicy` with an explicit, operator-managed
`known_hosts`:

1. Add an `OPENWRT_MCP_KNOWN_HOSTS` env var (default
   `/app/known_hosts`), mounted read-only in `docker-compose.yml` alongside the
   existing `./openwrt:/app/openwrt:ro` mount.
2. `client.load_host_keys(path)` when the file exists, then
   `client.set_missing_host_key_policy(paramiko.RejectPolicy())`.
3. On rejection, surface an actionable error naming the expected path and how
   to populate it (`ssh-keyscan -H <router> >> known_hosts`), not a bare
   paramiko traceback.
4. If a strict default would break existing deployments, gate it: default to
   strict, and allow `OPENWRT_MCP_INSECURE_HOST_KEYS=1` to fall back to
   `AutoAddPolicy` **with a loud warning log on every connection**. Do not make
   the insecure mode the default.

While in this file, check whether key-based auth is supported as an alternative
to `OPENWRT_SSH_PASSWORD`. Pinning the host key reduces the exposure but a
password still crosses the wire on every call; if `paramiko` is already wired
for `pkey`, documenting the key path as the recommended setup is a larger
security win than the host-key fix alone. Treat that as optional scope and note
it rather than expanding this task silently.

**Docs to update:** `docs/mcp-ssh.md` — the known_hosts setup step, the new env
vars, and the security rationale.

**Test to add.** Extend `tests/test_mcp_policy.py` to assert the configured
policy is `RejectPolicy` by default and that the insecure override requires the
explicit env var. Do not add a test that makes a real SSH connection.

---

## R12 `[P2]` 180 s timeout on `openwrt_run_repo_setup` is shorter than a real setup run

**Anchor:** `mcp_server/core.py:222` (`timeout_seconds=180`; compare
`:171` and `:192`, both 30, and the `:57` default of 15).

**Root cause.** `setup.sh` on a fresh router does a `pkg_update`, roughly 20
`apk`/`opkg` installs, five `/etc/init.d/* restart` calls, a fixed `sleep 2`,
and then runs each cron helper once — including
`openwrt-monitor-wan-quality.sh`, which alone performs three serial `ping -c 5`
runs. Over a slow WAN or a busy package mirror this routinely exceeds three
minutes.

**Impact.** The SSH channel times out and the tool reports failure while the
router is left **mid-install**: packages partially added, cron lines appended,
exporter possibly not restarted. The operator gets no signal about which half
completed, and the natural next step — rerun — is only safe to the extent
`setup.sh` is idempotent, which
`docs/CODE-REVIEW-FINDINGS.md` §1.1 documents it is **not** for profile
downgrades.

**Fix.**

1. Raise the timeout for this tool specifically to at least 600 s. Do not raise
   the shared default at `:57` — the read-only tools should stay fast-failing.
2. Make the timeout configurable via env (e.g. `OPENWRT_MCP_SETUP_TIMEOUT`)
   with the 600 s default, so a slow link can be accommodated without a code
   change.
3. On timeout, return an error that explicitly says the router may be in a
   partially-configured state and names the read-only tool to run next
   (`openwrt_monitoring_status`). A timeout here is not a clean failure and the
   message must not imply it is.

**Test to add.** `tests/test_mcp_policy.py` assertion that the setup tool's
timeout is greater than the read-only tools' and honors the env override.

---

## R13 `[P2]` MCP accepts a profile combination `setup.sh` always rejects

**Anchors:** `mcp_server/core.py:21` and `:228-238`

```python
ALLOWED_PROFILES = {"core", "clients", "traffic", "wifi_mesh", "dpi", "full"}
```

`normalize_profile` (`:228-238`) validates each comma-separated token against
that set independently, so `profile="full,traffic"` passes. But
`openwrt/setup.sh:205-212` rejects it unconditionally:

```sh
full)
  if [ "$OPENWRT_MONITOR_PROFILE" != "full" ]; then
    die "OPENWRT_MONITOR_PROFILE: full cannot be combined with other profile names"
  fi
```

**Impact.** The failure surfaces only after an SSH round-trip and a staged tar
upload, as a remote `die`, instead of at local validation. Minor, but the whole
point of the policy layer is to reject invalid input before touching the
router.

**Fix.** Mirror the rule in `normalize_profile`, after the per-token check:

```python
if "full" in parts and len(parts) > 1:
    raise PolicyError("profile 'full' cannot be combined with other profiles")
```

Keep the message close to `setup.sh`'s wording so operators see one consistent
explanation.

**Test to add.** `tests/test_mcp_policy.py:74` covers the valid combined case.
Add the rejection case: `normalize_profile("full,traffic")` raises
`PolicyError`. Also add `"traffic,full"` — order must not matter.

**Verification.**

```sh
python3 -m unittest discover -s tests -p 'test_*.py'
```

---

# Batch E — new metrics

Both gaps below were checked against `openwrt/` **and** against
`docs/CODE-REVIEW-FINDINGS.md` Part 6 (which already proposes cpufreq/thermal,
IPv6 device traffic, ingress SQM, uhttpd, BATMAN, and softnet backlog). Neither
appears in either place:

```sh
rg 'nf_conntrack_count|nf_conntrack_max|survey dump|tx retries|tx failed' \
  openwrt/ docs/CODE-REVIEW-FINDINGS.md
# no matches
```

New metrics must follow the repo contract: `openwrt_` prefix, fail-closed
`..._available 0` gauge, `..._seconds` only for durations, bounded label sets,
and a profile assignment. Add each to the appropriate dashboard generator —
never to generated JSON — and regenerate the manual-import/provisioning pairs.

## M1 `[P2]` Global conntrack table saturation

**Gap.** The repo measures *per-client* conntrack occupancy
(`openwrt_client_conntrack_entries`) but never the table itself. When the
conntrack table fills, new connections are dropped and users experience "the
internet is broken" while **every existing per-client series still looks
healthy** — there is currently no metric that shows the cause.

**Proposed metrics.**

| Metric | Source | Type |
| --- | --- | --- |
| `openwrt_conntrack_entries` | `/proc/sys/net/netfilter/nf_conntrack_count` | gauge |
| `openwrt_conntrack_max` | `/proc/sys/net/netfilter/nf_conntrack_max` | gauge |
| `openwrt_conntrack_buckets` | `/proc/sys/net/netfilter/nf_conntrack_buckets` | gauge |

Utilization is then `openwrt_conntrack_entries / openwrt_conntrack_max` — that
ratio, not the raw count, is the saturation signal. Cost: three file reads,
three gauges, **zero label cardinality**.

Consider also the offloaded-flow count from `nf_flowtable` if it is readable
without an `nft` call. That number is the missing piece for interpreting the
flow-offload accounting caveat the repo already tracks via
`openwrt_flow_offload_enabled` — offloaded flows bypass conntrack accounting,
so a low conntrack count with offload enabled means something different than
the same count with offload off. Verify readability on the target hardware
before committing to it; if it needs `nft`, treat it as separate optional
scope.

**Implementation notes.**

- Natural home: a Lua collector alongside the existing ones, or an extension of
  whichever helper already reads `/proc/sys`. Check for an existing reader
  before adding a file.
- Fail closed: if `/proc/sys/net/netfilter/` is absent (conntrack module not
  loaded), emit `openwrt_conntrack_collector_available 0` and no gauges.
  Emitting `0` entries would look like an idle router rather than a missing
  module.
- Profile: `core`. This is cheap and universally useful, not a `clients`-only
  concern. Confirm against `docs/advanced-profiles.md` before deciding.
- Dashboard: a utilization panel with a threshold. `nf_conntrack_max` defaults
  low on small-RAM routers, so this can genuinely saturate on the target
  hardware.
- Alert: an obvious candidate for `grafana/provisioning/alerting/` — sustained
  utilization above ~80%.

## M2 `[P2]` WiFi airtime / channel utilization and per-station error counters

**Gap A — airtime.** `openwrt/scripts/openwrt-monitor-wifi-radio.sh` collects
channel, frequency, tx power, noise, and quality, but not
`iw dev <if> survey dump`, which reports `channel active time`,
`channel busy time`, `channel receive time`, and `channel transmit time`.

**Busy-over-active is *the* WiFi congestion metric.** Noise floor alone does
not explain "5 GHz feels slow" when a neighbouring AP is saturating the
channel — the airtime split does, and it distinguishes *our own* traffic
(transmit + receive) from *someone else's* (busy minus ours).

Proposed: `openwrt_wifi_survey_active_time_seconds_total`,
`_busy_time_seconds_total`, `_receive_time_seconds_total`,
`_transmit_time_seconds_total`, labeled by `radio` / `ifname` / `channel`.
`iw` reports these as monotonic milliseconds since interface up, so counters
with a `_seconds_total` suffix are the right shape — divide by 1000 and let
Prometheus `rate()` produce the fraction. Confirm the reset-on-radio-restart
behavior on the target hardware and note it, since a counter reset is visible
as a `rate()` gap.

Emit only the **survey entry for the in-use channel** (`iw` marks it
`[in use]`). Dumping every surveyed channel multiplies series by the channel
count for no operational benefit.

**Gap B — per-station errors.** `openwrt-monitor-wifi-radio.sh:134-140` already
parses `iw station dump` but extracts only `connected time`, discarding
`tx retries`, `tx failed`, `rx drop misc`, and `tx bitrate` / `rx bitrate`.
Retry and failure **rates** are what distinguish a weak-signal client from a
channel-contention problem — signal strength alone does not. The parse loop is
already there; this is incremental.

Proposed: `openwrt_wifi_station_tx_retries_total`,
`_tx_failed_total`, `_rx_drop_misc_total`, plus
`openwrt_wifi_station_tx_bitrate_mbps` / `_rx_bitrate_mbps` gauges, labeled by
`mac` (lowercase) and `ifname`.

**Implementation notes.**

- **Cardinality.** Per-station metrics need the same cap treatment as R7 —
  reuse `CLIENT_INVENTORY_MAX` or an analogous bound rather than adding a third
  unbounded per-client metric. Do R7 first so the pattern exists to copy.
- Do not add `signal`/`rssi` here if an existing collector already emits it —
  grep first; `client_inventory.lua` gathers assoclist data and may already
  cover it.
- Fail closed: `iw` missing, or `survey dump` unsupported by the driver, emits
  `..._available 0`. MT7621/mt76 supports survey dump, but do not assume it for
  all targets.
- Profile: `wifi_mesh`. Confirm against `docs/advanced-profiles.md`.
- Parsing `iw` output is brittle across versions. Add a fixture under
  `tests/fixtures/` captured from the target hardware and a test that parses
  it, matching how the other collectors are tested.

---

# Execution guidance

**Order.** Batch A first (real breakage), then B (silent data problems), then C
and D in either order, then E. Do not start E before R7 — M2's cardinality cap
should reuse the pattern R7 establishes.

**Per-task loop.**

1. `git status --short` — confirm a clean start and note pre-existing drift
   (`.opencode/` and `graphify-out/` are expected untracked).
2. Read the anchor and its surrounding context before editing. Several of these
   fixes are one line; the reasoning around them is not.
3. Make the smallest correct change. Do not reformat or reorganize adjacent
   code.
4. Add or extend the test named in the task.
5. Run the narrowest relevant check, then the broader ones.
6. Update the nearest operator doc when configuration, metrics, profiles, or
   safety constraints changed.

**Validation commands** (from `AGENTS.md`):

```sh
python3 -m unittest discover -s tests -p 'test_*.py'
sh -n openwrt/setup.sh openwrt/scripts/*.sh
docker compose config
sh tests/run_all.sh
```

`sh tests/run_all.sh` regenerates dashboard artifacts — inspect `git diff`
afterward and keep generated-output drift out of unrelated commits. It skips
Lua checks when `lua5.1`/`luac5.1` are unavailable; report that as a skipped
check rather than a pass.

**Live-system boundaries.** R1 is the only task whose full validation needs a
running stack. R2, R8, and R11 cannot be fully validated without a router, and
per `AGENTS.md` `setup.sh` runs, restarts, and firewall changes are **not**
routine validation — they need explicit operator authorization and
`confirm=true`. Report these as static-only unless authorized, and keep
static / live / Grafana-UI evidence separated in the final report.

**Handoff.** Update `.agent/STATUS.md` when stopping with tasks outstanding,
and add a `.agent/CHANGELOG.md` entry per batch completed, referencing task
IDs. Tick the checkboxes in the task list above as work lands so the next agent
can resume without re-deriving state.

---

# Appendix A — reproductions

The four `[reproduced]` claims, run locally on 2026-07-25.

## A.1 — R1 Alloy image defaults

```
$ docker image inspect grafana/alloy:latest \
    --format '{{json .Config.Entrypoint}} {{json .Config.Cmd}}'
["/bin/alloy"] ["run","/etc/alloy/config.alloy","--storage.path=/var/lib/alloy/data"]

$ docker run --rm --entrypoint /bin/sh grafana/alloy:latest -c \
    '/bin/alloy run --help' | grep -E 'listen-addr|storage.path'
  --server.http.listen-addr string   Address to listen for HTTP traffic on (default "127.0.0.1:12345")
  --storage.path string              Base directory where components can store data (default "data-alloy/")
```

Confirms: the image sets `--storage.path` (so the `command` override drops it,
falling back to cwd-relative `data-alloy/`), and it never sets
`--server.http.listen-addr` (so the loopback default is pre-existing, **not**
caused by the override). `/usr/bin/alloy` and `/bin/alloy` are the same
binary, so the `exec` path in `docker-compose.yml` is fine.

## A.2 — R2 legacy-crontab abort

```
$ printf '* * * * * /usr/bin/openwrt-grafana-monitor-metrics\n*/5 * * * * /usr/bin/openwrt-grafana-monitor-sqm\n' > ct
$ sh -c 'set -eu; CRONTAB_FILE=ct
  if grep -qE "openwrt-grafana-monitor-(metrics|sqm)" "$CRONTAB_FILE" 2>/dev/null; then
    grep -vE "openwrt-grafana-monitor-(metrics|sqm)" "$CRONTAB_FILE" > "$CRONTAB_FILE.clean"
    mv "$CRONTAB_FILE.clean" "$CRONTAB_FILE"
    echo "removed superseded cron entries"
  fi
  echo REACHED_END'
$ echo "exit=$?"
exit=1
```

`REACHED_END` is never printed and the `removed superseded cron entries` log
line never appears — the shell dies at the `grep -v`.

## A.3 — R3 awk on a missing ROWSFILE

```
$ sh -c 'set -e; R=/nonexistent-rows.$$; awk -F "\t" "{print}" "$R"; echo REACHED_END'
awk: fatal: cannot open file `/nonexistent-rows.4065928' for reading: No such file or directory
$ echo "exit=$?"
exit=2
```

Confirms the `set -e` abort at `client-traffic.sh:159` before the `mv` at
`:163`. `grep -n 'ROWSFILE' openwrt/scripts/openwrt-monitor-client-traffic.sh`
confirms the file is referenced only at `:17` (declaration), `:23` (trap),
`:130` (the append inside the loop), and `:159` — never created
unconditionally.

## A.4 — R13 profile validation asymmetry

`mcp_server/core.py:232-238` checks membership per comma-separated token with
no combination rule, while `openwrt/setup.sh:205-212` contains an explicit
`full cannot be combined with other profile names` guard. `full,traffic`
therefore passes MCP validation and fails on the router.

---

# Appendix B — relationship to `docs/CODE-REVIEW-FINDINGS.md`

That document (2026-07-23, report-only, ~120 findings) remains open work and is
**not** superseded. Everything in it was excluded from this pass, including:
`/etc` flash wear, dhcp-pool staging, the `topology.lua` cardinality cap, the
`wifi_dethrash` `available 1` issue, ingress SQM, the MAC-case mismatch, UCI
`textfile_dir`, and profile-downgrade non-idempotency.

Siblings worth doing together:

| This plan | Related finding |
| --- | --- |
| R2 | §1.1 `set -e` + unguarded command pattern in `setup.sh` |
| R4, R9 | the already-documented staging bugs (dhcp-pool and others) |
| R7 | the `topology.lua` missing cardinality cap |
| R8 | §1.1 `pkg_installed` unanchored grep; `pkg_install_required` abort |
| M1, M2 | Part 6 missing-metrics list |

`openwrt-monitor-wan-quality.sh` (R4) has **no section** in that document. Add
one, or note there that its findings live here.
