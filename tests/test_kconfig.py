"""Kconfig seed parsing, merging and verification."""

import unittest

import support  # noqa: F401
from opbuild import kconfig
from opbuild.common import OpError


class KconfigTests(unittest.TestCase):
    def test_parse_normalizes_unset_and_rejects_invalid_lines(self):
        values = kconfig.parse(
            "# comment\nCONFIG_A=y\n# CONFIG_B is not set\nCONFIG_C=n\nCONFIG_D=\"a=b\"\nCONFIG_A=m\n", "seed")
        self.assertEqual(values, {"B": "n", "C": "n", "D": '"a=b"', "A": "m"})
        with self.assertRaisesRegex(OpError, "seed:2: not a Kconfig assignment"):
            kconfig.parse("CONFIG_A=y\nCONFIG_B = y\n", "seed")

    def test_last_assignment_wins_and_render_is_sorted(self):
        merged = kconfig.merge([{"B": "y", "A": "y"}, {"A": "n"}, {"C": '"x"'}])
        self.assertEqual(kconfig.render(merged),
                         '# CONFIG_A is not set\nCONFIG_B=y\nCONFIG_C="x"\n')

    def test_lost_reports_every_dropped_request(self):
        requested = {"Y": "y", "M": "m", "PROMOTED": "m", "S": '"v"', "N": "n", "GONE": "y"}
        actual = {"Y": "y", "M": "y", "PROMOTED": "y", "S": '"other"', "N": "y"}
        self.assertEqual(kconfig.lost(requested, actual), [
            'CONFIG_S="v" (now: CONFIG_S="other")',
            "CONFIG_GONE=y (now: # CONFIG_GONE is not set)",
        ])
        self.assertEqual(kconfig.lost({"Y": "y"}, {"Y": "m"}), ["CONFIG_Y=y (now: CONFIG_Y=m)"])


if __name__ == "__main__":
    unittest.main()
