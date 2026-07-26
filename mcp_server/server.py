"""Streamable HTTP MCP sidecar for guarded OpenWrt SSH operations."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from io import BytesIO
import logging
import os
from pathlib import Path
import socket
import tarfile
import time
from typing import Any

import paramiko
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Mount, Route

from .core import (
    DEFAULT_KNOWN_HOSTS_PATH,
    HOST_KEY_POLICY_AUTO_ADD,
    CommandSpec,
    HostKeyPolicy,
    PolicyError,
    build_configure_syslog_command,
    build_diagnostic_command,
    build_metrics_sample_command,
    build_monitoring_status_command,
    build_restart_monitoring_command,
    build_setup_command,
    build_system_facts_command,
    build_test_log_command,
    host_key_failure_message,
    parse_router_inventory,
    redact,
    require_confirm,
    resolve_host_key_policy,
    resolve_setup_timeout_seconds,
    resolve_router,
    truncate_output,
    validate_port,
)

logger = logging.getLogger(__name__)


DEFAULT_ROUTERS = "openwrt-main=192.168.0.1,openwrt-new=192.168.0.2"
DEFAULT_OUTPUT_LIMIT = 12000


@dataclass(frozen=True)
class Settings:
    routers_raw: str
    username: str
    password: str
    token: str
    allowed_hosts: frozenset[str]
    allowed_origins: frozenset[str]
    ssh_timeout_seconds: int
    setup_timeout_seconds: int
    output_limit: int
    repo_openwrt_dir: Path
    host_key_policy: HostKeyPolicy

    @classmethod
    def from_env(cls) -> "Settings":
        username = os.getenv("OPENWRT_SSH_USERNAME", "").strip()
        password = os.getenv("OPENWRT_SSH_PASSWORD", "")
        token = os.getenv("OPENWRT_MCP_TOKEN", "")
        if not username:
            raise RuntimeError("OPENWRT_SSH_USERNAME is required")
        if not password:
            raise RuntimeError("OPENWRT_SSH_PASSWORD is required")
        if len(token) < 16 or token == "change-me-openwrt-mcp-token":
            raise RuntimeError("OPENWRT_MCP_TOKEN must be set to a non-default value of at least 16 characters")

        host_key_policy = resolve_host_key_policy(
            os.getenv("OPENWRT_MCP_KNOWN_HOSTS", DEFAULT_KNOWN_HOSTS_PATH),
            os.getenv("OPENWRT_MCP_INSECURE_HOST_KEYS"),
        )

        return cls(
            routers_raw=os.getenv("OPENWRT_MCP_ROUTERS", DEFAULT_ROUTERS),
            username=username,
            password=password,
            token=token,
            allowed_hosts=_csv_set(os.getenv("OPENWRT_MCP_ALLOWED_HOSTS", "127.0.0.1,localhost")),
            allowed_origins=_csv_set(os.getenv("OPENWRT_MCP_ALLOWED_ORIGINS", "")),
            ssh_timeout_seconds=_int_env("OPENWRT_MCP_SSH_TIMEOUT", 15),
            setup_timeout_seconds=resolve_setup_timeout_seconds(os.getenv("OPENWRT_MCP_SETUP_TIMEOUT")),
            output_limit=_int_env("OPENWRT_MCP_OUTPUT_LIMIT", DEFAULT_OUTPUT_LIMIT),
            repo_openwrt_dir=Path(os.getenv("OPENWRT_MCP_OPENWRT_DIR", "/app/openwrt")),
            host_key_policy=host_key_policy,
        )


def _csv_set(raw: str) -> frozenset[str]:
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return int(value)


settings = Settings.from_env()
routers = parse_router_inventory(settings.routers_raw)
mcp = FastMCP(
    "openwrt_ssh_mcp",
    stateless_http=True,
    json_response=True,
    streamable_http_path="/mcp",
)


class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Any) -> Response:
        if request.url.path == "/healthz":
            return await call_next(request)

        host_header = request.headers.get("host", "")
        host = host_header.rsplit(":", 1)[0] if ":" in host_header else host_header
        if settings.allowed_hosts and host not in settings.allowed_hosts:
            return JSONResponse({"error": "host not allowed"}, status_code=403)

        origin = request.headers.get("origin")
        if origin and origin not in settings.allowed_origins:
            return JSONResponse({"error": "origin not allowed"}, status_code=403)

        expected = f"Bearer {settings.token}"
        if request.headers.get("authorization") != expected:
            return JSONResponse({"error": "authorization required"}, status_code=401)

        return await call_next(request)


class SSHRunner:
    def __init__(self, cfg: Settings):
        self.cfg = cfg

    def run(self, router_name: str, spec: CommandSpec) -> dict[str, Any]:
        router = resolve_router(routers, router_name)
        started = time.monotonic()
        client = self._connect(router.host, router.port)
        try:
            stdin, stdout, stderr = client.exec_command(spec.command, timeout=spec.timeout_seconds)
            stdin.close()
            stdout.channel.settimeout(spec.timeout_seconds)
            exit_code = stdout.channel.recv_exit_status()
            out = stdout.read().decode("utf-8", "replace")
            err = stderr.read().decode("utf-8", "replace")
        except (socket.timeout, TimeoutError):
            message = spec.timeout_message or f"command timed out after {spec.timeout_seconds} seconds"
            return self._result(
                router_name,
                spec,
                124,
                "",
                message,
                started,
                extra={"timed_out": True, "timeout_seconds": spec.timeout_seconds},
            )
        finally:
            client.close()
        return self._result(router_name, spec, exit_code, out, err, started)

    def stage_openwrt_payload(self, router_name: str) -> dict[str, Any]:
        if not self.cfg.repo_openwrt_dir.is_dir():
            raise PolicyError(f"OpenWrt payload directory not found: {self.cfg.repo_openwrt_dir}")
        router = resolve_router(routers, router_name)
        tar_bytes = _build_openwrt_tar(self.cfg.repo_openwrt_dir)
        spec = CommandSpec(
            command="rm -rf /tmp/openwrt && tar -x -C /tmp",
            timeout_seconds=90,
            mutating=True,
            description="Stage repo OpenWrt payload under /tmp/openwrt.",
        )
        started = time.monotonic()
        client = self._connect(router.host, router.port)
        try:
            channel = client.get_transport().open_session(timeout=spec.timeout_seconds)
            channel.settimeout(spec.timeout_seconds)
            channel.exec_command(spec.command)
            channel.sendall(tar_bytes)
            channel.shutdown_write()
            out_chunks: list[bytes] = []
            err_chunks: list[bytes] = []
            while not channel.exit_status_ready():
                if channel.recv_ready():
                    out_chunks.append(channel.recv(4096))
                if channel.recv_stderr_ready():
                    err_chunks.append(channel.recv_stderr(4096))
                time.sleep(0.05)
            while channel.recv_ready():
                out_chunks.append(channel.recv(4096))
            while channel.recv_stderr_ready():
                err_chunks.append(channel.recv_stderr(4096))
            exit_code = channel.recv_exit_status()
        finally:
            client.close()
        return self._result(
            router_name,
            spec,
            exit_code,
            b"".join(out_chunks).decode("utf-8", "replace"),
            b"".join(err_chunks).decode("utf-8", "replace"),
            started,
            extra={"payload_bytes": len(tar_bytes)},
        )

    def _connect(self, host: str, port: int) -> paramiko.SSHClient:
        client = paramiko.SSHClient()
        policy = self.cfg.host_key_policy
        known_hosts = Path(policy.known_hosts_path)
        if policy.policy == HOST_KEY_POLICY_AUTO_ADD:
            logger.warning(
                "OPENWRT_MCP_INSECURE_HOST_KEYS enabled: accepting any SSH host "
                "key for %s:%s (MITM risk; password auth still in use)",
                host,
                port,
            )
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        else:
            if known_hosts.is_file():
                client.load_host_keys(str(known_hosts))
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
        try:
            client.connect(
                hostname=host,
                port=port,
                username=self.cfg.username,
                password=self.cfg.password,
                look_for_keys=False,
                allow_agent=False,
                timeout=self.cfg.ssh_timeout_seconds,
                banner_timeout=self.cfg.ssh_timeout_seconds,
                auth_timeout=self.cfg.ssh_timeout_seconds,
            )
        except paramiko.BadHostKeyException as exc:
            raise PolicyError(
                host_key_failure_message(host, port, policy.known_hosts_path)
            ) from exc
        except paramiko.SSHException as exc:
            if "not found in known_hosts" in str(exc).lower():
                raise PolicyError(
                    host_key_failure_message(host, port, policy.known_hosts_path)
                ) from exc
            raise
        return client

    def _result(
        self,
        router_name: str,
        spec: CommandSpec,
        exit_code: int,
        stdout: str,
        stderr: str,
        started: float,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        stdout = redact(stdout, (self.cfg.password, self.cfg.token))
        stderr = redact(stderr, (self.cfg.password, self.cfg.token))
        stdout, stdout_truncated = truncate_output(stdout, self.cfg.output_limit)
        stderr, stderr_truncated = truncate_output(stderr, self.cfg.output_limit)
        result: dict[str, Any] = {
            "router": router_name,
            "description": spec.description,
            "exit_code": exit_code,
            "ok": exit_code == 0,
            "stdout": stdout,
            "stderr": stderr,
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
            "duration_ms": round((time.monotonic() - started) * 1000),
        }
        if extra:
            result.update(extra)
        return result


runner = SSHRunner(settings)


@mcp.tool()
def openwrt_list_routers() -> dict[str, Any]:
    """List the OpenWrt routers this MCP server is allowed to access."""

    return {"routers": [asdict(router) for router in routers.values()]}


@mcp.tool()
def openwrt_ssh_check(router: str) -> dict[str, Any]:
    """Verify SSH login to an allowed OpenWrt router."""

    spec = CommandSpec(command="printf 'ssh_ok=1\\nhostname='; hostname", description="Verify SSH connectivity.")
    return runner.run(router, spec)


@mcp.tool()
def openwrt_system_facts(router: str) -> dict[str, Any]:
    """Read OpenWrt release, kernel, uptime, and board facts."""

    return runner.run(router, build_system_facts_command())


@mcp.tool()
def openwrt_monitoring_status(router: str) -> dict[str, Any]:
    """Read exporter, syslog, cron, and local metrics health state."""

    return runner.run(router, build_monitoring_status_command())


@mcp.tool()
def openwrt_metrics_sample(router: str, limit_lines: int = 120) -> dict[str, Any]:
    """Read a bounded sample of the router-local Prometheus metrics endpoint."""

    return runner.run(router, build_metrics_sample_command(limit_lines))


@mcp.tool()
def openwrt_diagnostic(router: str, diagnostic: str) -> dict[str, Any]:
    """Run a read-only diagnostic by enum: routes, interfaces, wifi, dhcp, firewall_counters, collector_success, conntrack_sources, disk, inode_sources, processes."""

    return runner.run(router, build_diagnostic_command(diagnostic))


@mcp.tool()
def openwrt_send_test_log(
    router: str,
    confirm: bool,
    message: str = "test message from OpenWrt SSH MCP",
    tag: str = "openwrt-ssh-mcp",
) -> dict[str, Any]:
    """Send a bounded test syslog message. Requires confirm=true."""

    spec = build_test_log_command(message, tag)
    require_confirm(confirm, spec)
    return runner.run(router, spec)


@mcp.tool()
def openwrt_restart_monitoring(router: str, target: str, confirm: bool) -> dict[str, Any]:
    """Restart monitoring-related services only. target: exporter, log, cron, all. Requires confirm=true."""

    spec = build_restart_monitoring_command(target)
    require_confirm(confirm, spec)
    return runner.run(router, spec)


@mcp.tool()
def openwrt_configure_syslog(router: str, monitoring_host: str, port: int = 514, proto: str = "udp", confirm: bool = False) -> dict[str, Any]:
    """Configure OpenWrt remote syslog destination and restart logd. Requires confirm=true."""

    spec = build_configure_syslog_command(monitoring_host, port, proto)
    require_confirm(confirm, spec)
    return runner.run(router, spec)


@mcp.tool()
def openwrt_run_repo_setup(
    router: str,
    monitoring_host: str,
    confirm: bool,
    profile: str = "core",
    traffic_lan_interface: str = "",
    syslog_port: int = 514,
    syslog_proto: str = "udp",
) -> dict[str, Any]:
    """Stage this repo's openwrt/ payload and run setup.sh. Requires confirm=true."""

    spec = build_setup_command(
        monitoring_host,
        profile=profile,
        traffic_lan_interface=traffic_lan_interface,
        syslog_port=syslog_port,
        syslog_proto=syslog_proto,
        timeout_seconds=settings.setup_timeout_seconds,
    )
    require_confirm(confirm, spec)
    stage = runner.stage_openwrt_payload(router)
    if not stage["ok"]:
        return {"stage": stage, "setup": None, "ok": False}
    setup = runner.run(router, spec)
    return {"stage": stage, "setup": setup, "ok": bool(stage["ok"] and setup["ok"])}


async def healthz(request: Request) -> PlainTextResponse:
    return PlainTextResponse("ok\n")


@asynccontextmanager
async def lifespan(app: Starlette):
    async with mcp.session_manager.run():
        yield


app = Starlette(
    routes=[
        Route("/healthz", healthz, methods=["GET"]),
        Mount("/", app=mcp.streamable_http_app()),
    ],
    middleware=[Middleware(SecurityMiddleware)],
    lifespan=lifespan,
)


def _build_openwrt_tar(source: Path) -> bytes:
    buffer = BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        archive.add(source, arcname="openwrt")
    return buffer.getvalue()


def main() -> None:
    import uvicorn

    host = os.getenv("OPENWRT_MCP_BIND", "127.0.0.1")
    port = validate_port(int(os.getenv("OPENWRT_MCP_PORT", "8033")))
    socket.getaddrinfo(host, port)
    uvicorn.run("mcp_server.server:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
