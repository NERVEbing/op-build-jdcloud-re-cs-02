"""Source preparation against local git repositories."""

from pathlib import Path
import subprocess
import unittest

from support import make_root, write
from opbuild import source
from opbuild.common import OpError
from opbuild.config import Feature


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid",
                           *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def repository(path: Path, files: dict[str, str], branch: str = "main") -> str:
    path.mkdir(parents=True, exist_ok=True)
    if not (path / ".git").exists():
        git(path, "init", "-q", "-b", branch)
        # Like GitHub, allow fetching reachable commits by SHA over the file transport.
        git(path, "config", "uploadpack.allowReachableSHA1InWant", "true")
    for name, text in files.items():
        write(path / name, text)
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "update")
    return git(path, "rev-parse", "HEAD")


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.root = make_root(self, None)
        self.upstream = self.root / "upstream"
        repository(self.upstream, {"README": "owrt\n"}, branch="owrt")
        git(self.upstream, "checkout", "-q", "-b", "main")
        self.first = repository(self.upstream, {
            "README": "main 1\n",
            "feeds.conf.default": "src-git packages https://example.invalid/packages\n",
            "package/base/Makefile": "tracked\n",
        })
        # The upstream default branch is not main, as with VIKINGYFY/immortalwrt.
        git(self.upstream, "checkout", "-q", "owrt")
        self.url = self.upstream.as_uri()
        self.workspace = self.root / "workspace"
        self.src = self.workspace / "src"

    def prepare(self, commit=""):
        source.prepare_source(self.src, self.workspace, {"url": self.url, "branch": "main", "commit": commit})

    def test_clone_update_and_cleanup(self):
        self.prepare()
        self.assertEqual((self.src / "README").read_text(), "main 1\n")
        self.assertEqual((self.src / "dl").readlink(), Path("../dl"))
        self.assertEqual((self.src / ".ccache").readlink(), Path("../ccache"))
        self.assertEqual((self.workspace / "ccache/ccache.conf").read_text(), "max_size = 20GB\n")

        write(self.src / "README", "local edit\n")
        write(self.src / "package/stale/Makefile", "old feature\n")
        write(self.src / "package/feeds/x/link", "old link\n")
        write(self.src / "build_dir/object", "cache\n")
        write(self.src / "feeds.conf", "src-git other https://example.invalid/other\n")
        git(self.upstream, "checkout", "-q", "main")
        repository(self.upstream, {"README": "main 2\n"})
        git(self.upstream, "checkout", "-q", "owrt")
        self.prepare()

        self.assertEqual((self.src / "README").read_text(), "main 2\n")
        self.assertEqual((self.src / "package/base/Makefile").read_text(), "tracked\n")
        self.assertFalse((self.src / "package/stale").exists())
        self.assertFalse((self.src / "package/feeds").exists())
        self.assertFalse((self.src / "feeds.conf").exists())
        self.assertEqual((self.src / "build_dir/object").read_text(), "cache\n")

    def test_commit_pin(self):
        self.prepare()
        git(self.upstream, "checkout", "-q", "main")
        repository(self.upstream, {"README": "main 2\n"})
        self.prepare(commit=self.first)
        self.assertEqual(git(self.src, "rev-parse", "HEAD"), self.first)
        self.assertEqual((self.src / "README").read_text(), "main 1\n")

    def test_refuses_non_git_directory(self):
        write(self.src / "file", "x\n")
        with self.assertRaisesRegex(OpError, "not a git checkout"):
            self.prepare()

    def feature(self, name, kind, url, ref, packages, subdir=None):
        spec = {"type": kind, "url": url, "ref": ref}
        if subdir:
            spec["subdir"] = subdir
        return Feature(name, self.root / "features" / name, "test", tuple(packages), spec, {})

    def test_external_package_and_feed_sources(self):
        self.prepare()
        plugins = self.root / "plugins"
        revision = repository(plugins, {"luci-app-x/Makefile": "x\n", "other/file": "y\n"})
        sha = "a" * 40
        records = source.prepare_external(self.src, [
            self.feature("x", "package", plugins.as_uri(), "main", ["luci-app-x"], "luci-app-x"),
            self.feature("tagged", "feed", "https://example.invalid/tagged.git", "v1.0", ["a"]),
            self.feature("pinned", "feed", "https://example.invalid/pinned.git", sha, ["b"]),
        ], self.src / ".op/staging")
        self.assertEqual(records, [["x", plugins.as_uri(), "main", revision]])
        self.assertEqual((self.src / "package/luci-app-x/Makefile").read_text(), "x\n")
        self.assertFalse((self.src / "package/other").exists())
        self.assertEqual(list((self.src / ".op/staging").iterdir()), [])
        feeds = (self.src / "feeds.conf.default").read_text().splitlines()
        self.assertEqual(feeds[-2:], ["src-git tagged https://example.invalid/tagged.git;v1.0",
                                      f"src-git pinned https://example.invalid/pinned.git^{sha}"])

    def test_external_package_conflicts_and_fetch_failures(self):
        self.prepare()
        plugins = self.root / "plugins"
        repository(plugins, {"Makefile": "x\n"})
        staging = self.src / ".op/staging"
        with self.assertRaisesRegex(OpError, "already exists upstream"):
            source.prepare_external(self.src, [self.feature("b", "package", plugins.as_uri(), "main",
                                                            ["base"])], staging)
        with self.assertRaises(OpError):
            source.prepare_external(self.src, [self.feature("y", "package", plugins.as_uri(), "missing",
                                                            ["luci-app-y"])], staging)
        self.assertFalse((self.src / "package/luci-app-y").exists())
        self.assertEqual(list(staging.iterdir()), [])

    def test_update_feeds_prunes_unconfigured_feed_data(self):
        write(self.src / "feeds.conf.default",
              "src-git packages https://example.invalid/packages\n"
              "src-git nikki https://example.invalid/nikki;v1\n")
        calls = self.root / "feeds-calls"
        write(self.src / "scripts/feeds", f'#!/bin/sh\necho "$*" >> "{calls}"\n', 0o755)
        nikki = repository(self.src / "feeds/nikki", {"Makefile": "x\n"})
        for name in ("packages/Makefile", "packages.index", "old/Makefile", "old.index", "old.tmp/info"):
            write(self.src / "feeds" / name, "x\n")
        records = source.update_feeds(self.src, [
            self.feature("nikki", "feed", "https://example.invalid/nikki", "v1", ["luci-app-nikki"])])
        self.assertEqual(calls.read_text().splitlines(), ["update -a", "uninstall -a", "install -a"])
        self.assertEqual(sorted(path.name for path in (self.src / "feeds").iterdir()),
                         ["nikki", "packages", "packages.index"])
        self.assertEqual(records, [["nikki", "https://example.invalid/nikki", "v1", nikki]])


if __name__ == "__main__":
    unittest.main()
