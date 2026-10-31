"""Shared helpers for the unit tests."""

from __future__ import annotations

from pathlib import Path
import shutil
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "engine"))

PROFILE = """
features = ["wifi", "pppoe", "zerotier", "nikki", "tools"]

[system]
hostname = "Demo"
lan_ip = "192.168.50.1"

[wifi]
quiet_log = true
radios.radio0 = { channel = 149, htmode = "HE80" }
radios.radio2 = { channel = 36, htmode = "HE160" }
networks.wifi = { subnet = "172.16.0.1/24", access = "lan" }
networks.guest = { subnet = "172.16.1.1/24", access = "internet" }

[[wifi.ap]]
name = "main"
radio = "radio0"
ssid = "Main SSID"
key = "main-secret-key"
network = "wifi"

[[wifi.ap]]
name = "guest"
radio = "radio2"
ssid = "Guest"
key = "guest-secret-key"
network = "guest"

[pppoe]
username = "pppoe-user"
password = "pppoe-pass'word"

[zerotier]
networks = ["0123456789abcdef"]
"""


def make_root(test: unittest.TestCase, profile: str | None = PROFILE, name: str = "demo") -> Path:
    """A disposable project root with the repository's engine, base and features."""
    temp = tempfile.TemporaryDirectory(prefix="opb-test-")
    test.addCleanup(temp.cleanup)
    root = Path(temp.name)
    for part in ("engine", "base", "features"):
        shutil.copytree(REPO / part, root / part, ignore=shutil.ignore_patterns("__pycache__"))
    if profile is not None:
        write(root / "profiles" / name / "profile.toml", profile)
    return root


def write(path: Path, text: str, mode: int | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    if mode is not None:
        path.chmod(mode)
    return path
