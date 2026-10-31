#!/bin/sh
# Shared helpers for op-build uci-defaults scripts (POSIX sh, sourced).
# uci-defaults run on the first boot after every flash, including upgrades
# that keep settings, so each script rebuilds what it owns.

OP_PARAMS=/etc/op-build

# op_param <config.section.option>: a profile parameter rendered at build time.
# "uci -c" reads it directly, so UCI_CONFIG_DIR never leaks into later calls.
op_param() {
  uci -q -c "$OP_PARAMS" get "$1"
}

# op_param_sections <config> <type>: parameter section names of a type.
op_param_sections() {
  uci -q -c "$OP_PARAMS" -X show "$1" | sed -n "s/^$1\.\([^.=]*\)=$2\$/\1/p"
}

# op_sections <config> <type>: /etc/config section names of a type.
op_sections() {
  uci -q -X show "$1" | sed -n "s/^$1\.\([^.=]*\)=$2\$/\1/p"
}

op_set() {
  uci set "$1" || exit 1
}

op_fail() {
  echo "$OP_TAG: $*" >&2
  logger -t "$OP_TAG" "$*"
  exit 1
}

# op_begin <config...>: snapshot the configs and op-owned nftables rules. Any
# exit before op_commit restores them, and the script stays for the next boot.
op_begin() {
  OP_CONFIGS="$*"
  OP_DONE=0
  umask 077
  OP_BACKUP=$(mktemp -d /tmp/op-build.XXXXXX) || exit 1
  mkdir "$OP_BACKUP/nftables" || exit 1
  for config in $OP_CONFIGS; do
    # Keep changes committed by earlier defaults in the snapshot.
    uci commit "$config" || exit 1
    cp -p "/etc/config/$config" "$OP_BACKUP/$config" || exit 1
  done
  for rule in /etc/nftables.d/30-op-*.nft; do
    [ -e "$rule" ] || continue
    cp -p "$rule" "$OP_BACKUP/nftables/" || exit 1
  done
  trap op_finish EXIT
  trap 'exit 1' HUP INT TERM
}

# Invoked by the EXIT trap set in op_begin.
# shellcheck disable=SC2329
op_finish() {
  status=$?
  trap - EXIT HUP INT TERM
  if [ "$OP_DONE" != 1 ]; then
    restored=1
    for config in $OP_CONFIGS; do
      uci -q revert "$config"
      cp -p "$OP_BACKUP/$config" "/etc/config/$config" || restored=0
    done
    rm -f /etc/nftables.d/30-op-*.nft
    for rule in "$OP_BACKUP"/nftables/*.nft; do
      [ -e "$rule" ] || continue
      cp -p "$rule" /etc/nftables.d/ || restored=0
    done
    if [ "$restored" != 1 ]; then
      echo "$OP_TAG: failed; restore incomplete, backup kept in $OP_BACKUP" >&2
      exit 1
    fi
    echo "$OP_TAG: failed; previous configuration restored" >&2
    [ "$status" != 0 ] || status=1
  fi
  rm -rf "$OP_BACKUP"
  exit "$status"
}

op_commit() {
  for config in $OP_CONFIGS; do
    uci commit "$config" || exit 1
  done
  OP_DONE=1
}
