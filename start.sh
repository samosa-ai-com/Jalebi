#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_DIR="$ROOT/.jalebi"
PID_FILE="$RUN_DIR/server.pid"
LOG_FILE="$RUN_DIR/server.log"
VENV_PYTHON="$ROOT/apps/server/.venv/bin/python"

HOST="${JALEBI_HOST:-127.0.0.1}"
PORT="${JALEBI_PORT:-3456}"
URL="http://${HOST}:${PORT}/api/health"

if [[ ! -x "$VENV_PYTHON" ]]; then
  echo "Python venv not found at $VENV_PYTHON" >&2
  echo "Run 'uv sync' inside apps/server first." >&2
  exit 1
fi

mkdir -p "$RUN_DIR"

if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "Jalebi server already running (PID $(cat "$PID_FILE"))."
  exit 0
fi

rm -f "$PID_FILE"
nohup "$VENV_PYTHON" -m jalebi.app >> "$LOG_FILE" 2>&1 &
PID=$!
echo "$PID" > "$PID_FILE"
echo "Jalebi server started with PID $PID"
echo "  PID file: $PID_FILE"
echo "  Log file: $LOG_FILE"

for _ in $(seq 1 20); do
  if curl -sf "$URL" >/dev/null 2>&1; then
    echo "Healthy at $URL"
    exit 0
  fi
  sleep 0.5
done

echo "Server did not become healthy in time; check $LOG_FILE" >&2
exit 1
