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


class TestNetflowDashboard(unittest.TestCase):
    """Structural checks on the generated NetFlow dashboard.

    The generator's own validate_dashboard() already enforces layout, unique
    ids, sampling-rate correction, and variable hygiene on every run. What is
    added here is the class of mistake that regenerates cleanly and only
    fails in front of a human: panels built on columns that are empty in this
    deployment, and the two metric-shape traps that render as silence.
    """

    @classmethod
    def setUpClass(cls):
        import sys

        sys.path.insert(0, str(ROOT))
        import scripts.build_openwrt_netflow_dashboard as generator

        cls.generator = generator
        cls.spec = generator.build_dashboard()["spec"]
        cls.elements = cls.spec["elements"]

    def queries(self):
        """Every (panel id, query spec) pair in the dashboard."""
        for element in self.elements.values():
            for query in element["spec"]["data"]["spec"]["queries"]:
                yield element["spec"]["id"], query["spec"]["query"]["spec"]

    def sql_queries(self):
        for pid, spec in self.queries():
            if spec.get("rawSql"):
                yield pid, spec["rawSql"]

    def test_tab_order_is_the_operator_workflow(self):
        titles = [t["spec"]["title"] for t in self.spec["layout"]["spec"]["tabs"]]
        self.assertEqual(
            [
                "Flow Overview",
                "Applications",
                "External",
                "Security Signals",
                "Pipeline Health",
                "Pipeline Internals",
            ],
            titles,
        )

    def test_generated_copies_match_the_generator(self):
        rendered = self.generator.stable_json(self.generator.build_dashboard())
        for out in self.generator.OUTS:
            path = Path(out)
            self.assertTrue(path.exists(), f"{out} was never generated")
            self.assertEqual(
                rendered,
                path.read_text(encoding="utf-8"),
                f"{out} is stale; rerun python3 -m scripts.build_openwrt_netflow_dashboard",
            )

    def test_no_panel_queries_a_column_that_is_empty_here(self):
        # Each of these was checked against the live cluster and is 0%
        # populated for a structural reason that will not change: no BGP peer
        # to supply AS paths, no City database loaded (it OOMs the
        # orchestrator alongside Country), and no multi-site deployment.
        # A panel on any of them is a permanent "no data" wall, which looks
        # exactly like a broken pipeline.
        forbidden = [
            "DstASPath",
            "Dst1stAS",
            "Dst2ndAS",
            "Dst3rdAS",
            "DstCommunities",
            "DstLargeCommunities",
            "SrcGeoCity",
            "DstGeoCity",
            "SrcGeoState",
            "DstGeoState",
            "ExporterRole",
            "ExporterSite",
            "ExporterRegion",
            "ExporterTenant",
        ]
        for pid, sql in self.sql_queries():
            for column in forbidden:
                self.assertNotIn(
                    column,
                    sql,
                    f"panel {pid} queries {column}, which is 0% populated in this deployment",
                )

    def test_interface_boundary_is_never_filtered_on(self):
        # softflowd reports one ifIndex for both directions, so InIfBoundary
        # is constant across the dataset: filtering on it matches every row
        # or none, never the split it appears to offer.
        for pid, sql in self.sql_queries():
            self.assertNotIn("IfBoundary", sql, f"panel {pid} filters on interface boundary")

    def test_byte_and_packet_aggregates_are_sampling_rate_corrected(self):
        # Forgetting the multiplier under-reports by exactly the sampling
        # factor, which is invisible unless you already know the answer.
        pattern = re.compile(r"sum(?:If)?\(([^()]*(?:Bytes|Packets)[^()]*)\)")
        checked = 0
        for pid, sql in self.sql_queries():
            for match in pattern.finditer(sql):
                checked += 1
                self.assertIn(
                    "SamplingRate",
                    match.group(1),
                    f"panel {pid}: {match.group(1).strip()} is not sampling-rate corrected",
                )
        self.assertGreater(checked, 20, "expected many byte/packet aggregates to check")

    def test_histogram_quantile_is_only_used_over_bucket_series(self):
        # Akvorado publishes a histogram (insert_time_seconds_bucket, has le)
        # and a summary (flow_per_batch, has a quantile label) side by side.
        # histogram_quantile() over the summary returns nothing at all --
        # an empty panel with no error anywhere.
        seen = 0
        for pid, spec in self.queries():
            expr = spec.get("expr", "")
            if "histogram_quantile" not in expr:
                continue
            seen += 1
            for metric in re.findall(r"\b(akvorado_\w+|openwrt_\w+)\b", expr):
                self.assertTrue(
                    metric.endswith("_bucket"),
                    f"panel {pid}: histogram_quantile over non-bucket series {metric}",
                )
        self.assertGreater(seen, 0, "insert-latency panel should use histogram_quantile")

    def test_summary_metrics_are_selected_by_quantile_label(self):
        batch = [
            expr
            for _, spec in self.queries()
            for expr in [spec.get("expr", "")]
            if "flow_per_batch" in expr
        ]
        self.assertTrue(batch, "flows-per-batch panel is missing")
        for expr in batch:
            self.assertIn("quantile=", expr, "summary must be selected by its quantile label")
            self.assertNotIn("histogram_quantile", expr)

    def test_node_graph_edge_endpoints_all_exist_as_nodes(self):
        # A dangling edge endpoint crashes the node graph panel rather than
        # degrading. Both frames are built from one LIMITed pair set so the
        # node set is exactly the endpoints of the edge set; this checks the
        # id-prefix contract that makes that work.
        panel = self.elements["panel-215"]["spec"]
        by_ref = {
            q["spec"]["refId"]: q["spec"]["query"]["spec"]["rawSql"]
            for q in panel["data"]["spec"]["queries"]
        }
        self.assertEqual({"nodes", "edges"}, set(by_ref))
        for prefix in ("'h:'", "'a:'"):
            self.assertIn(prefix, by_ref["nodes"], f"nodes frame does not emit {prefix} ids")
            self.assertIn(prefix, by_ref["edges"], f"edges frame does not reference {prefix} ids")
        # Both frames must derive from the same pair set, or the LIMITs can
        # disagree and strand an endpoint.
        self.assertIn("WITH pairs AS", by_ref["nodes"])
        self.assertIn("WITH pairs AS", by_ref["edges"])

    def test_state_timeline_mappings_are_in_field_defaults(self):
        # byType overrides silently fail to apply on state timelines, leaving
        # raw "-Inf - +Inf" bracket text instead of the state names.
        for key, element in self.elements.items():
            viz = element["spec"]["vizConfig"]
            if viz["group"] != "state-timeline":
                continue
            defaults = viz["spec"]["fieldConfig"]["defaults"]
            self.assertTrue(defaults.get("mappings"), f"{key}: state timeline has no value mappings")
            # With "thresholds" colouring, Grafana labels each band by its
            # threshold bracket ("< 1", "1+") and discards the mapped state
            # names entirely.
            self.assertNotEqual(
                "thresholds",
                defaults["color"]["mode"],
                f"{key}: threshold colouring overrides value mappings on a state timeline",
            )
            for override in viz["spec"]["fieldConfig"]["overrides"]:
                if override["matcher"]["id"] == "byType":
                    for prop in override["properties"]:
                        self.assertNotEqual(
                            "mappings",
                            prop["id"],
                            f"{key}: value mappings in a byType override do not apply",
                        )

    def test_ratio_denominators_are_guarded(self):
        # An unguarded divide renders +Inf or a blank tile.
        for pid, sql in self.sql_queries():
            if "/ " not in sql:
                continue
            for match in re.finditer(r"/\s*(\w+\()", sql):
                self.assertIn(
                    match.group(1),
                    ("greatest(", "nullIf("),
                    f"panel {pid}: divide by {match.group(1)} is not guarded against zero",
                )

    def test_clickhouse_timeseries_panels_split_into_named_series(self):
        # The ClickHouse datasource does not turn a long-format result
        # (time, label, value) into one series per label -- it returns a
        # single series named after the value column. A three-way direction
        # split then renders as one line called "bps": wrong, and wrong in a
        # way that still looks like a working panel.
        #
        # Two acceptable shapes: pivot in SQL so each label is its own
        # column, or split browser-side with partitionByValues.
        for key, element in self.elements.items():
            spec = element["spec"]
            if spec["vizConfig"]["group"] != "timeseries":
                continue
            queries = spec["data"]["spec"]["queries"]
            sqls = [
                q["spec"]["query"]["spec"]["rawSql"]
                for q in queries
                if q["spec"]["query"]["spec"].get("rawSql")
            ]
            if not sqls:
                continue  # Prometheus panel; series come from legendFormat.
            transforms = spec["data"]["spec"]["transformations"]
            partitioned = any(t["spec"]["id"] == "partitionByValues" for t in transforms)
            for sql in sqls:
                if partitioned:
                    continue
                self.assertNotIn(
                    "GROUP BY time,",
                    sql,
                    f"{key} ({spec['title']}): long-format ClickHouse timeseries without "
                    "partitionByValues renders as a single mis-named series; pivot the "
                    "label into columns or add partition_by()",
                )

    def test_percent_stacked_panels_do_not_claim_percent_units(self):
        # Percent stacking normalises the AXIS to 0-100%, but the legend and
        # tooltip still format the RAW values with the field unit. Declaring
        # percentunit there renders bytes as "21282567600%" in the legend
        # while the graph itself looks perfectly fine.
        checked = 0
        for key, element in self.elements.items():
            viz = element["spec"]["vizConfig"]
            if viz["group"] != "timeseries":
                continue
            defaults = viz["spec"]["fieldConfig"]["defaults"]
            if defaults["custom"]["stacking"]["mode"] != "percent":
                continue
            checked += 1
            self.assertNotEqual(
                "percentunit",
                defaults.get("unit"),
                f"{key}: percent-stacked panel must carry the unit of the underlying "
                "quantity, not percentunit -- the legend formats raw values",
            )
        self.assertGreater(checked, 0, "expected percent-stacked companion panels")

    def test_clickhouse_piecharts_render_one_slice_per_row(self):
        # Same trap as bargauge: without values=True the donut collapses to a
        # single 100% slice named after the value column.
        checked = 0
        for key, element in self.elements.items():
            spec = element["spec"]
            if spec["vizConfig"]["group"] != "piechart":
                continue
            if not any(
                q["spec"]["query"]["spec"].get("rawSql")
                for q in spec["data"]["spec"]["queries"]
            ):
                continue
            checked += 1
            self.assertTrue(
                spec["vizConfig"]["spec"]["options"]["reduceOptions"]["values"],
                f"{key} ({spec['title']}): ClickHouse piechart needs reduceOptions.values=True",
            )
        self.assertGreater(checked, 0, "expected ClickHouse-backed donuts")

    def test_clickhouse_bargauges_render_one_bar_per_row(self):
        # reduceOptions.values must be True for SQL table frames; with the
        # default the panel reduces the single numeric column to one number
        # and renders empty, with no error anywhere.
        checked = 0
        for key, element in self.elements.items():
            spec = element["spec"]
            if spec["vizConfig"]["group"] != "bargauge":
                continue
            uses_sql = any(
                q["spec"]["query"]["spec"].get("rawSql")
                for q in spec["data"]["spec"]["queries"]
            )
            if not uses_sql:
                continue
            checked += 1
            self.assertTrue(
                spec["vizConfig"]["spec"]["options"]["reduceOptions"]["values"],
                f"{key} ({spec['title']}): ClickHouse bargauge needs reduceOptions.values=True "
                "or it renders empty",
            )
        self.assertGreater(checked, 5, "expected several ClickHouse-backed bar gauges")

    def test_ranking_bar_gauges_are_readable_and_neutral(self):
        # Two failure modes, both visual-only:
        #
        #  - A continuous-* scheme maps small values to the dark end of the
        #    ramp. On the dark theme the tail of a ranking becomes dark blue
        #    on dark grey and, because valueMode is "color", the NUMBERS go
        #    with it. Rows after the leading top-N entries must not be
        #    legible value at all.
        #  - Graded green/yellow/red steps on a size ranking paint the biggest
        #    item red as though being biggest were a fault.
        checked = 0
        for key, element in self.elements.items():
            spec = element["spec"]
            if spec["vizConfig"]["group"] != "bargauge":
                continue
            checked += 1
            defaults = spec["vizConfig"]["spec"]["fieldConfig"]["defaults"]
            mode = defaults["color"]["mode"]
            self.assertFalse(
                mode.startswith("continuous-"),
                f"{key} ({spec['title']}): {mode} renders the tail of the ranking "
                "unreadably dark; use thresholds or palette-classic",
            )
            if mode == "thresholds":
                steps = defaults["thresholds"]["steps"]
                self.assertEqual(
                    1,
                    len(steps),
                    f"{key} ({spec['title']}): graded thresholds on a size ranking imply "
                    "severity; a ranking needs a single neutral base step",
                )
                self.assertIsNone(
                    steps[0]["value"],
                    f"{key}: base threshold step must be null (-inf), not 0",
                )
        self.assertGreater(checked, 8, "expected the dashboard's bar gauges to be checked")

    def test_geomap_uses_layer_lookup_not_fieldlookup(self):
        # The fieldLookup transform fails on geomap with "missing frame in
        # gazetteer" -- browser console only, no panel error, empty map.
        # The marker layer's own lookup mode is what works.
        for key, element in self.elements.items():
            spec = element["spec"]
            if spec["vizConfig"]["group"] != "geomap":
                continue
            for transform in spec["data"]["spec"]["transformations"]:
                self.assertNotEqual(
                    "fieldLookup",
                    transform["spec"]["id"],
                    f"{key}: fieldLookup renders an empty geomap; use the layer's lookup mode",
                )
            for layer in spec["vizConfig"]["spec"]["options"]["layers"]:
                location = layer["location"]
                self.assertEqual("lookup", location["mode"], f"{key}: layer is not in lookup mode")
                self.assertTrue(
                    location["gazetteer"].startswith("public/gazetteer/"),
                    f"{key}: gazetteer must be a served path, not the UI label",
                )

    def test_units_are_never_left_as_short(self):
        for key, element in self.elements.items():
            viz = element["spec"]["vizConfig"]
            if viz["group"] in {"text", "logs"}:
                continue
            unit = viz["spec"]["fieldConfig"]["defaults"].get("unit")
            self.assertNotEqual("short", unit, f"{key}: unit left as 'short'")

    def test_no_value_is_a_field_default_not_a_panel_option(self):
        # Under `options` Grafana silently ignores it and every custom
        # empty-state message goes unused.
        for key, element in self.elements.items():
            self.assertNotIn(
                "noValue",
                element["spec"]["vizConfig"]["spec"]["options"],
                f"{key}: noValue belongs in fieldConfig.defaults",
            )


if __name__ == "__main__":
    unittest.main()
