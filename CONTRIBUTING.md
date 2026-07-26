# Contributing

## Development Rules

- Keep router code POSIX shell/BusyBox-compatible and Lua 5.1-compatible.
- Preserve Prometheus metric names, labels, dashboard UIDs, and datasource
  variables unless a change explicitly updates the public contract.
- Edit dashboard generators under `scripts/`; never edit generated dashboard JSON
  directly.
- Regenerate both the manual-import and Docker-provisioned copies when a
  dashboard source changes.
- Keep optional profiles fail-closed and show unavailable or unreliable states
  instead of plausible zeroes.
- Do not include credentials, host keys, router data, or private screenshots.

## Local Checks

Run the narrowest relevant check first, then the full offline gate:

```sh
python3 -m unittest discover -s tests -p 'test_*.py'
sh -n openwrt/setup.sh openwrt/scripts/*.sh
docker compose config
sh tests/run_all.sh
```

`tests/run_all.sh` regenerates dashboard artifacts and compares the paired
outputs. Review `git diff` afterward. Lua checks are skipped when Lua 5.1 is
unavailable. Live router checks are optional and require explicit authorization.

## Dashboard Changes

Run generators as Python modules from the repository root, for example:

```sh
python3 -m scripts.build_openwrt_operations_dashboard
```

Generated outputs belong in `grafana-dashboard-exports/` and
`grafana/provisioning/dashboards/`. Preserve the legacy exports unless the
change explicitly targets them.
