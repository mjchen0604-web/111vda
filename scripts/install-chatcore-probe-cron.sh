#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

CRON_FILE="${CHATCORE_PROBE_CRON_FILE:-/etc/cron.d/111vda-chatcore-probe}"
PROBE_SCRIPT="${CHATCORE_PROBE_SCRIPT:-$REPO_ROOT/scripts/chatcore-probe-auths.sh}"
CHAT_HOST="${CHATCORE_INTERNAL_CHAT_HOST:-127.0.0.1}"
CHAT_PORT="${CHATCORE_INTERNAL_CHAT_PORT:-1455}"
PROBE_LOG="${CHATCORE_PROBE_LOG_FILE:-/var/log/111vda-chatcore-probe.log}"
SCHEDULE="${CHATCORE_PROBE_SCHEDULE:-*/20 2-6 * * *}"
SWEEP_AFTER_PROBE="${CHATCORE_PROBE_SWEEP_AFTER_PROBE:-0}"

if [[ ! -f "$PROBE_SCRIPT" ]]; then
  echo "probe script not found: $PROBE_SCRIPT" >&2
  exit 1
fi

install -m 0755 "$PROBE_SCRIPT" "$PROBE_SCRIPT"
touch "$PROBE_LOG"
chmod 0644 "$PROBE_LOG"

cat >"$CRON_FILE" <<EOF
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# Low-traffic probe window. Adjust CHATCORE_PROBE_SCHEDULE if needed.
CHATCORE_INTERNAL_CHAT_HOST=$CHAT_HOST
CHATCORE_INTERNAL_CHAT_PORT=$CHAT_PORT
CHATCORE_PROBE_LOG_FILE=$PROBE_LOG
CHATCORE_PROBE_SWEEP_AFTER_PROBE=$SWEEP_AFTER_PROBE
$SCHEDULE root $PROBE_SCRIPT
EOF

chmod 0644 "$CRON_FILE"
echo "installed cron file: $CRON_FILE"
echo "schedule: $SCHEDULE"
echo "probe script: $PROBE_SCRIPT"
echo "log file: $PROBE_LOG"
