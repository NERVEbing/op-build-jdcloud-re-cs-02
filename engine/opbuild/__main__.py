"""Command line: python3 -m opbuild <action> [profile] [threads]."""

from __future__ import annotations

from pathlib import Path
import sys

from . import build, config
from .common import OpError, auto_threads, error, warn

ROOT = Path(__file__).resolve().parents[2]
USAGE = """Usage: python3 -m opbuild <setup|compile|build|info> <profile> [threads]
       python3 -m opbuild <clean|distclean>"""


def main(argv: list[str]) -> int:
    if not argv:
        print(USAGE, file=sys.stderr)
        return 2
    action, args = argv[0], argv[1:]
    ws = build.Workspace(ROOT)
    try:
        if action in ("clean", "distclean"):
            if args:
                print(USAGE, file=sys.stderr)
                return 2
            (build.clean if action == "clean" else build.distclean)(ws)
            return 0
        if action not in ("setup", "compile", "build", "info") or not 1 <= len(args) <= 2:
            print(USAGE, file=sys.stderr)
            return 2
        threads = auto_threads()
        if len(args) == 2:
            if not args[1].isdigit() or int(args[1]) < 1:
                raise OpError("threads must be a positive integer")
            threads = int(args[1])
        resolved = config.resolve(ROOT, args[0])
        for message in resolved.warnings:
            warn(message)
        if action == "info":
            build.print_info(resolved, threads)
        if action in ("setup", "build"):
            build.setup(resolved, ws)
        if action in ("compile", "build"):
            build.compile_firmware(resolved, ws, threads)
    except OpError as failure:
        error(str(failure))
        return 1
    except KeyboardInterrupt:
        error("interrupted")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
