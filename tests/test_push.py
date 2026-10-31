"""Synchronization exclusions and remote ownership handling of "op push"."""

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest

from support import REPO, write


@unittest.skipUnless(shutil.which("rsync"), "rsync is required")
class PushTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="opb-push-")
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.source = self.base / "source"
        self.target = self.base / "target"
        self.target.mkdir()
        self.source.mkdir()
        shutil.copy2(REPO / "op", self.source / "op")
        shutil.copytree(REPO / "docker", self.source / "docker")
        write(self.source / "engine/opbuild/__main__.py", "# engine fixture\n")
        write(self.source / "profiles/demo/profile.toml", "features = []\n")

    def push(self, *args, env=None):
        return subprocess.run(["bash", str(self.source / "op"), "push", *args], cwd=self.base,
                              text=True, capture_output=True, env=env)

    @staticmethod
    def deployed(target):
        """Files left by an earlier push; they mark the target as a copy of this project."""
        write(target / "op", "# earlier push\n")
        write(target / "engine/opbuild/__main__.py", "# earlier push\n")

    def test_local_sync_keeps_protected_paths(self):
        self.deployed(self.target)
        certs = self.target / "docker/certs"
        write(certs / "network.crt", "remote certificate\n")
        write(self.source / "docker/certs/local-only.crt", "local only\n")
        write(self.target / "workspace/src/file", "source tree\n")
        write(self.target / "profiles/remote-only/profile.toml", "features = []\n")
        write(self.target / "stale.sh", "obsolete\n")
        result = self.push(str(self.target) + "/")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((certs / "network.crt").read_text(), "remote certificate\n")
        self.assertFalse((certs / "local-only.crt").exists())
        self.assertTrue((certs / ".gitkeep").is_file())
        self.assertEqual((self.target / "workspace/src/file").read_text(), "source tree\n")
        # Profiles come from the local machine; remote-only profiles are removed.
        self.assertTrue((self.target / "profiles/demo/profile.toml").is_file())
        self.assertFalse((self.target / "profiles/remote-only").exists())
        self.assertFalse((self.target / "stale.sh").exists())

    def test_destination_must_be_missing_empty_or_this_project(self):
        other = write(self.base / "other/important.txt", "keep\n").parent
        result = self.push(str(other) + "/")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("neither empty nor a copy of this project", result.stderr)
        self.assertEqual(sorted(path.name for path in other.iterdir()), ["important.txt"])
        for target in (self.target, self.base / "missing"):
            with self.subTest(target=target.name):
                result = self.push(str(target) + "/")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertTrue((target / "engine/opbuild/__main__.py").is_file())

    def test_rsync_options_follow_the_separator(self):
        result = self.push(str(self.target) + "/", "--", "--exclude", "docker/")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.target / "op").is_file())
        self.assertFalse((self.target / "docker").exists())
        for args, message in (
            (("--exclude", "docker/"), "Unknown option: --exclude"),
            (("docker/",), "Unexpected argument: docker/"),
        ):
            with self.subTest(args=args):
                result = self.push(str(self.base / "fresh") + "/", *args)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
                self.assertFalse((self.base / "fresh").exists())

    def remote_env(self, root=True, **overrides):
        """Configure an earlier deployment as the push target, with fake remote commands."""
        self.target = self.base / "builder's $(touch INJECTED) directory"
        self.deployed(self.target)
        for name in ("workspace/dl/archive", "workspace/src/build_dir/object", "output/demo/firmware.bin"):
            write(self.target / name, "retained\n")
        write(self.source / ".push.conf",
              f"REMOTE_HOST=builder\nREMOTE_PATH={shlex.quote(str(self.target))}\nSSH_PORT=2222\n")
        self.ssh_log = self.base / "ssh.jsonl"
        self.chown_log = self.base / "chown.json"
        self.ssh_log.unlink(missing_ok=True)
        self.chown_log.unlink(missing_ok=True)
        commands = {
            "rsync": '''import os, sys
args = sys.argv[1:]
if args != ["--help"] and "--server" not in args:
    if os.environ.get("FAIL_SYNC"): sys.exit(1)
    args[-1] = args[-1].split(":", 1)[1]
os.execv(os.environ["REAL_RSYNC"], [os.environ["REAL_RSYNC"], *args])
''',
            "ssh": '''import json, os, subprocess, sys
from pathlib import Path
with Path(os.environ["SSH_LOG"]).open("a") as stream:
    stream.write(json.dumps(sys.argv[1:]) + "\\n")
sys.exit(subprocess.call(["sh", "-c", sys.argv[-1]]))
''',
            "chown": '''import json, os, sys
from pathlib import Path
with Path(os.environ["CHOWN_LOG"]).open("a") as stream:
    stream.write(json.dumps({"args": sys.argv[1:], "cwd": os.getcwd()}) + "\\n")
sys.exit(1 if os.environ.get("FAIL_CHOWN") else 0)
''',
            "id": '''import os, sys
if sys.argv[1:] == ["-u"] and os.environ.get("FAKE_ROOT"):
    print(0)
else:
    os.execv("/usr/bin/id", ["id", *sys.argv[1:]])
''',
        }
        bin_dir = self.base / "bin"
        for name, content in commands.items():
            write(bin_dir / name, "#!/usr/bin/env python3\n" + content, 0o755)
        env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", REAL_RSYNC=shutil.which("rsync"),
                   SSH_LOG=str(self.ssh_log), CHOWN_LOG=str(self.chown_log))
        if root:
            env["FAKE_ROOT"] = "1"
        env.update(overrides)
        return env

    def remote_push(self, *args, root=True, **overrides):
        return self.push(*args, env=self.remote_env(root, **overrides))

    def chown_calls(self):
        return [json.loads(line) for line in self.chown_log.read_text().splitlines()]

    def ssh_calls(self):
        """Remote commands, keyed by purpose: the pre-sync check and the ownership update."""
        calls = [json.loads(line) for line in self.ssh_log.read_text().splitlines()]
        return [("chown" if "chown" in call[-1] else "check", call[:-1]) for call in calls]

    def test_root_sync_matches_builder_ownership(self):
        result = self.remote_push()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.ssh_calls(), [("check", ["-p", "2222", "builder"]),
                                            ("chown", ["-p", "2222", "builder"])])
        calls = self.chown_calls()
        for call in calls:
            self.assertEqual(call["args"][:3], ["-h", "1000:1000", "--"])
            self.assertEqual(call["cwd"], str(self.target.resolve()))
        paths = {path for call in calls for path in call["args"][3:]}
        self.assertIn("./profiles/demo/profile.toml", paths)
        self.assertIn("./output/demo/firmware.bin", paths)
        self.assertFalse(any(path.startswith("./workspace") for path in paths))
        self.assertFalse((self.base / "INJECTED").exists())

    def test_non_root_sync_keeps_ownership(self):
        result = self.remote_push(root=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual([purpose for purpose, _ in self.ssh_calls()], ["check", "chown"])
        self.assertFalse(self.chown_log.exists())
        self.assertTrue((self.target / "profiles/demo/profile.toml").is_file())

    def test_explicit_destination_overrides_configuration(self):
        other = self.base / "override"
        result = self.remote_push(f"other-builder:{other}")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual({tuple(args) for _, args in self.ssh_calls()}, {("-p", "2222", "other-builder")})
        self.assertTrue(all(call["cwd"] == str(other.resolve()) for call in self.chown_calls()))
        self.assertFalse((self.target / "profiles").exists())

    def test_remote_destination_checks(self):
        for args, conf, message in (
            ((), "REMOTE_PATH=relative", "Remote path must be absolute: builder:relative"),
            (("builder:",), "", "Remote path must be absolute: builder:"),
            (("builder:~/x",), "", "Remote path must be absolute: builder:~/x"),
        ):
            with self.subTest(args=args, conf=conf):
                env = self.remote_env()
                with (self.source / ".push.conf").open("a") as stream:
                    stream.write(conf + "\n")
                result = self.push(*args, env=env)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
                self.assertFalse(self.ssh_log.exists())

        env = self.remote_env()
        (self.target / "op").unlink()
        result = self.push(env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("neither empty nor a copy of this project", result.stderr)
        self.assertEqual([purpose for purpose, _ in self.ssh_calls()], ["check"])
        self.assertFalse((self.target / "profiles").exists())
        self.assertTrue((self.target / "workspace/dl/archive").is_file())

    def test_dry_run_and_failures(self):
        result = self.remote_push("-n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.target / "profiles").exists())
        self.assertEqual([purpose for purpose, _ in self.ssh_calls()], ["check"])

        result = self.remote_push(FAIL_SYNC="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual([purpose for purpose, _ in self.ssh_calls()], ["check"])

        result = self.remote_push(FAIL_CHOWN="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Failed to verify or change remote project ownership", result.stderr)
        self.assertNotIn("Sync complete", result.stdout)


if __name__ == "__main__":
    unittest.main()
