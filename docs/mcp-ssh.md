# SSH MCP sidecar

This repo includes an optional MCP sidecar that lets Claude Code, Codex, and OpenCode use the same guarded SSH access path to your OpenWrt routers.

The sidecar is intentionally not a raw shell server. It only exposes allowlisted diagnostics and monitoring operations for configured routers.

## Security model

- MCP transport: Streamable HTTP at `http://127.0.0.1:8033/mcp` by default.
- MCP client auth: static bearer token in the `Authorization` header.
- Router auth: one OpenWrt SSH username/password from untracked `.env`.
- Router allowlist: labels from `OPENWRT_MCP_ROUTERS`; defaults to `openwrt-main=192.168.0.1,openwrt-new=192.168.0.2`.
- SSH host keys: verified against an operator-managed `known_hosts` file (RejectPolicy). Missing or mismatched keys fail closed with an actionable error.
- HTTP bind: localhost-only on the Docker host by default.
- Mutations require `confirm=true`.
- No arbitrary shell, arbitrary host, package upgrade, sysupgrade, firewall/network restart, or secret-reading tools.

Host-key pinning is what makes the router allowlist meaningful: without it, a LAN attacker who answers on the router IP can harvest `OPENWRT_SSH_PASSWORD` and feed arbitrary command output back to the sidecar. Keep the MCP port bound to localhost or a trusted private network either way.

SSH public-key auth is not wired yet (`look_for_keys=False`); password auth is still required. Pinning host keys reduces MITM risk but the password still crosses the wire on every call.

## Router user

Create a dedicated OpenWrt user if your firmware supports it, then grant only the privileges you are comfortable with. If your router only supports practical administration through `root`, use a strong unique password and keep the MCP endpoint localhost/private only.

The guarded tools need enough privilege to run:

- `/etc/init.d/prometheus-node-exporter-lua status|restart`
- `/etc/init.d/log status|restart`
- `/etc/init.d/cron status|restart`
- `uci show` and selected `uci set`/`uci commit system`
- `uci get network.lan.ipaddr` and `wget -qO- http://<lan-ip>:9100/metrics`
- `logger`
- read-only diagnostics such as `ip`, `df`, `nft`, redacted `wifi`, `iw`, `logread`
- optional setup staging under `/tmp/openwrt` and `sh /tmp/openwrt/setup.sh <monitoring_host>`

## Configure and start

Copy `.env.example` to `.env` and set real values:

```sh
OPENWRT_MCP_TOKEN=<long-random-token>
OPENWRT_SSH_USERNAME=<router-ssh-user>
OPENWRT_SSH_PASSWORD=<router-ssh-password>
OPENWRT_MCP_ROUTERS=openwrt-main=192.168.0.1,openwrt-new=192.168.0.2
```

### SSH known_hosts (required by default)

Populate the host-side `known_hosts` file (mounted read-only at
`/app/known_hosts` in the container) before the first SSH tool call:

```sh
ssh-keyscan -H 192.168.0.1 >> known_hosts
ssh-keyscan -H 192.168.0.2 >> known_hosts
```

Related env vars:

| Variable | Default | Purpose |
| --- | --- | --- |
| `OPENWRT_MCP_KNOWN_HOSTS_FILE` | `./known_hosts` | Host path bind-mounted into the container |
| `OPENWRT_MCP_KNOWN_HOSTS` | `/app/known_hosts` | Path the sidecar loads inside the container |
| `OPENWRT_MCP_INSECURE_HOST_KEYS` | empty/off | Set to `1` only to fall back to AutoAddPolicy (logs a warning on every connection) |
| `OPENWRT_MCP_SETUP_TIMEOUT` | `600` | Timeout in seconds for `openwrt_run_repo_setup`; separate from the fast SSH/read-only timeout |

Do not enable `OPENWRT_MCP_INSECURE_HOST_KEYS` unless the monitoring host and
LAN path to the routers are fully trusted. It disables host-key verification
and restores the pre-fix MITM exposure.

Start only the MCP sidecar:

```sh
docker compose --profile mcp up -d --build openwrt-ssh-mcp
docker compose --profile mcp ps openwrt-ssh-mcp
curl http://127.0.0.1:8033/healthz
```

The Compose port mapping is `127.0.0.1:${OPENWRT_MCP_PORT:-8033}:8033`. To expose it beyond localhost, change the port binding deliberately and update `OPENWRT_MCP_ALLOWED_HOSTS` / `OPENWRT_MCP_ALLOWED_ORIGINS`.

## Available tools

- `openwrt_list_routers`
- `openwrt_ssh_check`
- `openwrt_system_facts`
- `openwrt_monitoring_status`
- `openwrt_metrics_sample`
- `openwrt_diagnostic`
- `openwrt_send_test_log` — requires `confirm=true`
- `openwrt_restart_monitoring` — requires `confirm=true`
- `openwrt_configure_syslog` — requires `confirm=true`
- `openwrt_run_repo_setup` — requires `confirm=true`

`openwrt_run_repo_setup` stages this repository's `openwrt/` payload and then
runs `setup.sh`, so it can take several minutes on a fresh router or slow
package mirror. If it times out, treat the router as potentially partially
configured and run `openwrt_monitoring_status` next as a read-only check before
rerunning setup or changing router state.

The `profile` argument follows `OPENWRT_MONITOR_PROFILE`: use `core`, `traffic`,
`wifi_mesh`, `dpi`, `clients`, a comma-separated list of non-`full` profiles, or
the single word `full`. The MCP policy rejects `full` combined with any other
profile before staging files on the router.

Allowed diagnostics for `openwrt_diagnostic`:

```text
routes
interfaces
wifi
dhcp
firewall_counters
collector_success
conntrack_sources
disk
inode_sources
processes
```

## Claude Code

```sh
claude mcp add --scope local --transport http openwrt-ssh \
  http://127.0.0.1:8033/mcp \
  --header "Authorization: Bearer $OPENWRT_MCP_TOKEN"
```

Inside Claude Code, use `/mcp` to verify the server and then ask for a read-only check first, for example:

```text
Use openwrt-ssh to run openwrt_ssh_check on openwrt-main.
```

## Codex

```sh
export OPENWRT_MCP_TOKEN=<long-random-token>
codex mcp add openwrt-ssh \
  --url http://127.0.0.1:8033/mcp \
  --bearer-token-env-var OPENWRT_MCP_TOKEN
codex mcp list
```

## OpenCode

Add this to `opencode.json`:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "openwrt-ssh": {
      "type": "remote",
      "url": "http://127.0.0.1:8033/mcp",
      "oauth": false,
      "enabled": true,
      "headers": {
        "Authorization": "Bearer {env:OPENWRT_MCP_TOKEN}"
      }
    }
  }
}
```

## Validation sequence

Start with read-only tools:

1. `openwrt_list_routers`
2. `openwrt_ssh_check(router="openwrt-main")`
3. `openwrt_ssh_check(router="openwrt-new")`
4. `openwrt_monitoring_status(router="openwrt-main")`
5. `openwrt_metrics_sample(router="openwrt-main", limit_lines=80)`

Only after the read-only checks pass, test one guarded operation:

```text
Call openwrt_send_test_log with router="openwrt-main", confirm=true, message="mcp smoke test".
```

Do not run `openwrt_run_repo_setup` or restart services during validation unless you intend to change live router state.
