"""Logging, errors and process helpers shared by the engine."""

from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys
import time


class OpError(Exception):
    """A build failure reported to the user without a traceback."""


def _prefix(stream, color: str, label: str) -> str:
    text = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}]{label}"
    return f"\033[{color}m{text}\033[0m" if stream.isatty() else text


def log(message: str) -> None:
    print(f"{_prefix(sys.stdout, '0;32', '')} {message}", flush=True)


def step(message: str) -> None:
    print(f"{_prefix(sys.stdout, '0;34', ' ==>')} {message}", flush=True)


def warn(message: str) -> None:
    print(f"{_prefix(sys.stderr, '0;33', ' Warning:')} {message}", file=sys.stderr, flush=True)


def error(message: str) -> None:
    print(f"{_prefix(sys.stderr, '0;31', ' Error:')} {message}", file=sys.stderr, flush=True)


def run(args: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None,
        check: bool = True, capture: bool = False, what: str | None = None) -> subprocess.CompletedProcess:
    """Run a command; raise OpError with a short description when it fails."""
    try:
        result = subprocess.run(args, cwd=cwd, env=env, text=True,
                                stdout=subprocess.PIPE if capture else None,
                                stderr=subprocess.PIPE if capture else None)
    except OSError as error:
        raise OpError(f"{what or args[0]}: {error}") from error
    if check and result.returncode != 0:
        detail = f"\n{result.stderr.strip()}" if capture and result.stderr else ""
        raise OpError(f"{what or ' '.join(args)} failed (exit {result.returncode}){detail}")
    return result


def run_logged(args: list[str], *, cwd: Path, log_path: Path) -> int:
    """Run a command, streaming combined output to stdout and a log file."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w") as output:
        with subprocess.Popen(args, cwd=cwd, text=True, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT) as process:
            assert process.stdout is not None
            for line in process.stdout:
                sys.stdout.write(line)
                output.write(line)
            return process.wait()


def replace_once(path: Path, pattern: str, replacement: str, what: str) -> None:
    """Patch an upstream file; the anchor must match exactly once. The replacement is literal."""
    if not path.is_file():
        raise OpError(f"{what}: file not found: {path}")
    text = path.read_text()
    count = len(re.findall(pattern, text, flags=re.M))
    if count != 1:
        raise OpError(f"{what}: expected one anchor in {path}, found {count}")
    path.write_text(re.sub(pattern, lambda _match: replacement, text, count=1, flags=re.M))


def write_private(path: Path, text: str) -> None:
    """Write a file that may contain secrets with mode 0600."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(text)
    os.chmod(path, 0o600)


def auto_threads() -> int:
    """CPU count capped by memory; OpenWrt needs roughly 2 GB per job."""
    cpus = os.cpu_count() or 1
    try:
        with open("/proc/meminfo") as meminfo:
            kb = next(int(line.split()[1]) for line in meminfo if line.startswith("MemTotal:"))
    except (OSError, StopIteration, ValueError):
        return max(1, cpus)
    return max(1, min(cpus, kb // 1024 // 1024 // 2))


def format_duration(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 3600}h {seconds % 3600 // 60}m {seconds % 60}s"
