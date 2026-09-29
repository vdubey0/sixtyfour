#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"
ROOT="$PWD"
PYTHON="${PYTHON:-$ROOT/backend/.venv-agent/bin/python}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"

if [[ ! -x "$PYTHON" || ! -f frontend/node_modules/vite/bin/vite.js ]]; then
  echo "Install dependencies first (see README.md: Run locally)." >&2
  exit 1
fi
command -v node >/dev/null || { echo "Node.js is required." >&2; exit 1; }

pids=()
cleanup() {
  trap - EXIT INT TERM
  if [[ ${#pids[@]} -gt 0 ]]; then
    kill "${pids[@]}" 2>/dev/null || true
    wait "${pids[@]}" 2>/dev/null || true
  fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

"$PYTHON" -m uvicorn backend.main:app --reload --reload-dir "$ROOT/backend" \
  --host 127.0.0.1 --port "$BACKEND_PORT" &
pids+=("$!")
(
  cd "$ROOT/frontend"
  export SIXTYFOUR_API_TARGET="http://127.0.0.1:$BACKEND_PORT"
  exec node node_modules/vite/bin/vite.js --host 127.0.0.1 --port "$FRONTEND_PORT" --strictPort
) &
pids+=("$!")

echo "Frontend: http://127.0.0.1:$FRONTEND_PORT"
echo "Backend:  http://127.0.0.1:$BACKEND_PORT/docs"
echo "Reloads enabled. Press Ctrl+C to stop both servers."

# Compatible with macOS Bash 3.2, which has no wait -n.
while kill -0 "${pids[0]}" 2>/dev/null && kill -0 "${pids[1]}" 2>/dev/null; do
  sleep 1
done
echo "A server stopped; shutting down the other server." >&2
exit 1
