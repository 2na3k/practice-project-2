# Service prompt: langfuse-postgres

## System prompt

You are configuring Remymy's upstream PostgreSQL dependency for Langfuse. Keep it private, persistent, and scoped to observability metadata. Do not redesign the product database.

## System bound

- Own only the langfuse-postgres Compose service and langfuse-postgres-data volume.
- Langfuse web/worker own their schemas and migrations. Application recipes/users/history remain in SQLite.
- Do not publish PostgreSQL to the host or add direct browser/app access.
- Never delete the volume, reset schemas, or rotate credentials as an incidental startup fix.

## System contract

- Image postgres:16; restart unless-stopped; network remymy.
- POSTGRES_USER=postgres, POSTGRES_DB=postgres; POSTGRES_PASSWORD supplied through LANGFUSE_POSTGRES_PASSWORD.
- Set TZ=UTC and PGTZ=UTC.
- Named volume langfuse-postgres-data mounts at /var/lib/postgresql/data.
- Memory/swap limits 256m.
- Healthcheck pg_isready -U postgres; interval 3s, timeout 5s, retries 10.
- Langfuse DATABASE_URL points to langfuse-postgres:5432/postgres with matching credentials.
- Health gates both web and worker. pg_isready is server-readiness, not proof of successful application migrations or correct trace ingestion.
- Existing volume initialization follows upstream behavior; changing POSTGRES_PASSWORD does not automatically migrate an existing database user's password.
- No custom SQL schema or migrations for this service are defined in this repository.

## Build prompt

Recreate the private upstream PostgreSQL service with the named volume, UTC settings, credentials, limits, and readiness check. Wire Langfuse clients to it. Do not add product tables or copy a developer password into documentation.

## Acceptance checks

- Validate no host port, correct mount/credentials wiring, resource limits, and dependency conditions.
- If live checks are permitted, verify pg_isready and a Langfuse connection, then data survival across container recreation without removing the volume.
- Report checks actually run and any database-version/backup prerequisites. Do not perform destructive persistence tests on live data.

## Source anchors

- `SystemCode/remymy/docker-compose.yml`: langfuse-postgres and Langfuse DATABASE_URL
