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

Check the resolved environment and Compose configuration without starting containers:

```bash
docker compose --env-file .env.nonprod config
```

On startup, `api-migrate` runs once before the API. It validates an existing
`remymy-food.db` and leaves it unchanged. If the database is missing, it
requires both `RECIPES_SOURCE` and `FDC_SOURCE_DIR` to be explicitly set to
source paths visible inside the writable `/data` mount; only then does it run
ingestion. The API itself mounts the data directory read-only.

Published local ports:

| Service | URL |
| --- | --- |
| App | http://localhost:3000 |
| Langfuse | http://localhost:3030 |
| Data API | http://localhost:8002 |
| MCP | http://localhost:8000 |
| Models | http://localhost:8001 |

Langfuse is the tracing UI for connected Pi agent chat (`/api/chat`) and the MCP hello probe. Sign in with `remymy@example.com` / `remymylocal` on the local stack. Demo-mode Discover replies stay in the browser and do not create traces.

Inside the Compose network, services should use `api`, `mcp`, `models`, and `langfuse-web` as hostnames instead of `localhost`. The API reads the normalized SQLite database at `${DATA_DIR:-./data}/remymy-food.db` through a read-only mount; only its documented REST responses are exposed.
