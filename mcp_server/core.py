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

DIAGNOSTIC_COMMANDS: dict[str, str] = {
    "routes": "ip route; ip -6 route 2>/dev/null | head -80",
    "interfaces": "ip -brief addr 2>/dev/null || ip addr; printf '\\n/proc/net/dev\\n'; cat /proc/net/dev",
    "wifi": "wifi status 2>/dev/null | head -c 12000; printf '\\n'; iw dev 2>/dev/null",
    "dhcp": "uci -q show dhcp; printf '\\nRecent DHCP logs\\n'; logread 2>/dev/null | grep -E 'dnsmasq|odhcpd|DHCP' | tail -80",
    "firewall_counters": "nft list counters 2>/dev/null | head -200; printf '\\nRuleset counters\\n'; nft list ruleset 2>/dev/null | grep -E 'counter|chain|table' | head -200",
    "collector_success": "wget -qO- http://127.0.0.1:9100/metrics 2>/dev/null | grep '^node_scrape_collector_success' | head -120",
    "disk": "df -h; printf '\\nInodes\\n'; df -i 2>/dev/null; printf '\\nMounts\\n'; mount",
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
            "wget -qO- http://127.0.0.1:9100/metrics 2>/dev/null "
            "| grep -E '^(node_openwrt_info|node_scrape_collector_success|openwrt_.*collector_available)' "
            "| head -80"
        ),
        description="Read monitoring service, UCI, cron, and metrics status.",
    )


def build_metrics_sample_command(limit_lines: int = 120) -> CommandSpec:
    if limit_lines < 1 or limit_lines > 500:
        raise PolicyError("limit_lines must be between 1 and 500")
    return CommandSpec(
        command=f"wget -qO- http://127.0.0.1:9100/metrics 2>/dev/null | sed -n '1,{limit_lines}p'",
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
) -> CommandSpec:
    monitoring_host = validate_host(monitoring_host)
    syslog_port = validate_port(syslog_port)
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
        timeout_seconds=180,
        mutating=True,
        description="Run this repo's OpenWrt setup script with validated environment.",
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

