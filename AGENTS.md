# OpenWrt Grafana Monitor

## Purpose

This repository deploys OpenWrt router metrics and syslog to a Docker Compose
stack using Grafana OTEL-LGTM and Grafana Alloy. It also contains router-side
POSIX shell/Lua collectors, generated Grafana dashboards, and an optional
guarded SSH MCP sidecar.

## Component Map

- `docker-compose.yml`: OTEL-LGTM, Alloy, and opt-in `openwrt-ssh-mcp` services.
- `alloy/config.alloy`: router metric scraping, file-SD target discovery,
  bounded relabeling, and syslog forwarding.
- `openwrt/setup.sh`: OpenWrt installer. `openwrt/collectors/` contains Lua
  collectors, `openwrt/scripts/` contains cron/textfile helpers,
  `openwrt/lua/` contains shared `require`-able modules installed to
  `/usr/lib/lua/` (never to the collectors directory, where every file is
  loaded as a collector), and `openwrt/nftables/` contains optional nftables
  rules.
- `build_oui_table.py`: regenerates `openwrt/lua/oui_data.lua` from the IEEE
  MA-L registry. The generated table is committed; do not hand-edit it and do
  not hand-write vendor mappings.
- `build_dashboards.py`: classic dashboard source; writes four provisioning JSON
  files under `grafana/provisioning/dashboards/`.
- `build_openwrt_*_dashboard.py`: v2 dashboard sources; write matching manual
  import files under `grafana-dashboard-exports/` and provisioning files under
  `grafana/provisioning/dashboards/`.
- `grafana/provisioning/`: Docker-provisioned datasources, alerting, and
  generated dashboards.
- `mcp_server/`: allowlisted authenticated SSH MCP sidecar; see
  `docs/mcp-ssh.md`.
- `tests/`: offline shell/Lua/Python checks and fixtures.
- `docs/repository-map.md`: concise navigation map for agents and contributors.

The README can lag the generator set. Verify current source and output files
before relying on dashboard counts or trees.

## Efficient Repository Navigation

- Start with files named by the user.
- Search for symbols and references before reading complete files.
- Read only relevant line ranges in large files.
- Use `docs/repository-map.md` and the nearest operator doc before rediscovering
  the repository layout.
- Do not recursively inspect generated, dependency, cache, build, screenshot,
  archive, or local agent-state directories.
- Do not reread unchanged files without a concrete reason.
- Expand the search only when current evidence requires it.
- For dashboard work, inspect the generator first and generated JSON only when
  validating output pairs, import shape, or exact rendered schema.

## Default Exclusions

Normally avoid these unless directly relevant:

- `.git/`
- `.env` and `.env.*`
- `.agents/`, `.claude/`, `.codex/`, and `claude/`
- `__pycache__/` and `tests/__pycache__/`
- `grafana/provisioning/dashboards/*.json`
- `grafana-dashboard-exports/*.json`
- `grafana dashboards/*.json`
- `grafana_docs/`
- `skills/`
- `screenshot/`
- `docs/screenshots/`

Do not globally ignore generated dashboards during validation. This repository
intentionally stores generated JSON copies and compares v2 manual-import and
provisioning outputs.

## Architecture Boundaries

- Preserve metric names, label sets, datasource variables, and dashboard UIDs
  unless the task explicitly changes the contract.
- Preserve `${DS_PROMETHEUS}`, `${DS_LOKI}`, `job="openwrt"`, router filtering,
  `$__rate_interval`, and `$__auto` where existing dashboard patterns require
  them.
- Edit dashboard generators, never generated JSON directly. Regenerate outputs
  and compare each manual-import/provisioning pair.
- Keep legacy dashboards stable unless the task explicitly changes them.
- `ROUTER_TARGETS` generates Alloy file-SD targets with `job="openwrt"` and a
  bounded `router` label. Preserve the legacy single-router fallback.
- Alloy drops unbounded `node_nat_traffic` before storage. Do not restore it or
  collapse its labels in a way that silently loses series.
- Alloy lowercases the `mac`, `station` and `bssid` labels at ingest so the
  upstream uppercase-MAC collectors join this repository's lowercase ones.
  These labels must be normalised together: lowercasing one side of a
  `station`/`mac` join silently empties it.
- The topology node-graph contract is multi-router. Node ids must be nameable
  identically by every exporter (`router:<lan-ip>`, `bss:<bssid>`), only a
  gateway may emit `internet`/`modem:`/`router:`/`port:`, and every node and
  edge must carry `authority`. Placeholder (`authority="0"`) nodes exist so
  edge endpoints resolve; a dangling edge endpoint crashes the node graph
  panel rather than degrading. Reconciliation across routers lives in the
  dashboard PromQL, and `tests/test_topology_promql.sh` is what covers it.
- Optional profiles are `core`, `traffic`, `wifi_mesh`, `dpi`, `clients`, and
  `full`. Missing dependencies must fail closed or render explicit unavailable
  states, never plausible zeroes.
- Flow offload can invalidate traffic accounting. Keep unreliable state visible
  and do not describe static exposition or dashboard checks as live proof.
- Router code must remain POSIX shell/BusyBox-compatible and Lua 5.1-compatible.

## Editing

- Begin with `git status --short`, targeted `rg` searches, and the nearest
  source, test, call site, and documentation.
- Make the smallest correct change.
- Preserve unrelated behavior and formatting.
- Do not rewrite complete files for small edits.
- Follow existing repository patterns.
- Do not reorganize code while fixing an unrelated issue.
- Do not introduce abstractions without repeated evidence that they are needed.
- Add or update tests and fixtures when behavior changes, especially parser,
  fail-closed, duplicate-series, cardinality, generated-dashboard, router
  profile, or MCP policy behavior.
- Update the nearest operator documentation when configuration, profiles,
  metrics, dashboards, deployment behavior, or safety constraints change.

## Verification

Run the narrowest relevant validation first, then broader checks when applicable:

```sh
python3 -m unittest discover -s tests -p 'test_*.py'
sh -n openwrt/setup.sh openwrt/scripts/*.sh
docker compose config
sh tests/run_all.sh
```

Useful targeted dashboard commands:

```sh
python3 build_dashboards.py
python3 build_openwrt_operations_dashboard.py
python3 build_openwrt_clients_dashboard.py
python3 build_openwrt_advanced_dashboard.py
python3 build_openwrt_topology_dashboard.py
python3 build_openwrt_mission_control.py
```

`tests/run_all.sh` compiles Python, regenerates v2 dashboards, compares output
pairs, checks shell syntax, and runs available collector tests. It writes
generated dashboard artifacts; inspect `git diff` afterward. It skips Lua checks
when `lua5.1` or `luac5.1` is unavailable.

Set `ROUTER_METRICS_URL=http://<router>:9100/metrics` only for an authorized live
duplicate-series check.

Do not claim success without showing the command and result. Report skipped
checks and the reason they were skipped. Preserve full error details when
validation fails.

There is no verified Makefile, package-manager workflow, hosted CI workflow,
formatter, or linter. Do not invent commands for them.

## MCP and Live-System Safety

- Treat `.env` as private even if it exists in the worktree. Use `.env.example`
  for documented examples. Never quote tokens, passwords, keys, connection
  strings, private router data, or sensitive DNS/log output.
- The MCP sidecar is not a raw shell server. Use only allowlisted tools, keep it
  localhost/private-network bound, and start with read-only checks.
- Any MCP or router mutation requires explicit user authorization and
  `confirm=true`.
- Do not run setup, restarts, firewall changes, firmware upgrades, or other live
  mutations as routine validation.
- Do not run `apk upgrade`; OpenWrt firmware changes use sysupgrade or attended
  sysupgrade outside this repository's normal development workflow.
- Do not enable DNS query logging without explicit operator consent.

## Agent Handoff Files

- `.agent/STATUS.md` is a small current-state handoff. Keep it below about 100
  lines, replace obsolete information, and do not load it for unrelated tasks.
- `.agent/CHANGELOG.md` is append-only for meaningful repository changes. Do not
  log routine reads, searches, failed experiments, secrets, or sensitive command
  output.
- Update `.agent/STATUS.md` when a multi-step task ends with pending work,
  blockers, important generated-output drift, or a validation caveat.
- Add a `.agent/CHANGELOG.md` entry when changing repository behavior,
  instructions, validation expectations, generated-file workflow, or deployment
  assumptions.
- Do not use `.agents/` as durable repository state; it is ignored local tooling
  state.

## Git and Reporting

- Never commit, push, reset, rebase, amend, clean, or discard changes unless the
  user explicitly requests that exact operation.
- Before editing, record unexpected worktree changes and work around them.
- Before completion, review the focused diff for unrelated changes, secrets,
  generated drift, and regressions.
- Final reports should separate static validation, live-system validation, and
  Grafana/UI evidence.
- Include changed files, important decisions, commands and results, failures,
  skipped checks, and remaining risks.

## Definition of Done

Work is complete only when the requested behavior is implemented, relevant
checks pass or their failures are reported, generated copies are synchronized,
documentation is accurate, and static, live, and Grafana/UI evidence are clearly
separated.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

When the user types `/graphify`, use the installed graphify skill or instructions before doing anything else.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- Dirty graphify-out/ files are expected after hooks or incremental updates; dirty graph files are not a reason to skip graphify. Only skip graphify if the task is about stale or incorrect graph output, or the user explicitly says not to use it.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
