"""Rendering checks for the softflowd UCI template used by the `netflow` profile.

These run setup.sh's *actual* rendering block rather than a reimplementation of
it: the block is extracted from the installer, retargeted at a temp file, and
executed with /bin/sh. A test that duplicated the sed expressions would keep
passing after the installer's copy drifted, which is exactly the failure this
is meant to catch.

What matters about the output:

  - No placeholder survives. A stray __COLLECTOR_HOST__ makes softflowd export
    to a nonexistent host and the failure is silent on the router.
  - Each interface gets its own pid file and control socket. The shipped init
    script is a config_foreach, so shared paths mean the second instance kills
    the first.
  - The pcap filter excludes the collector endpoint, so softflowd does not
    account for its own telemetry as user traffic. (Accounting hygiene, not a
    runaway loop: all export packets share one 5-tuple and collapse into a
    single flow.)
  - `enabled` is 1 and `sampling_rate` is what was asked for. The stock package
    defaults are 0 and 100 respectively; inheriting either silently produces no
    data or 1-in-100 sampled counters.
"""

import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SETUP = ROOT / "openwrt" / "setup.sh"
TEMPLATE = ROOT / "openwrt" / "netflow" / "softflowd.config"

RENDER_START = ": > /etc/config/softflowd.new"
RENDER_END = "mv /etc/config/softflowd.new /etc/config/softflowd"


def extract_render_block() -> str:
    """Pull the softflowd rendering loop out of setup.sh."""
    lines = SETUP.read_text().splitlines()
    start = end = None
    for i, line in enumerate(lines):
        if start is None and line.strip() == RENDER_START:
            start = i
        elif start is not None and line.strip() == RENDER_END:
            end = i
            break
    if start is None or end is None:
        raise AssertionError(
            "could not locate the softflowd rendering block in openwrt/setup.sh; "
            "if it was refactored, update RENDER_START/RENDER_END here rather "
            "than dropping the test"
        )
    return "\n".join(lines[start:end])


def render(
    interfaces,
    host="192.168.0.100",
    port="2055",
    sampling="1",
    max_flows="8192",
    timeouts="maxlife=60",
):
    block = extract_render_block()
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "softflowd"
        script = block.replace("/etc/config/softflowd.new", str(out))
        specs = " ".join(
            f"{index}:{iface}" for index, iface in enumerate(interfaces, start=8)
        )
        env = {
            "SCRIPT_DIR": str(ROOT / "openwrt"),
            "MONITORING_HOST": host,
            "NETFLOW_PORT": port,
            "NETFLOW_SAMPLING_RATE": sampling,
            "NETFLOW_MAX_FLOWS": max_flows,
            "NETFLOW_TIMEOUTS": timeouts,
            "NETFLOW_INTERFACES": " ".join(interfaces),
            "NETFLOW_INTERFACE_SPECS": specs,
            "PATH": "/usr/bin:/bin",
        }
        subprocess.run(
            ["sh", "-eu", "-c", script], env=env, check=True, capture_output=True
        )
        return out.read_text()


def sections(rendered):
    """Split rendered output into `config softflowd` sections."""
    return [s for s in re.split(r"^config softflowd$", rendered, flags=re.M)[1:]]


def option(section, name):
    match = re.search(rf"^\toption {name}\s+'([^']*)'$", section, flags=re.M)
    return match.group(1) if match else None


class TestSoftflowdTemplate(unittest.TestCase):
    def test_template_placeholders_are_all_substituted(self):
        rendered = render(["eth1"])
        leftovers = re.findall(r"__[A-Z_]+__", rendered)
        self.assertEqual(
            [],
            leftovers,
            f"unsubstituted placeholders in rendered config: {sorted(set(leftovers))}",
        )

    def test_every_template_placeholder_is_handled_by_setup(self):
        # The reverse direction: adding a placeholder to the template without
        # a matching -e in setup.sh would render it literally into the config.
        placeholders = set(re.findall(r"__[A-Z_]+__", TEMPLATE.read_text()))
        self.assertTrue(placeholders, "template has no placeholders at all")
        block = extract_render_block()
        for placeholder in sorted(placeholders):
            self.assertIn(
                f"s/{placeholder}/",
                block,
                f"{placeholder} appears in softflowd.config but setup.sh never substitutes it",
            )

    def test_single_interface_renders_one_enabled_section(self):
        rendered = render(["eth1"])
        parts = sections(rendered)
        self.assertEqual(1, len(parts))
        self.assertEqual("8:eth1", option(parts[0], "interface"))
        # Stock package default is '0'; inheriting it starts nothing.
        self.assertEqual("1", option(parts[0], "enabled"))
        self.assertEqual("192.168.0.100:2055", option(parts[0], "host_port"))
        # v5 is IPv4-only; this network is dual-stack.
        self.assertEqual("9", option(parts[0], "export_version"))
        self.assertEqual("1", option(parts[0], "track_ipv6"))

    def test_sampling_rate_is_not_the_stock_default(self):
        parts = sections(render(["eth1"]))
        self.assertEqual(
            "1",
            option(parts[0], "sampling_rate"),
            "stock OpenWrt ships sampling_rate 100, which silently gives "
            "1-in-100 sampled byte counts",
        )

    def test_overrides_are_threaded_through(self):
        parts = sections(
            render(
                ["eth1"],
                host="10.0.0.5",
                port="9995",
                sampling="10",
                max_flows="4096",
                timeouts="maxlife=120",
            )
        )
        self.assertEqual("10.0.0.5:9995", option(parts[0], "host_port"))
        self.assertEqual("10", option(parts[0], "sampling_rate"))
        self.assertEqual("4096", option(parts[0], "max_flows"))
        self.assertEqual("maxlife=120", option(parts[0], "timeout"))

    def test_maxlife_is_bounded_by_default(self):
        # softflowd's own default maxlife is one week (softflowd.h
        # DEFAULT_MAXIMUM_LIFETIME = 3600*24*7) and flows are only exported on
        # expiry, so without an explicit -t an ongoing transfer contributes
        # nothing to the dashboard until it ends and then lands as one spike
        # stamped at expiry time. The rendered config must always carry a
        # bounded maxlife.
        timeout = option(sections(render(["eth1"]))[0], "timeout")
        self.assertIsNotNone(timeout, "no timeout option rendered")
        self.assertIn("maxlife=", timeout)
        seconds = int(timeout.split("maxlife=")[1].split(",")[0].split()[0])
        self.assertLessEqual(
            seconds,
            300,
            "maxlife must stay well under softflowd's 1-week default for "
            "throughput graphs to track reality",
        )

    def test_pcap_filter_excludes_the_collector_endpoint(self):
        parts = sections(render(["br-lan"], host="10.0.0.5", port="9995"))
        self.assertEqual(
            "not (host 10.0.0.5 and udp port 9995)",
            option(parts[0], "filter"),
            "without this softflowd counts its own export traffic as user "
            "traffic in top-talker panels",
        )

    def test_multiple_interfaces_get_distinct_runtime_paths(self):
        parts = sections(render(["eth1", "br-lan"]))
        self.assertEqual(2, len(parts))
        self.assertEqual(
            ["8:eth1", "9:br-lan"], [option(p, "interface") for p in parts]
        )

        # The init script is a config_foreach: one process per section. Shared
        # pid files or control sockets mean the second instance clobbers the
        # first, and the health collector reads the wrong socket.
        pids = [option(p, "pid_file") for p in parts]
        sockets = [option(p, "control_socket") for p in parts]
        self.assertEqual(len(pids), len(set(pids)), f"duplicate pid files: {pids}")
        self.assertEqual(
            len(sockets), len(set(sockets)), f"duplicate control sockets: {sockets}"
        )

    def test_control_socket_path_matches_what_the_health_collector_reads(self):
        # The collector derives the socket path independently; if these two
        # ever disagree every exporter reads as down while flows arrive fine.
        parts = sections(render(["eth1"]))
        socket = option(parts[0], "control_socket")
        collector = (ROOT / "openwrt" / "scripts" / "openwrt-monitor-netflow-health.sh").read_text()
        self.assertIn('softflowd-$iface.ctl', collector)
        self.assertEqual("/var/run/softflowd-eth1.ctl", socket)


if __name__ == "__main__":
    unittest.main()
