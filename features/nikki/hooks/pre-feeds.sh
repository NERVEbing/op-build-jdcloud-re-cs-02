#!/usr/bin/env bash
# Avoid reciprocal variant Kconfig cycles while preserving package conflicts.
set -euo pipefail

# Keep reciprocal variant conflicts in the default-provider direction to avoid
# Kconfig cycles such as mihomo-alpha <-> mihomo-meta. Package metadata is unchanged.
python3 - <<'PY'
from pathlib import Path

path = Path("scripts/package-metadata.pl")
source = path.read_text()
anchor = "\t\tnext unless $package{$depend};\n"
replacement = anchor + (
    "\t\t# Skip reverse conflicts against the default provider in Kconfig.\n"
    "\t\tnext if $package{$depend}{variant_default} &&\n"
    "\t\t\tgrep { $_ eq $pkgname } @{$package{$depend}{conflicts} || []};\n"
)
if replacement not in source:
    if source.count(anchor) != 1:
        raise SystemExit("Unexpected package-metadata.pl conflict handling; patch not applied")
    path.write_text(source.replace(anchor, replacement, 1))
print("Applied reciprocal variant Kconfig conflict fix")
PY
