"""Rendering of profile parameters into firmware files."""

import importlib.util
from pathlib import Path
import tempfile
import unittest

from support import PROFILE, REPO, make_root
from opbuild import config, render
from opbuild.common import OpError

WIFI = """config wifi 'main'
\toption country 'CN'
\toption quiet_log '1'

config radios 'radios_radio0'
\toption channel '149'
\toption htmode 'HE80'

config radios 'radios_radio2'
\toption channel '36'
\toption htmode 'HE160'

config networks 'networks_guest'
\toption subnet '172.16.1.1/24'
\toption access 'internet'

config networks 'networks_wifi'
\toption subnet '172.16.0.1/24'
\toption access 'lan'

config ap
\toption name 'main'
\toption radio 'radio0'
\toption ssid 'Main SSID'
\toption key 'main-secret-key'
\toption encryption 'psk2'
\toption network 'wifi'

config ap
\toption name 'guest'
\toption radio 'radio2'
\toption ssid 'Guest'
\toption key 'guest-secret-key'
\toption encryption 'psk2'
\toption network 'guest'
"""


class RenderTests(unittest.TestCase):
    def resolve(self, profile=PROFILE):
        return config.resolve(make_root(self, profile), "demo")

    def test_quote_escapes_single_quotes_and_rejects_line_breaks(self):
        self.assertEqual(render.quote("it's"), "'it'\\''s'")
        self.assertEqual(render.quote(False), "'0'")
        with self.assertRaises(OpError):
            render.quote("a\nb")

    def test_parameter_files(self):
        resolved = self.resolve()
        params = resolved.params
        self.assertEqual(render.render_params("wifi", resolved.catalog["wifi"].params, params["wifi"]), WIFI)
        self.assertEqual(render.render_params("pppoe", resolved.catalog["pppoe"].params, params["pppoe"]),
                         "config pppoe 'main'\n\toption username 'pppoe-user'\n"
                         "\toption password 'pppoe-pass'\\''word'\n")
        self.assertEqual(render.render_params("zerotier", resolved.catalog["zerotier"].params,
                                              params["zerotier"]),
                         "config zerotier 'main'\n\tlist networks '0123456789abcdef'\n")

    def test_map_entries_cannot_collide_with_other_sections(self):
        resolved = self.resolve('features = ["wifi"]\n[wifi]\n'
                                'networks.main = { subnet = "172.16.0.1/24", access = "lan" }\n'
                                'radios.radio0 = { channel = 149 }\n')
        text = render.render_params("wifi", resolved.catalog["wifi"].params, resolved.params["wifi"])
        self.assertIn("config wifi 'main'\n", text)
        self.assertIn("config networks 'networks_main'\n", text)
        self.assertIn("config radios 'radios_radio0'\n", text)

    def test_overlay_files_modes_and_isolation_rules(self):
        resolved = self.resolve()
        files = resolved.root / "files"
        written = render.write_overlay(files, resolved)
        names = sorted(path.relative_to(files).as_posix() for path in written)
        self.assertEqual(names, ["etc/op-build/pppoe", "etc/op-build/wifi", "etc/op-build/zerotier",
                                 "usr/share/op-build/nftables/guest.nft"])
        for name in ("pppoe", "wifi", "zerotier"):
            self.assertEqual((files / "etc/op-build" / name).stat().st_mode & 0o777, 0o600)
        rules = files / "usr/share/op-build/nftables/guest.nft"
        self.assertEqual(rules.stat().st_mode & 0o777, 0o644)
        self.assertEqual(rules.read_text(), (REPO / "tests/fixtures/guest.nft").read_text())

    def test_firmware_test_inputs_match_the_renderer(self):
        spec = importlib.util.spec_from_file_location("fixture_render", REPO / "tests/firmware/render.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temp:
            fresh = Path(temp) / "rendered"
            module.render_fixture(fresh)
            committed = REPO / "tests/firmware/rendered"
            names = sorted(path.relative_to(fresh) for path in fresh.rglob("*") if path.is_file())
            self.assertEqual(names, sorted(path.relative_to(committed)
                                           for path in committed.rglob("*") if path.is_file()))
            for name in names:
                self.assertEqual((fresh / name).read_bytes(), (committed / name).read_bytes(),
                                 f"{name} is stale; run: uv run --python 3.12 python tests/firmware/render.py")


if __name__ == "__main__":
    unittest.main()
