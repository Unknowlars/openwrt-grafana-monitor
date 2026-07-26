#!/usr/bin/env python3
"""Safety-policy tests for the OpenWrt SSH MCP server."""

from __future__ import annotations

import unittest

from mcp_server.core import (
    DEFAULT_KNOWN_HOSTS_PATH,
    DEFAULT_SETUP_TIMEOUT_SECONDS,
    HOST_KEY_POLICY_AUTO_ADD,
    HOST_KEY_POLICY_REJECT,
    PolicyError,
    build_configure_syslog_command,
    build_diagnostic_command,
    build_metrics_sample_command,
    build_monitoring_status_command,
    build_restart_monitoring_command,
    build_setup_command,
    build_test_log_command,
    host_key_failure_message,
    normalize_profile,
    parse_router_inventory,
    require_confirm,
    resolve_host_key_policy,
    resolve_setup_timeout_seconds,
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

    def test_metrics_commands_use_router_lan_ip(self) -> None:
        specs = [
            build_metrics_sample_command(),
            build_monitoring_status_command(),
            build_diagnostic_command("collector_success"),
        ]

        for spec in specs:
            with self.subTest(description=spec.description):
                self.assertIn("network.lan.ipaddr", spec.command)
                self.assertIn("LAN_IP=${LAN_IP%%/*}", spec.command)
                self.assertIn("http://$LAN_IP:9100/metrics", spec.command)
                self.assertNotIn("http://127.0.0.1:9100/metrics", spec.command)

    def test_wifi_diagnostic_redacts_wireless_secrets(self) -> None:
        spec = build_diagnostic_command("wifi")

        self.assertIn("[redacted]", spec.command)
        self.assertIn("wifi status", spec.command)
        self.assertIn("iw dev", spec.command)

    def test_conntrack_sources_diagnostic_is_bounded(self) -> None:
        spec = build_diagnostic_command("conntrack_sources")

        self.assertIn("conntrack_cli=", spec.command)
        self.assertIn("conntrack_cli_rows=", spec.command)
        self.assertIn("/proc/net/nf_conntrack", spec.command)
        self.assertIn("getHostHints_bytes=", spec.command)
        self.assertNotIn("src=", spec.command)
        self.assertNotIn("wifi status", spec.command)

    def test_inode_sources_diagnostic_is_bounded(self) -> None:
        spec = build_diagnostic_command("inode_sources")

        self.assertIn("df_iP=", spec.command)
        self.assertIn("stat_bin=", spec.command)
        self.assertIn("stat_f_tmp=", spec.command)
        self.assertIn("mount_overlay=", spec.command)
        self.assertNotIn("find ", spec.command)

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

    def test_setup_rejects_full_combined_with_other_profiles(self) -> None:
        for profile in ("full,traffic", "traffic,full"):
            with self.subTest(profile=profile):
                with self.assertRaisesRegex(
                    PolicyError,
                    "full.*cannot be combined",
                ):
                    normalize_profile(profile)

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

    def test_setup_timeout_default_exceeds_read_only_timeouts(self) -> None:
        setup = build_setup_command("192.168.0.221")
        read_only_specs = [
            build_monitoring_status_command(),
            build_metrics_sample_command(),
            build_diagnostic_command("routes"),
        ]

        self.assertEqual(setup.timeout_seconds, DEFAULT_SETUP_TIMEOUT_SECONDS)
        for spec in read_only_specs:
            with self.subTest(description=spec.description):
                self.assertGreater(setup.timeout_seconds, spec.timeout_seconds)

    def test_setup_timeout_honors_env_override(self) -> None:
        timeout_seconds = resolve_setup_timeout_seconds("900")
        setup = build_setup_command("192.168.0.221", timeout_seconds=timeout_seconds)

        self.assertEqual(setup.timeout_seconds, 900)
        self.assertIn("partially configured", setup.timeout_message)
        self.assertIn("openwrt_monitoring_status", setup.timeout_message)


class HostKeyPolicyTests(unittest.TestCase):
    """Default RejectPolicy; AutoAddPolicy only via explicit insecure flag."""

    def test_default_policy_is_reject(self) -> None:
        policy = resolve_host_key_policy()
        self.assertEqual(policy.policy, HOST_KEY_POLICY_REJECT)
        self.assertFalse(policy.insecure)
        self.assertEqual(policy.known_hosts_path, DEFAULT_KNOWN_HOSTS_PATH)

    def test_empty_and_zero_insecure_stay_reject(self) -> None:
        for raw in ("", "0", "false", "no", "off", None):
            with self.subTest(raw=raw):
                policy = resolve_host_key_policy(insecure_raw=raw)
                self.assertEqual(policy.policy, HOST_KEY_POLICY_REJECT)
                self.assertFalse(policy.insecure)

    def test_insecure_override_requires_explicit_truthy(self) -> None:
        for raw in ("1", "true", "YES", "on"):
            with self.subTest(raw=raw):
                policy = resolve_host_key_policy(insecure_raw=raw)
                self.assertEqual(policy.policy, HOST_KEY_POLICY_AUTO_ADD)
                self.assertTrue(policy.insecure)

    def test_custom_known_hosts_path(self) -> None:
        policy = resolve_host_key_policy(known_hosts_path="/etc/ssh/known_hosts")
        self.assertEqual(policy.known_hosts_path, "/etc/ssh/known_hosts")
        self.assertEqual(policy.policy, HOST_KEY_POLICY_REJECT)

    def test_failure_message_is_actionable(self) -> None:
        msg = host_key_failure_message("192.168.0.1", 22, "/app/known_hosts")
        self.assertIn("192.168.0.1", msg)
        self.assertIn("/app/known_hosts", msg)
        self.assertIn("ssh-keyscan", msg)
        self.assertIn("OPENWRT_MCP_INSECURE_HOST_KEYS", msg)


if __name__ == "__main__":
    unittest.main()
