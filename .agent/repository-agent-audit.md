# Repository agent audit

Date: 2026-07-25

## Repository overview

### Main applications and components

- Docker Compose observability stack for OpenWrt routers using Grafana OTEL-LGTM and Grafana Alloy.
- Router-side OpenWrt installer and collectors under `openwrt/`.
- Dashboard-as-code generators in root Python files:
  - `build_dashboards.py` for the four classic dashboards.
  - `build_openwrt_*_dashboard.py` for v2 dashboards: operations, clients, advanced, topology, and mission control.
- Grafana provisioning under `grafana/provisioning/`.
- Manual dashboard exports under `grafana-dashboard-exports/` and preserved legacy/PR exports under `grafana dashboards/`.
- Optional guarded SSH MCP sidecar under `mcp_server/`.
- Documentation under `docs/`, including setup, troubleshooting, MCP, advanced profiles, review findings, and a large topology/netflow plan.
- Local ignored agent/dashboard reference material exists under `.claude/`, `.agents/`, `.codex/`, `skills/`, and `grafana_docs/`.

### Languages, frameworks, and build systems

- Python: dashboard generators, MCP sidecar, policy tests.
- POSIX shell: OpenWrt installer and router helper scripts.
- Lua 5.1: OpenWrt collectors.
- Grafana JSON/YAML provisioning.
- Docker Compose: local stack and optional MCP sidecar.
- No verified Makefile, package-manager workflow, hosted CI workflow, formatter, or linter was found.

### Important entry points

- `docker-compose.yml`: OTEL-LGTM, Alloy, and `openwrt-ssh-mcp` services.
- `alloy/config.alloy`: scrape target discovery, relabeling, syslog receive, and forwarding.
- `openwrt/setup.sh`: router installer for collectors, helper scripts, profiles, syslog, and textfile metrics.
- `mcp_server/server.py` and `mcp_server/core.py`: guarded SSH MCP server.
- `tests/run_all.sh`: broad local validation script, currently ignored and untracked.
- `README.md`: operator-facing quick start and repo summary.

### Test, lint, build, deploy, and validation commands

Observed commands:

```sh
python3 -m unittest discover -s tests -p 'test_*.py'
sh -n openwrt/setup.sh openwrt/scripts/*.sh
docker compose config
sh tests/run_all.sh
python3 build_dashboards.py
python3 build_openwrt_operations_dashboard.py
python3 build_openwrt_clients_dashboard.py
python3 build_openwrt_advanced_dashboard.py
python3 build_openwrt_topology_dashboard.py
python3 build_openwrt_mission_control.py
docker compose up -d
docker compose --profile mcp up -d --build openwrt-ssh-mcp
```

`tests/run_all.sh` compiles generators, regenerates v2 dashboards, compares manual/provisioned v2 copies, checks shell syntax, and runs available shell/Lua collector tests. It optionally runs a live duplicate-series check when `ROUTER_METRICS_URL` is set.

### Important directory boundaries

- Maintained sources: root `build*.py`, `alloy/`, `openwrt/`, `mcp_server/`, selected `tests/`, and operator docs.
- Generated dashboard outputs: `grafana/provisioning/dashboards/*.json`, `grafana-dashboard-exports/*.json`, and legacy preserved `grafana dashboards/*.json`.
- Local/ignored reference or private state: `.agents/`, `.claude/`, `.codex/`, `skills/`, `grafana_docs/`, `screenshot/`, `__pycache__/`, `tests/__pycache__/`.
- Tracked screenshots: `docs/screenshots/*.png`.

## Current agent-readiness

### Existing `CLAUDE.md` and `AGENTS.md`

- Root `AGENTS.md` does not exist.
- `CLAUDE.md` exists but is untracked. It contains useful repository guidance, but it duplicates content that should be shared with other agents.
- The user-provided instructions and local `CLAUDE.md` both describe the correct generator/source-output workflow and safety constraints, but only Claude-like tools will discover `CLAUDE.md` by default.

### Instruction quality

- Useful constraints are present in `CLAUDE.md`: preserve dashboard UIDs, edit generators rather than generated JSON, preserve `ROUTER_TARGETS` and `job="openwrt"`, avoid live mutations, and run local validation.
- The guidance is not shared through a root `AGENTS.md`, so Codex/OpenCode-compatible tools may need to rediscover the repository.
- Some guidance is stale or incomplete relative to the current source tree:
  - `README.md` says there are 7 pre-built dashboards, but current provisioning contains 9 JSON dashboards.
  - `README.md` repo structure omits `build_openwrt_topology_dashboard.py`, `build_openwrt_mission_control.py`, and their generated outputs.
  - `CLAUDE.md` already warns that the README dashboard count/tree can lag, but this should be in shared agent instructions.

### Agent exploration cost

- Large files likely to attract unnecessary reading:
  - `docs/client-topology-and-netflow-plan.md`: 2348 lines.
  - `docs/CODE-REVIEW-FINDINGS.md`: 1548 lines.
  - `build_openwrt_mission_control.py`: 3144 lines.
  - `grafana_docs/llms-full.txt`: larger than 256 KB and ignored.
  - Several dashboard JSON exports are larger than 256 KB.
- Generated dashboard outputs and ignored local reference material are mixed near maintained source names; agents need explicit rules to avoid reading generated JSON first.

### Clear component boundaries

- The repository has recognizable component boundaries, but there is no concise map outside the README. The README is operator-facing and partially stale.
- No structural move is necessary to describe the boundaries clearly.

### Generated and irrelevant content exclusion

- `.gitignore` excludes many local/generated areas, including `.agents/`, `.claude/`, `.codex/`, `skills`, `grafana_docs`, `screenshot`, `docs/screenshots`, and `__pycache__/`.
- `.gitignore` also ignores most of `tests/`, while current local validation depends on ignored files such as `tests/run_all.sh`, `tests/check_exposition.py`, fixtures, and several shell/Lua tests.
- `docs/screenshots` is tracked but ignored; this is acceptable for preventing accidental new screenshots, but agents should treat screenshots as inspect-on-demand.

### Memory and changelog bounds

- There is no `.agent/STATUS.md` or `.agent/CHANGELOG.md`.
- Existing ignored `.agents/` appears to be local/private branch-collateral and should not be treated as durable repository state.

### Prompt caching risk

- `CLAUDE.md` contains durable guidance, not timestamps or current status, so it is mostly cache-friendly.
- The new durable root instruction should not include Git status, task state, logs, generated JSON, or current metrics.

## Observed inefficiencies

### 1. Missing shared root `AGENTS.md`

- Evidence: `sed -n '1,260p' AGENTS.md` failed with `No such file or directory`; root `CLAUDE.md` exists and is untracked.
- Impact: Non-Claude agents are less likely to find repository-specific constraints and commands.
- Likely cost: medium token/workflow cost from repeated discovery of dashboard generators, generated outputs, OpenWrt compatibility, and MCP safety.
- Severity: high.
- Safest corrective action: create concise root `AGENTS.md` with durable shared guidance; make `CLAUDE.md` import it.
- Confidence: high.
- Repository change required: yes.

### 2. Duplicated Claude-only instructions

- Evidence: `CLAUDE.md` is 144 lines and contains full shared repository policy.
- Impact: Any later update must be duplicated or agents may diverge.
- Likely cost: low to medium ongoing maintenance cost.
- Severity: medium.
- Safest corrective action: keep `CLAUDE.md` small with `@AGENTS.md` and only Claude-specific notes.
- Confidence: high.
- Repository change required: yes.

### 3. README dashboard map is stale

- Evidence: `README.md` says "7 pre-built dashboards" and lists only three v2 outputs, while current provisioning has 9 dashboard JSON files and v2 source generators include topology and mission control.
- Impact: Agents may rely on stale README counts and inspect the wrong generator or skip current dashboards.
- Likely cost: medium token/workflow cost during dashboard tasks.
- Severity: medium.
- Safest corrective action: add a concise `docs/repository-map.md` and link it from `README.md`; keep root instructions warning agents to verify source/output files before relying on README summaries.
- Confidence: high.
- Repository change required: yes.

### 4. Validation script and fixture set are ignored

- Evidence: `git ls-files tests` lists only `tests/test_client_conntrack.sh` and `tests/test_mcp_policy.py`; `git status --ignored --short tests` shows `tests/run_all.sh`, fixtures, and several collector tests as ignored.
- Impact: Fresh clones may lack the advertised validation harness unless these files are supplied out-of-band; agents also see a confusing mismatch between instructions and tracked files.
- Likely cost: high workflow cost when validation cannot run or cannot be reproduced.
- Severity: high.
- Safest corrective action: update `.gitignore` to stop ignoring the validation harness and fixtures. Do not add or remove test files in this task; make visibility explicit and let the maintainer decide what to stage.
- Confidence: high.
- Repository change required: yes for `.gitignore`; separate human review may be needed to decide which currently ignored tests should be committed.

### 5. Tracked `.env` contains private values

- Evidence: `.env` is tracked by `git ls-files`; `.gitignore` ignores future `.env`; the current `.env` differs from `.env.example` and contains non-placeholder MCP/router values.
- Impact: Secret exposure risk and agent risk from reading/copying secrets into responses, audit files, or patches.
- Likely cost: security risk rather than token cost.
- Severity: high.
- Safest corrective action: document redacted finding, add explicit agent rules to avoid reading or quoting `.env`, and recommend human rotation plus `git rm --cached .env` in a separate deliberate secret-remediation change.
- Confidence: high.
- Repository change required: documentation now; secret cleanup requires human approval.

### 6. Large docs and generated/reference files invite unnecessary context loading

- Evidence: `docs/client-topology-and-netflow-plan.md` is 2348 lines, `docs/CODE-REVIEW-FINDINGS.md` is 1548 lines, `build_openwrt_mission_control.py` is 3144 lines, and multiple dashboard/reference outputs are larger than 256 KB.
- Impact: Agents may load long historical docs or generated JSON when a targeted source file or map would suffice.
- Likely cost: medium to high token cost during dashboard and topology work.
- Severity: medium.
- Safest corrective action: add shared navigation/exclusion rules and a concise repository map; avoid moving or rewriting the large docs.
- Confidence: high.
- Repository change required: yes, documentation only.

### 7. No bounded durable agent handoff files

- Evidence: `.agent/STATUS.md` and `.agent/CHANGELOG.md` do not exist.
- Impact: Agents may use ignored `.agents/` or conversation history for durable state, or append unbounded notes in ad hoc files.
- Likely cost: low to medium workflow cost.
- Severity: medium.
- Safest corrective action: create bounded `.agent/STATUS.md` and append-only `.agent/CHANGELOG.md` with size and content rules.
- Confidence: high.
- Repository change required: yes.

## Structural-change candidates

### Candidate A: Move generated dashboard JSON away from provisioning folders

- Current location: `grafana/provisioning/dashboards/*.json`, `grafana-dashboard-exports/*.json`, `grafana dashboards/*.json`.
- Proposed location: none.
- Concrete benefit: Could separate generated outputs from maintained source.
- Evidence of current struggle: Agents need guidance to edit generators, but Docker provisioning currently depends on `grafana/provisioning/dashboards/`.
- References to current path: dashboard generators, `tests/run_all.sh`, README, docs, Docker provisioning, and Grafana provider YAML.
- Compatibility risks: Docker provisioning and manual import workflows could break.
- Migration complexity: high because every generator, validation compare, docs reference, and provisioning config would need updates.
- Rollback method: restore paths and generator outputs.
- Recommendation: reject. Documentation is sufficient and safer.

### Candidate B: Move `grafana dashboards/` to a no-space directory

- Current location: `grafana dashboards/`.
- Proposed location: `grafana-legacy-dashboards/`.
- Concrete benefit: Easier shell quoting and clearer name.
- Evidence of current struggle: None observed beyond theoretical quoting friction; README says these are preserved exports from a PR branch.
- References to current path: README, docs plan, code review findings, tracked files.
- Compatibility risks: External manual-import references and historical comparison paths could break.
- Migration complexity: medium.
- Rollback method: move files back and update references.
- Recommendation: reject. No demonstrated current agent or developer failure.

### Candidate C: Move large historical docs under an archive directory

- Current location: `docs/client-topology-and-netflow-plan.md`, `docs/CODE-REVIEW-FINDINGS.md`.
- Proposed location: `docs/archive/`.
- Concrete benefit: Reduce accidental reading of long historical files.
- Evidence of current struggle: These files are large, but no broken workflow or repeated accidental reads were observed in repository state.
- References to current path: README/doc references and prior work notes may rely on current paths.
- Compatibility risks: Existing links and agent memory references break.
- Migration complexity: medium.
- Rollback method: move files back and restore links.
- Recommendation: document only. Add navigation guidance to read these by targeted section only.

### Candidate D: Move ignored local skill/reference material into tracked docs

- Current location: `skills/`, `grafana_docs/`.
- Proposed location: none.
- Concrete benefit: Could make dashboard guidance portable.
- Evidence of current struggle: None for this task; these are intentionally ignored local agent/tooling resources.
- References to current path: local skill references and `.gitignore`.
- Compatibility risks: Large reference files could bloat repository context and accidentally vendor third-party docs.
- Migration complexity: high.
- Rollback method: remove tracked copies.
- Recommendation: reject. Keep ignored and document as optional local reference.

### Candidate E: Introduce component-level `AGENTS.md` files

- Current location: none.
- Proposed location: `openwrt/AGENTS.md`, `mcp_server/AGENTS.md`, or dashboard-specific instructions.
- Concrete benefit: Could add component-specific rules near source.
- Evidence of current struggle: The repository is small enough that root instructions can cover distinct rules without excessive length.
- References to current path: none.
- Compatibility risks: More instruction files can become stale or contradictory.
- Migration complexity: low.
- Rollback method: remove component files.
- Recommendation: reject for now. Root `AGENTS.md` plus `docs/repository-map.md` is the smaller sufficient change.

