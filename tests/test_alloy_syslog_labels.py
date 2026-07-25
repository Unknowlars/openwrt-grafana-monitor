"""Guards the Loki syslog stream-label contract in alloy/config.alloy.

Two failure modes, both silent in opposite directions:

1. A wildcard `labelmap` over `__syslog_(.+)` promotes every syslog field to a
   Loki *stream* label, including `__syslog_message_proc_id` -- the process PID
   for OpenWrt logd rfc3164 frames. Every dnsmasq/hostapd/netifd restart then
   mints a new stream. Unbounded stream cardinality degrades ingestion and
   query performance and inflates Loki's index; nothing about it is visible
   from a dashboard.

2. Renaming a promoted label that the dashboards select on makes every affected
   panel return no data, equally invisibly. The dashboards query
   `message_severity` and `message_app_name` -- the `message_` prefix is load
   bearing and must not be shortened to `severity`/`app_name`.

This test ties the two together: every syslog label the generators select on
must actually be promoted by the Alloy config, and nothing unbounded may be.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "alloy" / "config.alloy"

GENERATORS = (
    "build_dashboards.py",
    "build_openwrt_mission_control.py",
    "build_openwrt_operations_dashboard.py",
    "build_openwrt_advanced_dashboard.py",
    "build_openwrt_clients_dashboard.py",
    "build_openwrt_topology_dashboard.py",
)

# Fields loki.source.syslog exposes that must never become stream labels.
FORBIDDEN_SOURCE_FIELDS = ("__syslog_message_proc_id", "__syslog_message_msg_id")


def config_text() -> str:
    return CONFIG.read_text(encoding="utf-8")


def promoted_labels(text: str) -> set[str]:
    """Stream labels the syslog relabel block creates via target_label."""
    block = re.search(
        r'loki\.relabel\s+"openwrt_syslog"\s*\{(.*?)\n\}', text, re.DOTALL
    )
    assert block, "loki.relabel \"openwrt_syslog\" block not found"
    return set(re.findall(r'target_label\s*=\s*"([^"]+)"', block.group(1)))


def strip_comments(text: str) -> str:
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("//")
    )


class TestAlloySyslogLabels(unittest.TestCase):
    def test_no_wildcard_syslog_labelmap(self) -> None:
        """A labelmap over __syslog_(.+) promotes the PID -- see R5."""
        text = strip_comments(config_text())
        self.assertNotIn(
            "labelmap",
            text,
            "alloy/config.alloy uses a labelmap action. For __syslog_* this "
            "promotes message_proc_id (a PID) to a Loki stream label and "
            "reintroduces unbounded stream cardinality. Rename an explicit "
            "allowlist of fields instead -- see R5 in "
            "docs/CODE-REVIEW-REMEDIATION-PLAN.md.",
        )

    def test_pid_and_msgid_are_not_promoted(self) -> None:
        text = strip_comments(config_text())
        for field in FORBIDDEN_SOURCE_FIELDS:
            self.assertNotIn(
                field,
                text,
                f"{field} must not be referenced as a relabel source: it is "
                "unbounded (or useless) as a Loki stream label.",
            )

    def test_router_label_rule_is_preserved(self) -> None:
        """Load bearing for multi-router setups (ROUTER_TARGETS)."""
        labels = promoted_labels(config_text())
        self.assertIn(
            "router",
            labels,
            "the __syslog_message_hostname -> router rule was removed; logs "
            "from a second AP would all be attributed to the first router.",
        )

    def test_labels_selected_by_dashboards_are_promoted(self) -> None:
        """The regression that R5's own prescribed snippet would have caused.

        If a generator selects on message_severity but Alloy promotes bare
        `severity`, the panel silently returns no data.
        """
        promoted = promoted_labels(config_text())
        selected: dict[str, str] = {}
        for name in GENERATORS:
            path = ROOT / name
            if not path.exists():
                continue
            for label in re.findall(
                r"\b(message_[a-z_]+)\b", path.read_text(encoding="utf-8")
            ):
                selected.setdefault(label, name)

        self.assertTrue(selected, "no message_* label selectors found in generators")
        missing = {
            label: src for label, src in selected.items() if label not in promoted
        }
        self.assertFalse(
            missing,
            "these syslog labels are selected by dashboard generators but are "
            f"not promoted by alloy/config.alloy: {missing}. Panels using them "
            "return no data. Promoted labels are: " + repr(sorted(promoted)),
        )


if __name__ == "__main__":
    unittest.main()
