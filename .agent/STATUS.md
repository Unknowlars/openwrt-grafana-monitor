# Current status

## Remediation plan

`docs/CODE-REVIEW-REMEDIATION-PLAN.md` is still the source of truth.
Remediation tasks `R1`-`R13` are implemented and validated. The next unchecked
plan task is `M1` (global conntrack table saturation metric).

Do not commit, stage, reset, clean, or discard worktree changes unless the user
explicitly asks for that exact git operation.

## Live follow-up fixes (2026-07-25)

The user authorized bounded MCP/router fixes after the R13 pass. The following
live issues found through read-only MCP/router checks were fixed in source and
deployed to both configured routers with `openwrt_run_repo_setup confirm=true`:

- MCP metrics helpers now resolve `network.lan.ipaddr`, strip any CIDR suffix,
  and fetch `http://$LAN_IP:9100/metrics` instead of assuming router-local
  `127.0.0.1`.
- MCP `wifi` diagnostics redact common wireless secret fields before returning
  `wifi status`.
- MCP adds bounded `conntrack_sources` and `inode_sources` diagnostics. They
  report availability/counts only, not raw host hints, conntrack rows, client
  addresses, MACs, or filesystem listings.
- `openwrt-monitor-client-conntrack.sh` now separates WiFi association-event
  availability from conntrack availability, falls back from the `conntrack` CLI
  to `/proc/net/nf_conntrack` or `/proc/net/ip_conntrack`, and falls back from
  `getHostHints` identity to DHCP leases/ARP when needed.
- `openwrt-monitor-inodes.sh` now falls back from `df -iP` to `stat -f`.
  `setup.sh` opportunistically installs `coreutils-stat` when neither inode
  source is usable.

Live outcome after deployment:

- `openwrt-main` and `openwrt-new`: exporter running, cron running, syslog
  configured to `192.168.0.247:514/udp`.
- Both routers now report `openwrt_client_conntrack_collector_available 1`,
  `openwrt_wifi_assoc_events_collector_available 1`, and
  `openwrt_filesystem_inode_collector_available 1`.
- `conntrack-tools` is not present in the OpenWrt 25.12.5 apk feed; setup logs
  that package-name error on stderr, then succeeds through the `conntrack`
  fallback package.
- `coreutils-stat` installed successfully on both routers and made the inode
  fallback usable.

## Validation

Static/offline checks passed on 2026-07-25:

- `python3 -m unittest tests.test_mcp_policy -v`
- `sh tests/test_client_conntrack.sh`
- `sh tests/test_inodes.sh`
- `sh tests/test_setup_nlbwmon_optional.sh`
- `python3 -m unittest discover -s tests -p 'test_*.py'`
- `sh -n openwrt/setup.sh openwrt/scripts/*.sh`
- `docker compose config`
- `docker compose --profile mcp config` (output expands private `.env` values;
  do not quote it)
- `sh tests/run_all.sh`
- `git diff --name-only -- grafana-dashboard-exports grafana/provisioning/dashboards`
- `git diff --check`

Live checks passed:

- Direct `/metrics` fetch from both routers succeeded.
- `python3 tests/check_exposition.py /tmp/openwrt-main.metrics`:
  `2888 samples, no duplicate series`.
- `python3 tests/check_exposition.py /tmp/openwrt-new.metrics`:
  `1950 samples, no duplicate series`.

## Remaining risks

- Grafana UI screenshots were not captured in this follow-up; evidence is from
  source, generated-output checks, MCP read-only status, direct metrics fetches,
  and exposition validation.
- `.env` is private and contains real local values. Do not quote secrets from
  Compose profile output or MCP/router diagnostics.
