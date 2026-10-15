#!/bin/sh
# hooks/lib/hostid.sh: canonical machine identity for the harness.
#
#   host_id    : stable, hardware-derived short id (from IOPlatformUUID). Migration
#                Assistant copies ComputerName + files but NOT the hardware UUID, so a
#                CLONE gets a different id, ownership can't be inherited by copying a file.
#   host_label : "<ComputerName> (<hw.model>)" for display.
#
# Source it (`. hostid.sh`) to get the functions, or run it as a CLI: hostid.sh id|label

host_uuid() {
  ioreg -rd1 -c IOPlatformExpertDevice 2>/dev/null | awk -F'"' '/IOPlatformUUID/{print $4; exit}'
}

host_id() {
  uuid=$(host_uuid)
  [ -n "$uuid" ] || uuid=$(hostname 2>/dev/null)
  printf '%s' "$uuid" | tr 'A-Z' 'a-z' | tr -cd 'a-f0-9' | cut -c1-12
}

host_label() {
  cn=$(scutil --get ComputerName 2>/dev/null || hostname -s 2>/dev/null || echo Mac)
  model=$(sysctl -n hw.model 2>/dev/null || echo Mac)
  printf '%s (%s)' "$cn" "$model"
}

# CLI dispatch only when executed directly (not when sourced).
case "${0##*/}" in
  hostid.sh)
    case "${1:-label}" in
      id)    host_id; echo ;;
      uuid)  host_uuid; echo ;;
      label) host_label; echo ;;
      *)     echo "usage: hostid.sh id|uuid|label" >&2; exit 2 ;;
    esac
    ;;
esac
