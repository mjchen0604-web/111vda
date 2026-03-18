#!/usr/bin/env bash
set -euo pipefail

CHAT_HOST="${CHATCORE_INTERNAL_CHAT_HOST:-127.0.0.1}"
CHAT_PORT="${CHATCORE_INTERNAL_CHAT_PORT:-1455}"
LOCK_FILE="${CHATCORE_PROBE_LOCK_FILE:-/tmp/111vda-chatcore-probe.lock}"
LOG_FILE="${CHATCORE_PROBE_LOG_FILE:-/var/log/111vda-chatcore-probe.log}"
CONNECT_TIMEOUT="${CHATCORE_PROBE_CONNECT_TIMEOUT:-5}"
MAX_TIME="${CHATCORE_PROBE_MAX_TIME:-180}"
SWEEP_AFTER_PROBE="${CHATCORE_PROBE_SWEEP_AFTER_PROBE:-0}"

mkdir -p "$(dirname "$LOCK_FILE")"
mkdir -p "$(dirname "$LOG_FILE")"

timestamp() {
  date '+%Y-%m-%d %H:%M:%S'
}

log() {
  echo "[$(timestamp)] $*" | tee -a "$LOG_FILE"
}

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  log "probe skipped: another probe job is still running"
  exit 0
fi

probe_url="http://${CHAT_HOST}:${CHAT_PORT}/api/actions/probe_auths"
sweep_url="http://${CHAT_HOST}:${CHAT_PORT}/api/actions/sweep_invalid_auths"

log "probe start -> ${probe_url}"
probe_output="$(
  curl -fsS \
    --connect-timeout "$CONNECT_TIMEOUT" \
    --max-time "$MAX_TIME" \
    -X POST \
    "$probe_url"
)"
log "probe result -> ${probe_output}"

if [[ "$SWEEP_AFTER_PROBE" == "1" || "$SWEEP_AFTER_PROBE" == "true" ]]; then
  log "sweep start -> ${sweep_url}"
  sweep_output="$(
    curl -fsS \
      --connect-timeout "$CONNECT_TIMEOUT" \
      --max-time "$MAX_TIME" \
      -X POST \
      "$sweep_url"
  )"
  log "sweep result -> ${sweep_output}"
fi

