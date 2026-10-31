"""Kconfig seed parsing, merging and post-defconfig verification."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Iterable

from .common import OpError

ASSIGN = re.compile(r"CONFIG_([A-Za-z0-9_][A-Za-z0-9_.+-]*)=(.*)")
UNSET = re.compile(r"# CONFIG_([A-Za-z0-9_][A-Za-z0-9_.+-]*) is not set")
NOT_SET = "n"


def parse(text: str, source: str, strict: bool = True) -> dict[str, str]:
    """Map symbols (without CONFIG_) to values; "is not set" and "=n" become NOT_SET."""
    values: dict[str, str] = {}
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        match = UNSET.fullmatch(line)
        if match:
            symbol, value = match[1], NOT_SET
        elif not line or line.startswith("#"):
            continue
        elif (match := ASSIGN.fullmatch(line)):
            symbol, value = match[1], NOT_SET if match[2] == "n" else match[2]
        elif strict:
            raise OpError(f"{source}:{number}: not a Kconfig assignment: {line}")
        else:
            continue
        values.pop(symbol, None)
        values[symbol] = value
    return values


def read(path: Path) -> dict[str, str]:
    return parse(path.read_text(), str(path), strict=False)


def merge(layers: Iterable[dict[str, str]]) -> dict[str, str]:
    """The last assignment wins, including explicit NOT_SET."""
    result: dict[str, str] = {}
    for layer in layers:
        for symbol, value in layer.items():
            result.pop(symbol, None)
            result[symbol] = value
    return result


def line(symbol: str, value: str) -> str:
    return f"# CONFIG_{symbol} is not set" if value == NOT_SET else f"CONFIG_{symbol}={value}"


def render(values: dict[str, str]) -> str:
    return "".join(line(symbol, values[symbol]) + "\n" for symbol in sorted(values))


def lost(requested: dict[str, str], actual: dict[str, str]) -> list[str]:
    """Requested values that defconfig did not keep. NOT_SET requests are not checked."""
    missing = []
    for symbol, value in requested.items():
        if value == NOT_SET:
            continue
        current = actual.get(symbol, NOT_SET)
        # A module promoted to built-in by another selection is still provided.
        if current == value or (value == "m" and current == "y"):
            continue
        missing.append(f"{line(symbol, value)} (now: {line(symbol, current)})")
    return missing
