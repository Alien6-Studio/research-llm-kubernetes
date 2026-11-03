#!/usr/bin/env bash
# Run on the explicitly selected LXD host. Existing instances/profiles are unchanged.
set -euo pipefail
INSTANCE=a6-capacity-v2
if [ "$(lxc list "local:${INSTANCE}" --format json)" != '[]' ]; then
  echo 'The research instance already exists; inspect it before proceeding.' >&2
  exit 1
fi
# LXD reads redirected stdin as configuration; never let it consume this script.
lxc launch ubuntu:24.04 "local:${INSTANCE}" --vm \
  -c limits.cpu=8 -c limits.memory=16GiB -c boot.autostart=false \
  -d root,size=40GiB </dev/null
for attempt in $(seq 1 90); do
  if lxc exec "local:${INSTANCE}" -- true </dev/null 2>/dev/null; then
    echo 'The isolated VM agent is ready.'
    exit 0
  fi
  sleep 2
done
echo 'VM agent did not become ready; the created instance is retained for diagnosis.' >&2
exit 1
