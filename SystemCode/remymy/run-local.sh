#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -z "${PYTHON:-}" ]; then
  if [ -x "$ROOT/api/.venv/bin/python3" ]; then
    PYTHON="$ROOT/api/.venv/bin/python3"
  else
    PYTHON=python3
  fi
fi

API_PID=""

cleanup() {
  if [ -n "$API_PID" ]; then
    kill "$API_PID" 2>/dev/null || true
  fi
  wait 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 130' INT TERM

prefix() {
  awk -v tag="$1" '{ print "[" tag "] " $0; fflush() }'
}

wait_for() {
  local name=$1 url=$2 pid=$3 seconds=$4
  for _ in $(seq "$seconds"); do
    if curl -sf -m 2 "$url" >/dev/null; then
      echo "$name is up: $url"
      return 0
    fi
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "$name exited before it was ready" >&2
      return 1
    fi
    sleep 1
  done
  echo "$name was not ready after ${seconds}s" >&2
  return 1
}

RECIPE_DB="${DATABASE_PATH:-$ROOT/data/remymy-food.db}"
if [ ! -e "$RECIPE_DB" ]; then
  echo "warning: $RECIPE_DB not found; recipe routes return 503 and no demo user is created" >&2
fi

CATALOG_CSV="${CATALOG_PATH:-$ROOT/data/price_mapped_nutrients.csv}"
CATALOG_DB="${CATALOG_DATABASE_PATH:-$ROOT/api/remymy-catalog.db}"
if [ ! -e "$CATALOG_CSV" ] && [ ! -e "$CATALOG_DB" ]; then
  echo "warning: neither $CATALOG_CSV nor $CATALOG_DB found; recommendations return 503" >&2
fi

REDIS="${REDIS_URL:-redis://localhost:6379/0}"
if ! "$PYTHON" -c "import redis, sys; redis.Redis.from_url(sys.argv[1], socket_connect_timeout=2).ping()" "$REDIS" 2>/dev/null; then
  echo "warning: Redis is not reachable at $REDIS; the API runs without its cache" >&2
fi

cd "$ROOT/api"
"$PYTHON" serve.py > >(prefix api) 2>&1 &
API_PID=$!
# startup builds the catalog database, loads the model and builds the HNSW index before the API answers
wait_for api http://localhost:8002/health "$API_PID" 180

echo "Swagger: http://localhost:8002/docs. Ctrl+C stops it."
wait "$API_PID"
