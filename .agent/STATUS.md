# Current status

## Current goal

Work the remediation backlog in `docs/CODE-REVIEW-REMEDIATION-PLAN.md`
(tasks `R1`-`R13`, `M1`-`M2`) without changing metric names, label sets, or
dashboard UIDs except where a task explicitly says to.

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

## Next action

Implement Batch A (`R1`-`R4`). Do `R7` before `M2`, since `M2`'s per-station
cardinality cap should reuse the pattern `R7` establishes. Tick the plan's
checkboxes as tasks land.

## Blockers

- `R2`, `R8`, and `R11` cannot be fully validated without a router. Per
  `AGENTS.md`, `setup.sh` runs, service restarts, and firewall changes are not
  routine validation and need explicit operator authorization plus
  `confirm=true`. Report them as static-only until then.
- `R1`'s `curl`/`docker compose exec` checks need a running stack.
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
