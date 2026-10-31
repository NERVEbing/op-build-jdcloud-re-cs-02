"""Host CLI argument handling and container dispatch without Docker."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from support import REPO, write


class CliTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="opb-cli-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        shutil.copy2(REPO / "op", self.root / "op")
        shutil.copytree(REPO / "features", self.root / "features")
        for name in ("demo", "lab"):
            write(self.root / f"profiles/{name}/profile.toml", "features = []\n")
        self.calls = self.root / "docker-calls.jsonl"
        docker = write(self.root / "docker", '''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
with Path(os.environ["TEST_DOCKER_CALLS"]).open("a") as stream:
    stream.write(json.dumps(sys.argv[1:]) + "\\n")
if sys.argv[1] == "images":
    print("fixture-image")
''', 0o755)
        self.environment = dict(os.environ, OP_DOCKER=str(docker), OP_UID=str(os.getuid()),
                                OP_GID=str(os.getgid()), TEST_DOCKER_CALLS=str(self.calls))

    def op(self, *args):
        return subprocess.run(["bash", str(self.root / "op"), *args], env=self.environment, text=True,
                              capture_output=True)

    def last_call(self):
        return json.loads(self.calls.read_text().splitlines()[-1])

    def test_dispatches_engine_actions(self):
        for args, expected in (
            (("build", "demo", "-j", "8"), ["build", "demo", "8"]),
            (("compile", "demo", "-j8"), ["compile", "demo", "8"]),
            (("build", "demo"), ["build", "demo"]),
            (("setup", "demo"), ["setup", "demo"]),
            (("info", "lab"), ["info", "lab"]),
            (("clean",), ["clean"]),
            (("distclean",), ["distclean"]),
        ):
            with self.subTest(args=args):
                result = self.op(*args)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                call = self.last_call()
                self.assertEqual(call[-len(expected) - 3:], ["python3", "-m", "opbuild", *expected])
                self.assertIn("PYTHONPATH=/builder/engine", call)
                self.assertIn(f"{self.root}:/builder", call)

    def test_invalid_arguments_fail_before_container_start(self):
        for args, message in (
            (("build", "demo", "-j"), "-j requires a positive integer"),
            (("build", "demo", "-j0"), "-j requires a positive integer"),
            (("build", "demo", "-j", "2.5"), "-j requires a positive integer"),
            (("build", "demo", "-j8", "-j0"), "-j requires a positive integer"),
            (("setup", "demo", "-j8"), "Unknown argument: -j8"),
            (("clean", "demo"), "Unknown argument: demo"),
            (("build",), "Missing profile (available: demo lab)"),
            (("build", "nope"), "Unknown profile: nope (available: demo lab)"),
            (("build", "../demo"), "Invalid profile name"),
        ):
            with self.subTest(args=args):
                result = self.op(*args)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
                self.assertFalse(self.calls.exists())

    def test_root_ids_build_a_non_root_image(self):
        self.environment.update(OP_UID="0", OP_GID="0")
        result = self.op("image")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        call = self.last_call()
        self.assertIn("BUILDER_UID=1000", call)
        self.assertIn("BUILDER_GID=1000", call)
        self.assertEqual(call[call.index("-t") + 1], "op-build-jdcloud-re-cs-02:24.04")

    def test_list_shows_profiles_and_feature_descriptions(self):
        result = self.op("list")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("profiles: demo lab", result.stdout)
        self.assertRegex(result.stdout, r"wifi +无线")
        self.assertFalse(self.calls.exists())


if __name__ == "__main__":
    unittest.main()
