# Remymy

Remymy is composed of the `app`, `mcp`, and `models` services, plus a PostgreSQL database. The full local stack is defined in [`docker-compose.yml`](./docker-compose.yml).

## Environment setup

Use `.env.nonprod` for the non-production environment. It should contain at least:

```env
ENV=nonprod
APP_PORT=3000
API_PORT=8002
DATA_DIR=./data
# Optional: set both to enable one-shot source ingestion into DATA_DIR.
RECIPES_SOURCE=
FDC_SOURCE_DIR=
FDC_RELEASE=unspecified
MCP_PORT=8000
MODEL_PORT=8001
MODEL_FILE=hybrid-foodcom-bpr.npz
POSTGRES_PORT=5432
```

The `.env.nonprod` file is ignored by Git, so keep passwords and other environment-specific values there. Compose reads this file for both variable substitution and the `ENV=nonprod` value passed into the application services.

## Run the stack

From the repository root:

```bash
docker compose \
  --env-file SystemCode/remymy/.env.nonprod \
  -f SystemCode/remymy/docker-compose.yml \
  up --build
```

Or from this directory:

```bash
docker compose --env-file .env.nonprod up --build
```

Shortcut: `./start-all.sh` does the build and start, moves any port that is
already in use to the next free one, opens the app when everything is healthy,
and follows the logs. Press Ctrl+C to stop it. It starts `models` and `mcp`
(the `recommender` profile) only when `data/raw/food-com/RAW_recipes.csv` and
`data/models/$MODEL_FILE` exist, since neither is in the repository. To start
them by hand, add `--profile recommender` to the commands above.

Check the resolved environment and Compose configuration without starting containers:

```bash
docker compose --env-file .env.nonprod config
```

On startup, `api-migrate` runs once before the API. It makes sure `remymy.db`,
the API's one database on the `remymy-user-data` volume, holds the recipes. One
that holds them is left as it is. The first time, it merges in the recipes of
`data/remymy-food.db` when that file is there. Otherwise it requires both
`RECIPES_SOURCE` and `FDC_SOURCE_DIR` to be explicitly set to source paths
visible inside the `/data` mount; only then does it run ingestion. Both
services mount the data directory read-only.

Each service built here (`app`, `api-migrate`, `api`, `mcp`, `models`,
`langfuse-gateway`) also keeps what it prints in a log file per hour (UTC), at
`logs/<service>/YYYY-MM-DD_HH.log`: a folder per service, a file per hour. Their entrypoint,
`logging/hourly-log.sh`, copies the process's stdout and stderr there, and
`docker compose logs` shows the same lines as before. Files older than
`LOG_RETENTION_HOURS` (default 168, a week; `0` keeps them all) are deleted. Set
`LOG_DIR_HOST` to keep them somewhere other than `./logs`.

Published local ports:

| Service | URL |
| --- | --- |
| App | http://localhost:3000 |
| Langfuse | http://localhost:3030 |
| Data API | http://localhost:8002 |
| MCP | http://localhost:8000 |
| Models | http://localhost:8001 |

Langfuse is the tracing UI for connected Pi agent chat (`/api/chat`) and MCP calls. Connected chat meal recommendations go through the Python MCP service, which filters the mounted Food.com recipe dataset and ranks candidates with the model service; it does not use any local meal list. The local-only gateway on port 3030 automatically establishes the pre-seeded development session, so the dashboard opens without an email/password screen. The port remains bound to `127.0.0.1`; do not expose this passwordless gateway outside a development machine.

Each MCP invocation creates a nested `mcp.client.call` tool observation. It
records the MCP tool name and arguments, transport endpoint and duration, plus
the exact returned MCP `content`, optional `structuredContent`, and `isError`
flag. Standalone `/api/mcp` checks use the trace name `remymy-mcp-probe`; agent
calls keep the MCP observation under their `remymy-agent-chat` trace.

Connected chat requests stream newline-delimited JSON when the browser sends
`Accept: application/x-ndjson`. The stream reports the real Pi agent lifecycle
(request interpretation, model turns, catalog/MCP tool calls, and answer
composition) before the final result, which powers the stage-by-stage flow in
the chat UI. Plain JSON remains available for clients that omit that header.

Inside the Compose network, services should use `api`, `mcp`, `models`, and `langfuse-web` as hostnames instead of `localhost`. The API keeps everything (recipes, catalog, users, their history and saved recipes) in one SQLite database, `remymy.db` on the `remymy-user-data` volume; only its documented REST responses are exposed.
