#!/usr/bin/env bash

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && /bin/pwd -P)"
ENV_FILE="$ROOT/.env.nonprod"
OPEN_BROWSER=1

usage() {
  cat <<EOF
Usage: ./start-all.sh [--no-open]

Builds and starts Remymy with Docker Compose: the Data API (with its Redis
cache), the Next.js app and local Langfuse. When the recommender files are
present it also starts the models and mcp services. Opens the app in your
browser once everything answers. Press Ctrl+C to stop and remove the
containers; user accounts and Langfuse data are kept in Docker volumes.

Reads .env.nonprod when it exists. The recommender (models + mcp) starts only
when both of these exist under DATA_DIR (default ./data):
  raw/food-com/RAW_recipes.csv
  models/\$MODEL_FILE              (default hybrid-foodcom-bpr.npz)

Ports come from the environment, then .env.nonprod, then these defaults:
  APP_PORT       app             (default 3000)
  API_PORT       Data API        (default 8002)
  LANGFUSE_PORT  Langfuse        (default 3030)
  MINIO_PORT     Langfuse media  (default 9090)
  MCP_PORT       mcp             (default 8000)
  MODEL_PORT     models          (default 8001)

A port that is already in use is replaced by the next free one.

Example: APP_PORT=3100 ./start-all.sh --no-open
EOF
}

for arg in "$@"; do
  case "$arg" in
    --no-open) OPEN_BROWSER=0 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
done

if [ -t 1 ]; then
  BOLD=$'\033[1m'; RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RESET=$'\033[0m'
else
  BOLD=""; RED=""; GREEN=""; YELLOW=""; RESET=""
fi

info() { printf '%s==>%s %s\n' "$BOLD" "$RESET" "$*"; }
ok() { printf '%s ✓%s %s\n' "$GREEN" "$RESET" "$*"; }
warn() { printf '%s !%s %s\n' "$YELLOW" "$RESET" "$*" >&2; }
fail() { printf '%s ✗%s %s\n' "$RED" "$RESET" "$*" >&2; }

# The value of KEY in .env.nonprod, or nothing. The last assignment wins, as
# it does for Compose.
env_value() {
  [ -f "$ENV_FILE" ] || return 0
  sed -n "s/^$1=//p" "$ENV_FILE" | tail -n 1 | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/"
}

# Environment first, then .env.nonprod, then the default.
setting() {
  local current=${!1} fallback
  [ -n "$current" ] && return
  fallback=$(env_value "$1")
  printf -v "$1" '%s' "${fallback:-$2}"
}

setting APP_PORT 3000
setting API_PORT 8002
setting LANGFUSE_PORT 3030
setting MINIO_PORT 9090
setting MCP_PORT 8000
setting MODEL_PORT 8001
setting DATA_DIR ./data
setting MODEL_FILE hybrid-foodcom-bpr.npz

compose_args=(--project-directory "$ROOT" -f "$ROOT/docker-compose.yml")
[ -f "$ENV_FILE" ] && compose_args+=(--env-file "$ENV_FILE")

compose() {
  docker compose "${compose_args[@]}" "$@"
}

problems=0

if ! command -v docker >/dev/null; then
  fail "Docker is required. Install Docker Desktop: https://docs.docker.com/get-docker/"
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  fail "Docker Compose v2 is required (the \`docker compose\` command)."
  problems=1
fi
if ! docker info >/dev/null 2>&1; then
  fail "Docker is not running. Start Docker Desktop and try again."
  problems=1
fi

if [ "$problems" -ne 0 ]; then
  exit 1
fi

[ -f "$ENV_FILE" ] || warn ".env.nonprod not found; using the defaults in docker-compose.yml."

case "$DATA_DIR" in
  /*) data_path=$DATA_DIR ;;
  *) data_path="$ROOT/${DATA_DIR#./}" ;;
esac

if [ ! -f "$data_path/remymy-food.db" ]; then
  fail "$data_path/remymy-food.db not found. The Data API needs it (see README.md)."
  exit 1
fi

# The recommender needs files that are not in the repository. Without them its
# health check never passes, so it is left out rather than failing the start.
recommender=0
if [ -f "$data_path/raw/food-com/RAW_recipes.csv" ] && [ -f "$data_path/models/$MODEL_FILE" ]; then
  recommender=1
  export COMPOSE_PROFILES=recommender
else
  warn "Recommender files not found, so models and mcp are not started:"
  [ -f "$data_path/raw/food-com/RAW_recipes.csv" ] || printf '     missing %s\n' "$data_path/raw/food-com/RAW_recipes.csv" >&2
  [ -f "$data_path/models/$MODEL_FILE" ] || printf '     missing %s\n' "$data_path/models/$MODEL_FILE" >&2
fi

if [ -n "$(compose ps --status running --quiet 2>/dev/null)" ]; then
  fail "Remymy is already running in Docker."
  printf '   Press Ctrl+C in the terminal running ./start-all.sh, or run: (cd %q && docker compose down)\n' "$ROOT" >&2
  exit 1
fi

# A port that is already taken (by another program or another container) is
# swapped for the next free one, so the stack still starts. Ports already
# picked are skipped too, so two services never land on the same one.
port_busy() {
  if command -v lsof >/dev/null; then
    lsof -nP -iTCP:"$1" -sTCP:LISTEN -t >/dev/null 2>&1
  else
    (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null
  fi
}

taken=" "
pick_port() {
  local var=$1 label=$2 port=${!1} start=${!1}
  while port_busy "$port" || [[ "$taken" == *" $port "* ]]; do
    port=$((port + 1))
    if [ "$port" -gt 65535 ] || [ $((port - start)) -gt 100 ]; then
      fail "No free port found for the $label from $start to $((port - 1))."
      exit 1
    fi
  done
  [ "$port" = "$start" ] || warn "Port $start ($label) is in use, using $port instead."
  printf -v "$var" '%s' "$port"
  taken="$taken$port "
}

pick_port APP_PORT "app"
pick_port API_PORT "Data API"
pick_port LANGFUSE_PORT "Langfuse"
pick_port MINIO_PORT "Langfuse media"
if [ "$recommender" -eq 1 ]; then
  pick_port MCP_PORT "mcp"
  pick_port MODEL_PORT "models"
fi

# Read by docker-compose.yml. Exported values win over .env.nonprod.
export APP_PORT API_PORT LANGFUSE_PORT MINIO_PORT MCP_PORT MODEL_PORT

banner() {
  echo
  info "Remymy is running"
  printf '    App           http://localhost:%s\n' "$APP_PORT"
  printf '    Data API      http://localhost:%s/docs\n' "$API_PORT"
  printf '    Langfuse      http://localhost:%s\n' "$LANGFUSE_PORT"
  if [ "$recommender" -eq 1 ]; then
    printf '    mcp           http://localhost:%s/health\n' "$MCP_PORT"
    printf '    models        http://localhost:%s/health\n' "$MODEL_PORT"
  fi
  printf '    Live logs follow. Press Ctrl+C to stop everything.\n\n'
}

stop_all() {
  echo
  info "Stopping containers…"
  compose down >/dev/null 2>&1 && ok "All containers stopped."
}

interrupted=0
trap 'interrupted=1' INT TERM

info "Starting Remymy in Docker (app :$APP_PORT, Data API :$API_PORT, Langfuse :$LANGFUSE_PORT)"
info "The first run builds the images and takes a few minutes."

if ! compose up --build --detach --wait --wait-timeout 900 --remove-orphans; then
  if [ "$interrupted" -eq 0 ]; then
    echo
    fail "Remymy did not start. The last log lines from each service:"
    compose ps --all
    compose logs --tail 30
  fi
  stop_all
  exit 1
fi
[ "$interrupted" -eq 0 ] || { stop_all; exit 130; }

banner
if [ "$OPEN_BROWSER" -eq 1 ]; then
  if command -v open >/dev/null; then
    open "http://localhost:$APP_PORT"
  elif command -v xdg-open >/dev/null; then
    xdg-open "http://localhost:$APP_PORT" >/dev/null 2>&1
  fi
fi

# `compose logs --follow` quits whenever a container is removed, so it is
# restarted rather than trusted as a sign that the stack has stopped.
follow_logs() {
  compose logs --follow --tail 0 &
  LOGS=$!
}
follow_logs

# api-migrate is a one-shot job that exits on purpose, so it is not watched.
watched="api app redis langfuse-web langfuse-worker langfuse-gateway"
[ "$recommender" -eq 1 ] && watched="$watched mcp models"

failed=0
while [ "$interrupted" -eq 0 ]; do
  # shellcheck disable=SC2086
  stopped=$(compose ps --all --status exited --status dead --format '{{.Service}}' $watched 2>/dev/null | tr '\n' ' ')
  if [ -n "${stopped// /}" ]; then
    fail "Stopped unexpectedly: $stopped. Shutting down the others."
    failed=1
    break
  fi
  kill -0 "$LOGS" 2>/dev/null || follow_logs
  sleep 2
done

kill "$LOGS" 2>/dev/null
wait "$LOGS" 2>/dev/null
stop_all
exit "$failed"
