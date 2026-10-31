"""Build hooks shipped with base and features."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from support import REPO, write


class HookTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="opb-hooks-")
        self.addCleanup(temp.cleanup)
        self.src = Path(temp.name)

    def run_hook(self, hook, success=True, **env):
        result = subprocess.run(["bash", str(REPO / hook)], cwd=self.src, text=True, capture_output=True,
                                env=dict(os.environ, **env))
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        return result

    def test_base_post_feeds_uninstalls_cycle_packages_and_flags_obsolete_entries(self):
        calls = self.src / "calls"
        write(self.src / "scripts/feeds", f'#!/bin/sh\necho "$*" > "{calls}"\n', 0o755)
        for name in ("video/qt5base", "packages/squeezelite"):
            write(self.src / "package/feeds" / name / "Makefile", "x\n")
        result = self.run_hook("base/hooks/post-feeds.sh")
        self.assertEqual(calls.read_text(), "uninstall qt5base squeezelite luci-app-fwknopd\n")
        self.assertIn("luci-app-fwknopd is not installed", result.stderr)
        self.assertNotIn("qt5base is not installed", result.stderr)

    def test_wifi_quiet_log_patch(self):
        init = write(self.src / "package/network/services/hostapd/files/wpad.init",
                     "\t\tprocd_set_param command /usr/sbin/hostapd -s -g /var/run/hostapd/global\n")
        resolved = self.src / "resolved.json"
        for quiet in (False, True, True):
            write(resolved, json.dumps({"params": {"wifi": {"quiet_log": quiet}}}))
            self.run_hook("features/wifi/hooks/post-feeds.sh", OP_RESOLVED=str(resolved))
            self.assertEqual(" -s -q -g " in init.read_text(), quiet)
        init.write_text("changed upstream\n")
        result = self.run_hook("features/wifi/hooks/post-feeds.sh", success=False, OP_RESOLVED=str(resolved))
        self.assertIn("Expected one hostapd start command", result.stderr)

    def test_nikki_variant_conflict_patch(self):
        metadata = write(self.src / "scripts/package-metadata.pl",
                         "sub x {\n\t\tnext unless $package{$depend};\n}\n")
        for _ in range(2):
            self.run_hook("features/nikki/hooks/pre-feeds.sh")
            self.assertEqual(metadata.read_text().count("variant_default"), 1)
        metadata.write_text("changed upstream\n")
        result = self.run_hook("features/nikki/hooks/pre-feeds.sh", success=False)
        self.assertIn("patch not applied", result.stderr)


if __name__ == "__main__":
    unittest.main()
