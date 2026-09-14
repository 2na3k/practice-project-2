# Remymy

Remymy is composed of the `app`, `mcp`, and `models` services, plus a PostgreSQL database. The full local stack is defined in [`docker-compose.yml`](./docker-compose.yml).

## Environment setup

Use `.env.nonprod` for the non-production environment. It should contain at least:

```env
ENV=nonprod
APP_PORT=3000
MCP_PORT=8000
MODEL_PORT=8001
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

Published local ports:

| Service | URL |
| --- | --- |
| App | http://localhost:3000 |
| MCP | http://localhost:8000 |
| Models | http://localhost:8001 |
| PostgreSQL | localhost:5432 |

Inside the Compose network, services should use `mcp`, `models`, and `database` as hostnames instead of `localhost`.
