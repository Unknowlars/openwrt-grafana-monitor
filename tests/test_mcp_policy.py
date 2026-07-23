#!/usr/bin/env python3
"""Safety-policy tests for the OpenWrt SSH MCP server."""

from __future__ import annotations

import unittest

from mcp_server.core import (
    PolicyError,
    build_configure_syslog_command,
    build_diagnostic_command,
    build_restart_monitoring_command,
    build_setup_command,
    build_test_log_command,
    parse_router_inventory,
    require_confirm,
)


class RouterInventoryTests(unittest.TestCase):
    def test_parse_expected_dual_router_inventory(self) -> None:
        routers = parse_router_inventory("openwrt-main=192.168.0.1,openwrt-new=192.168.0.2:2222")

        self.assertEqual(routers["openwrt-main"].host, "192.168.0.1")
        self.assertEqual(routers["openwrt-main"].port, 22)
        self.assertEqual(routers["openwrt-new"].host, "192.168.0.2")
        self.assertEqual(routers["openwrt-new"].port, 2222)

    def test_rejects_bad_router_label(self) -> None:
        with self.assertRaises(PolicyError):
            parse_router_inventory("bad/router=192.168.0.1")

    def test_rejects_login_string_host(self) -> None:
        with self.assertRaises(PolicyError):
            parse_router_inventory("router=root@192.168.0.1")


class CommandPolicyTests(unittest.TestCase):
    def test_rejects_unknown_diagnostic(self) -> None:
        with self.assertRaises(PolicyError):
            build_diagnostic_command("raw_shell")

    def test_mutating_commands_require_confirm_true(self) -> None:
        spec = build_restart_monitoring_command("log")

        with self.assertRaises(PolicyError):
            require_confirm(False, spec)
        require_confirm(True, spec)

    def test_rejects_invalid_restart_target(self) -> None:
        with self.assertRaises(PolicyError):
            build_restart_monitoring_command("network")

    def test_configure_syslog_is_quoted_and_guarded(self) -> None:
        spec = build_configure_syslog_command("192.168.0.221", 514, "udp")

        self.assertTrue(spec.mutating)
        self.assertIn("uci set system.@system[0].log_ip=192.168.0.221", spec.command)
        self.assertIn("/etc/init.d/log restart", spec.command)

    def test_rejects_bad_syslog_proto_and_port(self) -> None:
        with self.assertRaises(PolicyError):
            build_configure_syslog_command("192.168.0.221", 514, "tls")
        with self.assertRaises(PolicyError):
            build_configure_syslog_command("192.168.0.221", 70000, "udp")

    def test_test_log_bounds_message(self) -> None:
        with self.assertRaises(PolicyError):
            build_test_log_command("x" * 241)

    def test_setup_rejects_unknown_profile(self) -> None:
        with self.assertRaises(PolicyError):
            build_setup_command("192.168.0.221", profile="full,root_shell")

    def test_setup_accepts_combined_profiles(self) -> None:
        spec = build_setup_command(
            "192.168.0.221",
            profile="clients,traffic",
            traffic_lan_interface="br-lan",
            syslog_port=1514,
            syslog_proto="tcp",
        )

        self.assertTrue(spec.mutating)
        self.assertIn("OPENWRT_MONITOR_PROFILE=clients,traffic", spec.command)
        self.assertIn("TRAFFIC_LAN_INTERFACE=br-lan", spec.command)
        self.assertIn("SYSLOG_PORT=1514", spec.command)
        self.assertIn("SYSLOG_PROTO=tcp", spec.command)


if __name__ == "__main__":
    unittest.main()
