"""Package-manager-specific installer checks for openwrt/setup.sh."""

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SETUP = ROOT / "openwrt" / "setup.sh"


def extract_function(name: str) -> str:
    lines = SETUP.read_text().splitlines()
    start = None
    for index, line in enumerate(lines):
        if line == f"{name}() {{":
            start = index
            break
    if start is None:
        raise AssertionError(f"could not locate shell function {name}")

    for index in range(start + 1, len(lines)):
        if lines[index] == "}":
            return "\n".join(lines[start : index + 1])
    raise AssertionError(f"could not find end of shell function {name}")


class TestSetupPackageSelection(unittest.TestCase):
    def run_helper(self, manager: str, failing_package: str = ""):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            log = tmpdir / "calls.log"
            helper = tmpdir / manager
            helper.write_text(
                "#!/bin/sh\n"
                f"printf '%s\\n' \"$0 $*\" >> {log}\n"
                f"[ \"${{2:-}}\" = \"{failing_package}\" ] && exit 1\n"
                "exit 0\n"
            )
            helper.chmod(helper.stat().st_mode | stat.S_IXUSR)
            env = os.environ.copy()
            env["PATH"] = f"{tmpdir}:{env['PATH']}"
            script = "\n".join(
                [
                    extract_function("pkg_install_optional"),
                    extract_function("install_conntrack_cli"),
                    f"PKG_MANAGER={manager}",
                    "install_conntrack_cli",
                ]
            )
            subprocess.run(["sh", "-eu", "-c", script], env=env, check=True)
            return log.read_text().splitlines()

    def test_apk_uses_current_conntrack_package_name_without_old_noise(self):
        calls = self.run_helper("apk")
        self.assertEqual(1, len(calls))
        self.assertTrue(calls[0].endswith("apk add conntrack"), calls)

    def test_opkg_falls_back_from_conntrack_tools_to_conntrack(self):
        calls = self.run_helper("opkg", failing_package="conntrack-tools")
        self.assertEqual(2, len(calls))
        self.assertTrue(calls[0].endswith("opkg install conntrack-tools"), calls)
        self.assertTrue(calls[1].endswith("opkg install conntrack"), calls)


if __name__ == "__main__":
    unittest.main()
