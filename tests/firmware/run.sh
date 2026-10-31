#!/usr/bin/env bash
# Run the firmware uci-defaults scripts against real uci in a throwaway
# ImmortalWrt rootfs container. Requires Docker on an x86_64 Linux host.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DOCKER="${OP_DOCKER:-docker}"
# immortalwrt/rootfs:x86-64-master as of 2026-10-20.
IMAGE="${OP_ROOTFS_IMAGE:-immortalwrt/rootfs@sha256:b26a2f99a537a81cc712ea71bb51f8a2bf2acb00257330eb75870278eca16e7b}"

exec "$DOCKER" run --rm --network none -v "$ROOT:/repo:ro" --entrypoint /bin/sh "$IMAGE" \
    /repo/tests/firmware/cases.sh
