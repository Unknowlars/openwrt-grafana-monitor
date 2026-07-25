"""Policy and command construction for the OpenWrt SSH MCP server.

This module intentionally has no MCP or SSH dependencies so its safety rules can
be tested without network access.
"""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import re
import shlex
from typing import Iterable


LABEL_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
HOST_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")
PROFILE_RE = re.compile(r"^[A-Za-z0-9_,.-]+$")
TAG_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

ALLOWED_PROFILES = {"core", "clients", "traffic", "wifi_mesh", "dpi", "full"}
ALLOWED_SYSLOG_PROTOCOLS = {"udp", "tcp"}
DEFAULT_KNOWN_HOSTS_PATH = "/app/known_hosts"
DEFAULT_SETUP_TIMEOUT_SECONDS = 600
HOST_KEY_POLICY_REJECT = "RejectPolicy"
HOST_KEY_POLICY_AUTO_ADD = "AutoAddPolicy"
SETUP_TIMEOUT_MESSAGE = (
    "openwrt_run_repo_setup timed out while running setup.sh; the router may be "
    "partially configured. Run openwrt_monitoring_status as the next read-only "
    "check before rerunning setup or making additional router changes."
)
METRICS_URL_COMMAND = (
    "LAN_IP=$(uci -q get network.lan.ipaddr 2>/dev/null); "
    "LAN_IP=${LAN_IP%%/*}; "
    "LAN_IP=${LAN_IP:-127.0.0.1}; "
    "METRICS_URL=http://$LAN_IP:9100/metrics"
)
SAFE_WIFI_STATUS_COMMAND = (
    "wifi status 2>/dev/null | "
    "awk '"
    "/\"(key|password|passwd|psk|sae_password|wps_pin)\"[[:space:]]*:/ { "
    "sub(/:.*/, \": \\\"[redacted]\\\",\"); print; next } "
    "{ print }"
    "' | head -c 12000"
)
CONNTRACK_SOURCES_COMMAND = (
    "printf 'conntrack_cli='; "
    "if command -v conntrack >/dev/null 2>&1; then printf 'present\\n'; else printf 'missing\\n'; fi; "
    "printf 'conntrack_cli_rows='; "
    "if command -v conntrack >/dev/null 2>&1; then conntrack -L 2>/dev/null | wc -l; else printf '0\\n'; fi; "
    "for f in /proc/net/nf_conntrack /proc/net/ip_conntrack; do "
    "printf '%s=' \"$f\"; "
    "if [ -r \"$f\" ]; then wc -l < \"$f\" 2>/dev/null; else printf 'unreadable\\n'; fi; "
    "done; "
    "printf 'getHostHints_bytes='; ubus call luci-rpc getHostHints 2>/dev/null | wc -c; "
    "printf 'wireless_status_bytes='; ubus call network.wireless status 2>/dev/null | wc -c"
)
INODE_SOURCES_COMMAND = (
    "printf 'df_iP='; "
    "if df -iP / >/dev/null 2>&1; then printf 'present\\n'; else printf 'unavailable\\n'; fi; "
    "printf 'stat_bin='; "
    "if command -v stat >/dev/null 2>&1; then printf 'present\\n'; else printf 'missing\\n'; fi; "
    "printf 'stat_f_tmp='; "
    "if command -v stat >/dev/null 2>&1; then stat -f -c '%c %d' /tmp 2>/dev/null || printf 'unavailable\\n'; else printf 'unavailable\\n'; fi; "
    "printf 'mount_overlay='; [ -e /overlay ] && printf 'present\\n' || printf 'missing\\n'; "
    "printf 'mount_tmp='; [ -e /tmp ] && printf 'present\\n' || printf 'missing\\n'"
)

DIAGNOSTIC_COMMANDS: dict[str, str] = {
    "routes": "ip route; ip -6 route 2>/dev/null | head -80",
    "interfaces": "ip -brief addr 2>/dev/null || ip addr; printf '\\n/proc/net/dev\\n'; cat /proc/net/dev",
    "wifi": f"{SAFE_WIFI_STATUS_COMMAND}; printf '\\n'; iw dev 2>/dev/null",
    "dhcp": "uci -q show dhcp; printf '\\nRecent DHCP logs\\n'; logread 2>/dev/null | grep -E 'dnsmasq|odhcpd|DHCP' | tail -80",
    "firewall_counters": "nft list counters 2>/dev/null | head -200; printf '\\nRuleset counters\\n'; nft list ruleset 2>/dev/null | grep -E 'counter|chain|table' | head -200",
    "collector_success": f"{METRICS_URL_COMMAND}; wget -qO- \"$METRICS_URL\" 2>/dev/null | grep '^node_scrape_collector_success' | head -120",
    "conntrack_sources": CONNTRACK_SOURCES_COMMAND,
    "disk": "df -h; printf '\\nInodes\\n'; df -i 2>/dev/null; printf '\\nMounts\\n'; mount",
    "inode_sources": INODE_SOURCES_COMMAND,
    "processes": "ps; printf '\\nListening sockets\\n'; netstat -lntup 2>/dev/null || ss -lntup 2>/dev/null",
}

MONITORING_SERVICE_COMMANDS: dict[str, str] = {
    "exporter": "/etc/init.d/prometheus-node-exporter-lua restart",
    "log": "/etc/init.d/log restart",
    "cron": "/etc/init.d/cron restart",
    "all": "/etc/init.d/prometheus-node-exporter-lua restart; /etc/init.d/log restart; /etc/init.d/cron restart",
}


class PolicyError(ValueError):
    """Raised when a requested MCP action violates the command policy."""


@dataclass(frozen=True)
class Router:
    name: str
    host: str
    port: int = 22


@dataclass(frozen=True)
class CommandSpec:
    command: str
    timeout_seconds: int = 15
    mutating: bool = False
    description: str = ""
    timeout_message: str = ""


@dataclass(frozen=True)
class HostKeyPolicy:
    """SSH host-key verification policy for the MCP sidecar.

    ``policy`` is the paramiko policy class name: RejectPolicy (default) or
    AutoAddPolicy (only when insecure mode is explicitly enabled).
    """

    policy: str
    known_hosts_path: str
    insecure: bool


def resolve_host_key_policy(
    known_hosts_path: str | None = None,
    insecure_raw: str | None = None,
) -> HostKeyPolicy:
    """Resolve host-key policy from config values (not env lookup).

    Default is strict RejectPolicy. AutoAddPolicy requires an explicit truthy
    insecure flag (``1``/``true``/``yes``/``on``).
    """

    path = (known_hosts_path or "").strip() or DEFAULT_KNOWN_HOSTS_PATH
    insecure = _env_flag(insecure_raw)
    if insecure:
        return HostKeyPolicy(
            policy=HOST_KEY_POLICY_AUTO_ADD,
            known_hosts_path=path,
            insecure=True,
        )
    return HostKeyPolicy(
        policy=HOST_KEY_POLICY_REJECT,
        known_hosts_path=path,
        insecure=False,
    )


def resolve_setup_timeout_seconds(raw: str | None = None) -> int:
    """Resolve the setup command timeout from an env-style string."""

    value = (raw or "").strip()
    if not value:
        return DEFAULT_SETUP_TIMEOUT_SECONDS
    try:
        timeout_seconds = int(value)
    except ValueError as exc:
        raise PolicyError("OPENWRT_MCP_SETUP_TIMEOUT must be an integer") from exc
    if timeout_seconds < 1:
        raise PolicyError("OPENWRT_MCP_SETUP_TIMEOUT must be positive")
    return timeout_seconds


def host_key_failure_message(host: str, port: int, known_hosts_path: str) -> str:
    """Actionable error when SSH host-key verification fails."""

    return (
        f"SSH host key verification failed for {host}:{port}. "
        f"Add the router key to the operator-managed known_hosts file "
        f"({known_hosts_path}), for example: "
        f"ssh-keyscan -H {host} >> known_hosts "
        f"and mount that file read-only at {known_hosts_path}. "
        f"Only on a trusted network, set OPENWRT_MCP_INSECURE_HOST_KEYS=1 to "
        f"disable host-key verification (not recommended)."
    )


def parse_router_inventory(raw: str) -> dict[str, Router]:
    """Parse `name=host[:port],name=host[:port]` into an allowlist."""

    routers: dict[str, Router] = {}
    raw = (raw or "").strip()
    if not raw:
        raise PolicyError("OPENWRT_MCP_ROUTERS must not be empty")

    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            raise PolicyError("router inventory contains an empty entry")
        if "=" not in entry:
            raise PolicyError(f"invalid router entry {entry!r}; expected name=host[:port]")
        name, address = entry.split("=", 1)
        name = name.strip()
        address = address.strip()
        if not LABEL_RE.fullmatch(name):
            raise PolicyError(f"invalid router name {name!r}")
        host, port = _parse_host_port(address)
        if name in routers:
            raise PolicyError(f"duplicate router name {name!r}")
        routers[name] = Router(name=name, host=host, port=port)
    return routers


def resolve_router(routers: dict[str, Router], router: str) -> Router:
    try:
        return routers[router]
    except KeyError as exc:
        allowed = ", ".join(sorted(routers))
        raise PolicyError(f"unknown router {router!r}; allowed routers: {allowed}") from exc


def build_system_facts_command() -> CommandSpec:
    return CommandSpec(
        command=(
            "printf 'OpenWrt release\\n'; "
            "cat /etc/openwrt_release 2>/dev/null; "
            "printf '\\nKernel\\n'; uname -a; "
            "printf '\\nUptime\\n'; uptime; "
            "printf '\\nBoard\\n'; "
            "cat /tmp/sysinfo/model 2>/dev/null; "
            "cat /tmp/sysinfo/board_name 2>/dev/null; "
            "ubus call system board 2>/dev/null | head -c 4096"
        ),
        description="Read OpenWrt release, kernel, uptime, and board facts.",
    )


def build_monitoring_status_command() -> CommandSpec:
    return CommandSpec(
        command=(
            "printf 'Exporter service\\n'; "
            "/etc/init.d/prometheus-node-exporter-lua status 2>&1; "
            "printf '\\nExporter UCI\\n'; "
            "uci -q show prometheus-node-exporter-lua; "
            "printf '\\nSyslog UCI\\n'; "
            "uci -q show system | grep 'log_'; "
            "printf '\\nCron service\\n'; "
            "/etc/init.d/cron status 2>&1; "
            "printf '\\nMetrics health sample\\n'; "
            f"{METRICS_URL_COMMAND}; "
            "wget -qO- \"$METRICS_URL\" 2>/dev/null "
            "| grep -E '^(node_openwrt_info|node_scrape_collector_success|openwrt_.*collector_available)' "
            "| head -80"
        ),
        description="Read monitoring service, UCI, cron, and metrics status.",
    )


def build_metrics_sample_command(limit_lines: int = 120) -> CommandSpec:
    if limit_lines < 1 or limit_lines > 500:
        raise PolicyError("limit_lines must be between 1 and 500")
    return CommandSpec(
        command=f"{METRICS_URL_COMMAND}; wget -qO- \"$METRICS_URL\" 2>/dev/null | sed -n '1,{limit_lines}p'",
        description="Read a bounded local exporter metrics sample.",
    )


def build_diagnostic_command(kind: str) -> CommandSpec:
    if kind not in DIAGNOSTIC_COMMANDS:
        allowed = ", ".join(sorted(DIAGNOSTIC_COMMANDS))
        raise PolicyError(f"invalid diagnostic {kind!r}; allowed diagnostics: {allowed}")
    return CommandSpec(
        command=DIAGNOSTIC_COMMANDS[kind],
        description=f"Run read-only diagnostic {kind}.",
    )


def build_test_log_command(message: str, tag: str = "openwrt-ssh-mcp") -> CommandSpec:
    message = (message or "test message from OpenWrt SSH MCP").strip()
    tag = (tag or "openwrt-ssh-mcp").strip()
    if len(message) > 240:
        raise PolicyError("log message must be 240 characters or shorter")
    if not TAG_RE.fullmatch(tag):
        raise PolicyError("log tag may contain only letters, digits, underscore, dot, and hyphen")
    return CommandSpec(
        command=f"logger -t {shlex.quote(tag)} {shlex.quote(message)}",
        mutating=True,
        description="Send a bounded test log message through OpenWrt logd.",
    )


def build_restart_monitoring_command(target: str) -> CommandSpec:
    if target not in MONITORING_SERVICE_COMMANDS:
        allowed = ", ".join(sorted(MONITORING_SERVICE_COMMANDS))
        raise PolicyError(f"invalid restart target {target!r}; allowed targets: {allowed}")
    return CommandSpec(
        command=MONITORING_SERVICE_COMMANDS[target],
        timeout_seconds=30,
        mutating=True,
        description=f"Restart monitoring service target {target}.",
    )


def build_configure_syslog_command(host: str, port: int, proto: str) -> CommandSpec:
    host = validate_host(host)
    port = validate_port(port)
    proto = proto.lower().strip()
    if proto not in ALLOWED_SYSLOG_PROTOCOLS:
        raise PolicyError("syslog proto must be udp or tcp")
    return CommandSpec(
        command=(
            f"uci set system.@system[0].log_ip={shlex.quote(host)}; "
            f"uci set system.@system[0].log_port={shlex.quote(str(port))}; "
            f"uci set system.@system[0].log_proto={shlex.quote(proto)}; "
            "uci commit system; "
            "/etc/init.d/log restart; "
            "uci -q show system | grep 'log_'"
        ),
        timeout_seconds=30,
        mutating=True,
        description="Configure OpenWrt remote syslog and restart logd.",
    )


def build_setup_command(
    monitoring_host: str,
    *,
    profile: str = "core",
    traffic_lan_interface: str = "",
    syslog_port: int = 514,
    syslog_proto: str = "udp",
    timeout_seconds: int = DEFAULT_SETUP_TIMEOUT_SECONDS,
) -> CommandSpec:
    monitoring_host = validate_host(monitoring_host)
    syslog_port = validate_port(syslog_port)
    timeout_seconds = int(timeout_seconds)
    if timeout_seconds < 1:
        raise PolicyError("setup timeout must be positive")
    syslog_proto = syslog_proto.lower().strip()
    if syslog_proto not in ALLOWED_SYSLOG_PROTOCOLS:
        raise PolicyError("syslog proto must be udp or tcp")

    profile = normalize_profile(profile)
    env_parts = [
        f"OPENWRT_MONITOR_PROFILE={shlex.quote(profile)}",
        f"SYSLOG_PORT={shlex.quote(str(syslog_port))}",
        f"SYSLOG_PROTO={shlex.quote(syslog_proto)}",
    ]
    if traffic_lan_interface:
        env_parts.append(f"TRAFFIC_LAN_INTERFACE={shlex.quote(validate_interface(traffic_lan_interface))}")
    return CommandSpec(
        command=f"{' '.join(env_parts)} sh /tmp/openwrt/setup.sh {shlex.quote(monitoring_host)}",
        timeout_seconds=timeout_seconds,
        mutating=True,
        description="Run this repo's OpenWrt setup script with validated environment.",
        timeout_message=SETUP_TIMEOUT_MESSAGE,
    )


def normalize_profile(profile: str) -> str:
    profile = (profile or "core").strip()
    if not PROFILE_RE.fullmatch(profile):
        raise PolicyError("profile contains invalid characters")
    parts = [part.strip() for part in profile.split(",") if part.strip()]
    if not parts:
        raise PolicyError("profile must not be empty")
    invalid = [part for part in parts if part not in ALLOWED_PROFILES]
    if invalid:
        raise PolicyError(f"invalid profile(s): {', '.join(invalid)}")
    if "full" in parts and len(parts) > 1:
        raise PolicyError("profile 'full' cannot be combined with other profile names")
    return ",".join(parts)


def validate_interface(value: str) -> str:
    value = value.strip()
    if not value or not re.fullmatch(r"^[A-Za-z0-9_.:-]+$", value):
        raise PolicyError("interface contains invalid characters")
    return value


def validate_host(value: str) -> str:
    value = (value or "").strip()
    if not value or not HOST_RE.fullmatch(value):
        raise PolicyError("host contains invalid characters")
    if "/" in value or "@" in value:
        raise PolicyError("host must be a hostname or IP address, not a network or login string")
    try:
        ipaddress.ip_address(value)
    except ValueError:
        if ".." in value or value.startswith("-") or value.endswith("-"):
            raise PolicyError("invalid hostname")
    return value


def validate_port(value: int) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise PolicyError("port must be an integer") from exc
    if port < 1 or port > 65535:
        raise PolicyError("port must be between 1 and 65535")
    return port


def require_confirm(confirm: bool, spec: CommandSpec) -> None:
    if spec.mutating and confirm is not True:
        raise PolicyError("confirm=true is required for this mutating operation")


def truncate_output(value: str, limit: int) -> tuple[str, bool]:
    if limit < 1:
        raise PolicyError("output limit must be positive")
    if len(value) <= limit:
        return value, False
    return value[:limit] + "\n[truncated]", True


def redact(value: str, secrets: Iterable[str]) -> str:
    redacted = value
    for secret in secrets:
        secret = secret or ""
        if len(secret) >= 4:
            redacted = redacted.replace(secret, "[redacted]")
    return redacted


def _parse_host_port(address: str) -> tuple[str, int]:
    if not address:
        raise PolicyError("router address must not be empty")
    host = address
    port = 22
    if address.count(":") == 1:
        maybe_host, maybe_port = address.rsplit(":", 1)
        if maybe_port.isdigit():
            host = maybe_host
            port = validate_port(int(maybe_port))
    return validate_host(host), port


def _env_flag(raw: str | None) -> bool:
    return (raw or "").strip().lower() in {"1", "true", "yes", "on"}
