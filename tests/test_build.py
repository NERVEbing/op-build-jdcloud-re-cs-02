"""Setup, compile guards, fingerprints and artifact handling with stubbed sources."""

import json
import os
from pathlib import Path
import unittest
from unittest import mock

from support import make_root, write
from opbuild import build, config, source
from opbuild.common import OpError

PROFILE = """
features = ["wifi", "pppoe", "tools"]

[system]
hostname = "Demo"
lan_ip = "192.168.50.1"

[wifi]
quiet_log = true
networks.guest = { subnet = "172.16.1.1/24", access = "internet" }

[pppoe]
username = "user"
password = "secret"
"""

FAKE_MAKE = r'''#!/usr/bin/env python3
import os, sys
from pathlib import Path
mode = os.environ.get("FAKE_MAKE", "")
if "defconfig" in sys.argv[1:]:
    with open(os.environ["FAKE_MAKE_LOG"], "a") as log:
        log.write(" ".join(sys.argv[1:]) + "\n")
    if mode == "fail":
        sys.exit(2)
    if mode == "error-line":
        print("tmp/.config-package.in:1:error: recursive dependency detected!")
    drop = os.environ.get("FAKE_MAKE_DROP")
    lines = [line for line in Path(".config").read_text().splitlines()
             if not drop or not line.startswith(f"CONFIG_{drop}=")]
    if os.environ.get("FAKE_MAKE_ADD"):
        lines.append(os.environ["FAKE_MAKE_ADD"])
    Path(".config").write_text("\n".join(lines) + "\n")
    Path("tmp").mkdir(exist_ok=True)
    Path("tmp/.packageinfo").write_text("")
'''

HOOK = '#!/usr/bin/env bash\necho "{label}:$OP_PROFILE:$OP_LAN_IP:$(stat -c %a "$OP_RESOLVED" 2>/dev/null ' \
       '|| stat -f %Lp "$OP_RESOLVED")" >> "$OP_ROOT/hooks.log"\n'


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.root = make_root(self, PROFILE)
        self.ws = build.Workspace(self.root)
        self.src = self.ws.src
        bin_dir = self.root / "bin"
        write(bin_dir / "make", FAKE_MAKE, 0o755)
        self.environment = mock.patch.dict(os.environ, {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "FAKE_MAKE_LOG": str(self.root / "make.log"),
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        # Like "git reset --hard", every setup starts from pristine upstream files.
        patchers = [mock.patch.object(source, "prepare_source", side_effect=lambda *_: self.upstream())]
        patchers += [mock.patch.object(source, name, return_value=[])
                     for name in ("prepare_external", "update_feeds")]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def upstream(self):
        write(self.src / "package/base-files/files/bin/config_generate",
              "\t\tset system.@system[-1].hostname='ImmortalWRT'\n"
              '\t\t\t\tlan) ipad=${ipaddr:-"192.168.1.1"} ;;\n')
        write(self.src / "package/network/config/wifi-scripts/files/lib/wifi/mac80211.uc",
              "set ${si}.ssid='${defaults?.ssid || 'OWRT'}'\n")
        write(self.src / "package/base-files/files/etc/banner", "upstream\n")
        write(self.src / "package/network/services/hostapd/files/wpad.init",
              "\t\tprocd_set_param command /usr/sbin/hostapd -s -g /var/run/hostapd/global\n")
        write(self.src / "scripts/feeds", "#!/bin/sh\n", 0o755)

    def resolve(self):
        return config.resolve(self.root, "demo")

    def setup_build(self, **env):
        with mock.patch.dict(os.environ, env):
            build.setup(self.resolve(), self.ws)

    def test_setup_layers_hooks_branding_and_markers(self):
        for stage in ("post-feeds", "post-config"):
            write(self.root / f"profiles/demo/hooks/{stage}.sh", HOOK.format(label=f"profile-{stage}"))
        write(self.root / "features/tools/hooks/post-feeds.sh", HOOK.format(label="tools-post-feeds"))
        # A post-config hook output is overridden by profile files, which are applied last.
        write(self.root / "features/tools/hooks/post-config.sh",
              '#!/usr/bin/env bash\nmkdir -p files/etc && echo hook > files/etc/generated\n')
        write(self.root / "profiles/demo/files/etc/generated", "profile\n")
        write(self.root / "profiles/demo/files/etc/uci-defaults/90-custom", "#!/bin/sh\n")
        write(self.root / "profiles/demo/banner", "custom banner\n")

        self.setup_build()

        lines = (self.root / "hooks.log").read_text().splitlines()
        self.assertEqual(lines, ["tools-post-feeds:demo:192.168.50.1:600",
                                 "profile-post-feeds:demo:192.168.50.1:600",
                                 "profile-post-config:demo:192.168.50.1:600"])
        resolved = json.loads((self.ws.meta / "resolved.json").read_text())
        self.assertEqual(resolved["params"]["pppoe"]["password"], "secret")
        self.assertEqual((self.src / ".config").read_text(), (self.ws.meta / "requested.config").read_text())
        self.assertIn("RECURSIVE_DEP_IS_ERROR=1 defconfig", (self.root / "make.log").read_text())

        generate = (self.src / "package/base-files/files/bin/config_generate").read_text()
        self.assertIn("hostname='Demo'", generate)
        self.assertIn('"192.168.50.1"', generate)
        self.assertIn("'ImmortalWrt'", (self.src / "package/network/config/wifi-scripts/files/lib/wifi"
                                        "/mac80211.uc").read_text())
        self.assertEqual((self.src / "package/base-files/files/etc/banner").read_text(), "custom banner\n")
        self.assertIn(" -s -q -g ", (self.src / "package/network/services/hostapd/files/wpad.init").read_text())

        files = self.src / "files"
        self.assertEqual((files / "etc/generated").read_text(), "profile\n")
        for script in ("10-op-base", "45-op-cleanup", "50-op-wifi", "60-op-pppoe", "90-custom"):
            self.assertTrue(os.access(files / "etc/uci-defaults" / script, os.X_OK), script)
        self.assertFalse((files / "etc/uci-defaults/70-op-zerotier").exists())
        self.assertEqual((files / "etc/op-build/pppoe").stat().st_mode & 0o777, 0o600)
        self.assertTrue((files / "usr/share/op-build/nftables/guest.nft").is_file())
        self.assertEqual((self.ws.meta / "profile").read_text(), "demo\n")
        self.assertEqual((self.ws.meta / "fingerprint").read_text().strip(), build.fingerprint(self.resolve()))

    def test_setup_rebuilds_files_from_scratch(self):
        write(self.src / "files/etc/stale", "old\n")
        self.setup_build()
        self.assertFalse((self.src / "files/etc/stale").exists())

    def test_hook_failure_stops_setup_and_invalidates_markers(self):
        self.setup_build()
        write(self.root / "profiles/demo/hooks/post-feeds.sh", "#!/usr/bin/env bash\nexit 3\n")
        with self.assertRaisesRegex(OpError, "hook profiles/demo/hooks/post-feeds.sh failed"):
            self.setup_build()
        self.assertFalse((self.ws.meta / "profile").exists())
        self.assertFalse((self.ws.meta / "fingerprint").exists())

    def test_defconfig_verification(self):
        device = "TARGET_qualcommax_ipq60xx_DEVICE_jdcloud_re-cs-02"
        for env, message in (
            ({"FAKE_MAKE": "fail"}, "make defconfig failed"),
            ({"FAKE_MAKE": "error-line"}, "Kconfig reported an error"),
            ({"FAKE_MAKE_DROP": "VERSION_DIST"}, "dropped CONFIG_VERSION_DIST"),
            ({"FAKE_MAKE_DROP": device}, f"did not select CONFIG_{device}"),
            ({"FAKE_MAKE_ADD": "CONFIG_TARGET_MULTI_PROFILE=y"}, "MULTI_PROFILE must stay disabled"),
            ({"FAKE_MAKE_DROP": "DEVEL"}, "configuration does not match the request"),
            ({"FAKE_MAKE_DROP": "PACKAGE_iwinfo"}, "configuration does not match the request"),
        ):
            with self.subTest(env=env), self.assertRaisesRegex(OpError, message):
                self.setup_build(**env)
        with open(self.root / "profiles/demo/profile.toml", "a") as profile:
            profile.write("\n[build]\nstrict_config = false\n")
        self.setup_build(FAKE_MAKE_DROP="DEVEL")
        self.assertTrue((self.ws.meta / "profile").is_file())

    def test_feature_sources_must_come_from_the_feature(self):
        write(self.root / "profiles/demo/profile.toml", 'features = ["nikki", "wolultra"]\n')
        resolved = self.resolve()
        info = write(self.src / "tmp/.packageinfo",
                     "Source-Makefile: package/feeds/nikki/luci-app-nikki/Makefile\nPackage: luci-app-nikki\n"
                     "Source-Makefile: package/luci-app-wolultra/Makefile\nPackage: luci-app-wolultra\n")
        build.verify_feature_sources(resolved, self.ws)
        with info.open("a") as stream:
            stream.write("Source-Makefile: package/feeds/luci/luci-app-wolultra/Makefile\nPackage: luci-app-wolultra\n")
        with self.assertRaisesRegex(OpError, "also provided by package/feeds/luci/luci-app-wolultra"):
            build.verify_feature_sources(resolved, self.ws)
        info.write_text("")
        with self.assertRaisesRegex(OpError, "not found in tmp/.packageinfo"):
            build.verify_feature_sources(resolved, self.ws)

    def test_fingerprint_tracks_inputs(self):
        resolved = self.resolve()
        original = build.fingerprint(resolved)
        write(self.root / "engine/opbuild/__pycache__/x.cpython-312.pyc", "bytecode")
        write(self.root / "base/stray.pyc", "bytecode")
        self.assertEqual(build.fingerprint(resolved), original)
        hook = write(self.root / "profiles/demo/hooks/post-config.sh", "#!/bin/sh\n")
        changed = build.fingerprint(resolved)
        self.assertNotEqual(changed, original)
        hook.chmod(0o755)
        self.assertNotEqual(build.fingerprint(resolved), changed)
        executable = build.fingerprint(resolved)
        hook.rename(hook.with_name("post-feeds.sh"))
        self.assertNotEqual(build.fingerprint(resolved), executable)

    def test_fingerprint_follows_links_except_in_overlays(self):
        profile = self.root / "profiles/demo/profile.toml"
        text = profile.read_text()
        shared = write(self.root / "profiles/shared/profile.toml", text)
        profile.unlink()
        profile.symlink_to("../shared/profile.toml")
        (self.root / "profiles/linked").symlink_to("demo")
        for index, name in enumerate(("demo", "linked")):
            with self.subTest(profile=name):
                before = build.fingerprint(config.resolve(self.root, name))
                shared.write_text(text.replace('"Demo"', f'"Host{index}"'))
                self.assertNotEqual(build.fingerprint(config.resolve(self.root, name)), before)

        # Overlay links are copied into the image as links; only their targets count.
        external = write(self.root / "external/data", "a\n")
        link = self.root / "profiles/demo/files/etc/data"
        link.parent.mkdir(parents=True)
        link.symlink_to(external.parent)
        resolved = self.resolve()
        before = build.fingerprint(resolved)
        external.write_text("b\n")
        self.assertEqual(build.fingerprint(resolved), before)
        link.unlink()
        link.symlink_to("/tmp")
        self.assertNotEqual(build.fingerprint(resolved), before)

        (self.root / "profiles/demo/loop").symlink_to(".")
        with self.assertRaisesRegex(OpError, "symlink loop in build inputs: profiles/demo/loop"):
            build.fingerprint(resolved)

    def test_compile_requires_matching_setup(self):
        resolved = self.resolve()
        with self.assertRaisesRegex(OpError, "not set up"):
            build.compile_firmware(resolved, self.ws, 1)
        self.setup_build()
        (self.ws.meta / "profile").write_text("other\n")
        with self.assertRaisesRegex(OpError, "set up for 'other', not 'demo'"):
            build.compile_firmware(resolved, self.ws, 1)
        (self.ws.meta / "profile").write_text("demo\n")
        write(self.root / "profiles/demo/files/etc/new", "x\n")
        with self.assertRaisesRegex(OpError, "build inputs changed since setup"):
            build.compile_firmware(resolved, self.ws, 1)


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.root = make_root(self)
        self.ws = build.Workspace(self.root)
        self.resolved = config.resolve(self.root, "demo")
        images = self.ws.src / "bin/targets/qualcommax/ipq60xx"
        for kind in ("factory", "sysupgrade"):
            write(images / f"immortalwrt-jdcloud_re-cs-02-{kind}.bin", kind + "\n")
        write(images / "immortalwrt-jdcloud_re-cs-02.manifest", "packages\n")
        write(images / "feeds.buildinfo", "feed revision\n")
        write(images / "sha256sums", "sums\n")
        write(self.ws.src / ".config", "CONFIG_TEST=y\n")
        write(self.ws.meta / "sources.tsv", "nikki\turl\tv1\tsha\n")
        self.output = self.root / "output/demo"

    def history(self):
        self.output.mkdir(parents=True)
        for stamp in ("20261005-120000", "20261006-120000", "20261007-120000"):
            write(self.output / stamp / "firmware.bin", "previous\n")
        (self.output / "latest").symlink_to("20261007-120000")

    def test_collection_and_retention(self):
        self.history()
        other = write(self.root / "output/se/20261005-120000/firmware.bin", "other profile\n")
        write(self.output / "archive/keep", "keep\n")
        (self.output / "20991231-235959").symlink_to(other.parent)
        write(self.output / "20991230-235959", "unrelated file\n")

        build.collect_artifacts(self.resolved, self.ws, stamp="20261008-120000")

        kept = sorted(path.name for path in self.output.iterdir()
                      if path.is_dir() and not path.is_symlink() and path.name != "archive")
        self.assertEqual(kept, ["20261007-120000", "20261008-120000"])
        self.assertEqual(os.readlink(self.output / "latest"), "20261008-120000")
        current = self.output / "20261008-120000"
        self.assertEqual(sorted(path.name for path in current.iterdir()), [
            "demo-feeds.buildinfo", "demo-immortalwrt-jdcloud_re-cs-02-factory.bin",
            "demo-immortalwrt-jdcloud_re-cs-02-sysupgrade.bin", "demo-immortalwrt-jdcloud_re-cs-02.manifest",
            "demo-sources.tsv", "demo.config"])
        self.assertEqual(other.read_text(), "other profile\n")
        self.assertEqual((self.output / "archive/keep").read_text(), "keep\n")
        self.assertTrue((self.output / "20991231-235959").is_symlink())

    def test_first_collection(self):
        build.collect_artifacts(self.resolved, self.ws, stamp="20261008-120000")
        self.assertEqual(sorted(path.name for path in self.output.iterdir()), ["20261008-120000", "latest"])

    def test_failed_collection_preserves_history_and_latest(self):
        self.history()
        (self.ws.src / ".config").unlink()
        with self.assertRaises(OpError):
            build.collect_artifacts(self.resolved, self.ws, stamp="20261008-120000")
        self.assertEqual(os.readlink(self.output / "latest"), "20261007-120000")
        self.assertFalse((self.output / "20261008-120000").exists())
        for stamp in ("20261005-120000", "20261006-120000", "20261007-120000"):
            self.assertTrue((self.output / stamp).is_dir())


if __name__ == "__main__":
    unittest.main()
