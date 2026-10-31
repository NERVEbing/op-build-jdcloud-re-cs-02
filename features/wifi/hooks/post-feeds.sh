#!/usr/bin/env bash
# With wifi.quiet_log, start hostapd with -q so AP-STA events follow the
# per-radio log_level instead of the global verbosity.
set -euo pipefail

python3 - <<'PY'
import json
import os
from pathlib import Path

if not json.loads(Path(os.environ["OP_RESOLVED"]).read_text())["params"]["wifi"]["quiet_log"]:
    raise SystemExit(0)
init = Path("package/network/services/hostapd/files/wpad.init")
anchor = "procd_set_param command /usr/sbin/hostapd -s -g /var/run/hostapd/global"
patched = anchor.replace(" -s -g ", " -s -q -g ")
text = init.read_text()
if text.count(patched) == 1 and anchor not in text:
    print("hostapd already starts with -q")
    raise SystemExit(0)
if text.count(anchor) != 1:
    raise SystemExit(f"Expected one hostapd start command in {init}, found {text.count(anchor)}")
init.write_text(text.replace(anchor, patched))
print("hostapd starts with -q")
PY
