# Service prompt: langfuse-clickhouse

## System prompt

You are configuring Remymy's ClickHouse dependency for self-hosted Langfuse analytics. Reuse the upstream image and let Langfuse manage its analytical schema.

## System bound

- Own only ClickHouse Compose configuration and its data/log volumes.
- Langfuse web/worker use its internal HTTP/native endpoints. This is not the recipe database or the Food.com model artifact store.
- Do not expose analytics ports to the host, add custom product tables, or delete persisted data for troubleshooting.

## System contract

- Image clickhouse/clickhouse-server:24.8-alpine; restart unless-stopped; network remymy.
- CLICKHOUSE_DB=default; CLICKHOUSE_USER/PASSWORD supplied through LANGFUSE_CLICKHOUSE_USER/PASSWORD.
- Named volumes langfuse-clickhouse-data:/var/lib/clickhouse and langfuse-clickhouse-logs:/var/log/clickhouse-server.
- Memory/swap limits 1536m.
- Healthcheck wget --no-verbose --tries=1 --spider http://127.0.0.1:8123/ping; interval 5s, timeout 5s, start period 10s, retries 10.
- Langfuse HTTP endpoint http://langfuse-clickhouse:8123; migration endpoint clickhouse://langfuse-clickhouse:9000; cluster mode disabled.
- Both Langfuse services wait for healthy ClickHouse. The ping endpoint is process readiness, not proof of migrations, authenticated query access, or persisted traces.
- Preserve internal endpoint/credential consistency. Upstream migrations/schema remain outside custom code in this repository.

## Build prompt

Recreate the private ClickHouse service, persistent data and log volumes, resource budget, health probe, and matching Langfuse environment. Do not replace it with PostgreSQL or write a bespoke analytics API.

## Acceptance checks

- Validate image, mounts, resource limits, endpoints, cluster-disabled setting, and no published host ports.
- If running the stack, verify ping plus Langfuse's authenticated connection/migrations and a stored trace.
- Report checks actually run. Test restart persistence only with approved/non-destructive data handling.

## Source anchors

- `SystemCode/remymy/docker-compose.yml`: langfuse-clickhouse and shared Langfuse environment
