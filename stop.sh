#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_DIR="${JALEBI_DATA_DIR:-$HOME/.jalebi}"
PID_FILE="$RUN_DIR/server.pid"

if [[ ! -f "$PID_FILE" ]]; then
  echo "No PID file found at $PID_FILE; server is not running (or was not started via start.sh)."
  exit 0
fi

PID="$(cat "$PID_FILE")"

if ! kill -0 "$PID" 2>/dev/null; then
  echo "Process $PID is not running; removing stale PID file."
  rm -f "$PID_FILE"
  exit 0
fi

echo "Stopping Jalebi server (PID $PID)..."
kill -TERM "$PID"

for _ in $(seq 1 20); do
  if ! kill -0 "$PID" 2>/dev/null; then
    break
  fi
  sleep 0.25
done

if kill -0 "$PID" 2>/dev/null; then
  echo "Process did not exit after SIGTERM; sending SIGKILL." >&2
  kill -KILL "$PID"
fi

rm -f "$PID_FILE"
echo "Jalebi server stopped."
