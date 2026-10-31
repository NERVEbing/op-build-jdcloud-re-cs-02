"""Render enabled feature parameters into firmware overlay files."""

from __future__ import annotations

import ipaddress
from pathlib import Path
import re
from typing import Any

from . import wifi
from .common import OpError, write_private
from .config import Resolved

PARAMS_DIR = Path("etc/op-build")
NFT_DIR = Path("usr/share/op-build/nftables")
MAP_KEY = re.compile(r"[A-Za-z0-9]+")


def quote(value: Any) -> str:
    """Quote a value for a UCI file; single quotes are escaped shell-style."""
    text = ("1" if value else "0") if isinstance(value, bool) else str(value)
    if any(char in text for char in "\n\r\0"):
        raise OpError("UCI values must not contain line breaks or NUL characters")
    return "'" + text.replace("'", "'\\''") + "'"


def _options(name: str, spec: dict, value: Any) -> list[str]:
    if spec["type"] == "list":
        return [f"\tlist {name} {quote(item)}" for item in value]
    return [f"\toption {name} {quote(value)}"]


def _section(kind: str, name: str | None, fields: dict, values: dict) -> str:
    lines = [f"config {kind}" + (f" {quote(name)}" if name else "")]
    for field, spec in fields.items():
        if field in values:
            lines += _options(field, spec, values[field])
    return "\n".join(lines)


def render_params(feature: str, specs: dict, values: dict) -> str:
    """Scalars go to the "main" section; map entries become "<param>_<key>" sections, and
    table lists become anonymous sections. uci_load is non-strict, so names must not collide."""
    main = [f"config {feature} 'main'"]
    blocks: list[str] = []
    for name, spec in specs.items():
        if name not in values:
            continue
        value = values[name]
        if spec["type"] == "map":
            for key in sorted(value):
                if not MAP_KEY.fullmatch(key):
                    raise OpError(f"{feature}.{name}: key {key!r} cannot be a UCI section suffix")
                blocks.append(_section(name, f"{name}_{key}", spec["items"]["fields"], value[key]))
        elif spec["type"] == "list" and spec["items"]["type"] == "table":
            blocks += [_section(name, None, spec["items"]["fields"], item) for item in value]
        else:
            main += _options(name, spec, value)
    return "\n\n".join(["\n".join(main), *blocks]) + "\n"


def write_overlay(files: Path, resolved: Resolved) -> list[Path]:
    """Write parameter files (0600, they may contain secrets) and generated firmware data."""
    written = []
    for name, values in resolved.params.items():
        path = files / PARAMS_DIR / name
        write_private(path, render_params(name, resolved.catalog[name].params, values))
        written.append(path)
    for name, network in resolved.params.get("wifi", {}).get("networks", {}).items():
        if network["access"] != "internet":
            continue
        gateway = str(ipaddress.IPv4Interface(network["subnet"]).ip)
        path = files / NFT_DIR / f"{name}.nft"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(wifi.isolation_rules(name, gateway))
        path.chmod(0o644)
        written.append(path)
    return written
