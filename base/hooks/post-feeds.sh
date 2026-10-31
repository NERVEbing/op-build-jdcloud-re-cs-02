#!/usr/bin/env bash
# Break Kconfig recursive dependencies in upstream feeds. The engine runs
# "make RECURSIVE_DEP_IS_ERROR=1 defconfig", so any cycle fails setup.
# Only the package/feeds links are removed; feed sources stay in place.
#   qt5base          PACKAGE_qt5base-gui <-> BUILD_qt5base-gui_OPENGL_OPENGLES2 (video)
#   squeezelite      PACKAGE_squeezelite-custom <-> SQUEEZELITE_WMA_ALAC (packages)
#   luci-app-fwknopd PACKAGE_fwknopd <- luci-app-fwknopd -> FWKNOPD_GPG -> fwknopd (luci)
# Verified against VIKINGYFY/immortalwrt main f9b0ce7 on 2026-10-31.
set -euo pipefail

packages=(qt5base squeezelite luci-app-fwknopd)

for pkg in "${packages[@]}"; do
    if ! compgen -G "package/feeds/*/$pkg" >/dev/null; then
        echo "Warning: $pkg is not installed; its Kconfig workaround may be obsolete" >&2
    fi
done

./scripts/feeds uninstall "${packages[@]}"
