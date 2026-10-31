"""Profile resolution: schema, seeds, feature selection and wifi checks."""

import datetime
import shutil
import unittest

from support import PROFILE, REPO, make_root, write
from opbuild import config, schema
from opbuild.common import OpError


class ConfigTests(unittest.TestCase):
    def resolve(self, profile=PROFILE, files=None):
        root = make_root(self, profile)
        for relative, text in (files or {}).items():
            write(root / relative, text)
        return config.resolve(root, "demo", today=datetime.date(2026, 10, 31))

    def assert_rejected(self, profile, message, files=None):
        with self.assertRaisesRegex(OpError, message):
            self.resolve(profile, files)

    def test_example_profile_resolves(self):
        root = make_root(self, None)
        shutil.copytree(REPO / "examples/profile", root / "profiles/example")
        resolved = config.resolve(root, "example")
        self.assertIn("wifi", resolved.enabled)
        self.assertEqual(resolved.params["wifi"]["ap"], [])

    def test_feature_selection_controls_owned_packages(self):
        resolved = self.resolve()
        requested = resolved.requested
        self.assertEqual(requested["PACKAGE_luci-app-nikki"], "y")
        self.assertEqual(requested["PACKAGE_kmod-ath11k-pci"], "y")
        # Disabled features remove upstream device defaults such as the LED app.
        self.assertEqual(requested["PACKAGE_luci-app-athena-led"], "n")
        self.assertEqual(requested["FEED_nikki"], "n")
        self.assertEqual(requested["TARGET_qualcommax_ipq60xx_DEVICE_jdcloud_re-cs-02"], "y")
        self.assertEqual(requested["VERSION_NUMBER"], '"26.10"')
        self.assertEqual(resolved.layers[-1][0], "generated")

    def test_profile_seed_overrides_options_but_not_ownership_or_target(self):
        seed = "profiles/demo/config.seed"
        resolved = self.resolve(files={seed: "CONFIG_DEVEL=n\nCONFIG_TARGET_ROOTFS_EXT4FS=y\n"})
        self.assertEqual(resolved.requested["DEVEL"], "n")
        self.assertEqual(resolved.requested["TARGET_ROOTFS_EXT4FS"], "y")
        for line, message in (
            ("# CONFIG_PACKAGE_iwinfo is not set", "belongs to feature wifi"),
            ("CONFIG_PACKAGE_luci-app-athena-led=y", "belongs to feature athena-led"),
            ("CONFIG_TARGET_PER_DEVICE_ROOTFS=y", "selects the target"),
            ("CONFIG_TARGET_MULTI_PROFILE=y", "selects the target"),
            ("CONFIG_TARGET_qualcommax_ipq60xx_DEVICE_other=y", "selects the target"),
        ):
            with self.subTest(line=line):
                self.assert_rejected(PROFILE, message, {seed: line + "\n"})

    def test_base_and_feature_seeds_cannot_claim_owned_packages(self):
        self.assert_rejected(PROFILE, "base/config.seed: CONFIG_PACKAGE_zram-swap belongs to feature zram",
                             {"base/config.seed": "CONFIG_PACKAGE_zram-swap=y\n"})
        self.assert_rejected(PROFILE, "features/zram/config.seed: CONFIG_PACKAGE_bash belongs",
                             {"features/zram/config.seed": "CONFIG_PACKAGE_bash=y\n"})

    def test_catalog_rejects_shared_package_ownership(self):
        self.assert_rejected(PROFILE, "package zram-swap is already owned by feature",
                             {"features/zz/feature.toml": 'description = "x"\npackages = ["zram-swap"]\n'})

    def test_profile_structure_errors(self):
        for profile, message in (
            ('features = ["wifi", "wifi"]\n', "duplicate feature"),
            ('features = ["nope"]\n', "unknown feature"),
            ('features = []\n[nope]\n', "unknown key"),
            ('features = []\n[system]\nhostname = "bad name"\n', "system.hostname: must match"),
            ('features = []\n[system]\nlan_ip = "192.168.1.0"\n', "host address"),
            ('features = ["pppoe"]\n', "pppoe.username: required"),
            ('features = ["nikki"]\n[nikki]\nx = 1\n', "feature nikki has no parameters"),
            ('features = ["wifi"]\n[wifi]\ncolour = "red"\n', r"wifi: unknown key\(s\): colour"),
            ('features = ["wifi"]\n[wifi]\nradios.radio0 = { channel = "149" }\n', "must be an integer"),
            ('features = []\n[source]\ncommit = "abc"\n', "source.commit: must match"),
        ):
            with self.subTest(profile=profile):
                self.assert_rejected(profile, message)

    def test_disabled_feature_parameters_are_ignored_with_warning(self):
        resolved = self.resolve('features = []\n[pppoe]\nusername = "u"\n')
        self.assertNotIn("pppoe", resolved.params)
        self.assertIn("[pppoe] is ignored", resolved.warnings[0])

    def test_secrets_are_masked_and_never_echoed(self):
        document = self.resolve().document(masked=True)["params"]
        self.assertEqual(document["pppoe"], {"username": "******", "password": "******"})
        self.assertEqual([ap["key"] for ap in document["wifi"]["ap"]], ["******", "******"])
        self.assertEqual(document["zerotier"]["networks"], ["******"])
        self.assertEqual(document["wifi"]["ap"][0]["ssid"], "Main SSID")
        with self.assertRaises(OpError) as caught:
            self.resolve('features = ["zerotier"]\n[zerotier]\nnetworks = ["secret-id"]\n')
        self.assertNotIn("secret-id", str(caught.exception))

    def test_wifi_cross_checks(self):
        base = 'features = ["wifi"]\n[wifi]\n'
        ap = '[[wifi.ap]]\nname = "{name}"\nradio = "radio0"\nssid = "x"\n{extra}\n'
        for body, message in (
            ('radios.radio0 = { channel = 150 }\n', "150 is not available on radio0"),
            ('radios.radio0 = { channel = 36 }\n', "36 is not available on radio0"),
            ('radios.radio0 = { channel = 165, htmode = "HE40" }\n', "supports HE20 only"),
            ('radios.radio1 = { channel = 6, htmode = "HE80" }\n', "supports up to HE40"),
            ('networks.guest = { subnet = "172.16.1.1/25", access = "internet" }\n', "must be a /24"),
            ('networks.guest = { subnet = "172.16.1.150/24", access = "internet" }\n', "outside the DHCP pool"),
            ('networks.guest = { subnet = "192.168.1.2/24", access = "internet" }\n', "overlaps lan"),
            ('networks.lan = { subnet = "172.16.1.1/24", access = "lan" }\n', "name is reserved"),
            ('networks.guest = { subnet = "172.16.1.1/24" }\n', "access: required"),
            (ap.format(name="radio0", extra='key = "12345678"'), "reserved for wireless devices"),
            (ap.format(name="a", extra='key = "12345678"\nnetwork = "guest"'), "neither lan nor a defined"),
            (ap.format(name="a", extra='key = "1234567"'), "8-63 printable ASCII"),
            (ap.format(name="a", extra=''), "key: required for psk2"),
            (ap.format(name="a", extra='encryption = "none"\nkey = "12345678"'), "must be omitted"),
        ):
            with self.subTest(body=body):
                self.assert_rejected(base + body, message)
        resolved = self.resolve(base + ap.format(name="a", extra='encryption = "sae"\nkey = "' + "k" * 70 + '"'))
        self.assertEqual(resolved.params["wifi"]["ap"][0]["network"], "lan")

    def test_schema_declarations_are_checked(self):
        for spec, message in (
            ({"type": "table", "fields": {}}, "only supported as list or map items"),
            ({"type": "map", "keys": "x", "items": {"type": "string"}}, "map items must be tables"),
            ({"type": "string", "required": True, "default": "x"}, "cannot have a default"),
            ({"type": "integer", "pattern": "x"}, "pattern applies to strings only"),
        ):
            with self.subTest(spec=spec), self.assertRaisesRegex(OpError, message):
                schema.check_spec(spec, "spec")


if __name__ == "__main__":
    unittest.main()
