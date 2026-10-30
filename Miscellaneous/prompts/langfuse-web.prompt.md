# Service prompt: langfuse-web

## System prompt

You are configuring Remymy's self-hosted Langfuse v3 web/API service. Reuse the upstream image and its supported configuration; do not reimplement Langfuse or add another observability product.

## System bound

- Own the langfuse-web Compose service and its environment wiring, not upstream application code.
- Serve tracing UI/public APIs internally at http://langfuse-web:3000. Remymy app/API/MCP instrumentation sends traces there.
- Use langfuse-postgres for relational metadata, langfuse-clickhouse for analytics, Redis db 0 for queues/cache, and langfuse-minio for S3 objects.
- Product recipes/users are in the Data API's SQLite file, not this service's PostgreSQL.
- Browser access is through the loopback-only gateway; never expose the pre-seeded automatic-login path publicly. Trace payloads may contain chat/preferences; do not promise they contain no personal data.

## System contract

- Image docker.langfuse.com/langfuse/langfuse:3; restart unless-stopped; network remymy.
- Depend on healthy langfuse-postgres, Redis, langfuse-clickhouse, and successfully completed langfuse-minio-init.
- Memory/swap limits 1280m; NODE_OPTIONS max-old-space-size=960.
- Share worker environment for DATABASE_URL, SALT, ENCRYPTION_KEY, ClickHouse endpoints/credentials, Redis db 0 connection string, S3 bucket/region/credentials/event-media prefixes, and TELEMETRY_ENABLED=false.
- Set NEXTAUTH_URL to the externally reachable local gateway URL and supply NEXTAUTH_SECRET. Preserve a stable encryption key/salt across restarts; do not rotate or invent their values.
- Seed organization id/name remymy/Remymy and project id/name remymy-pi/Remymy Pi Agent. Seed project API keys and initial user's email/name/password via LANGFUSE_INIT_* settings.
- Application LANGFUSE_PUBLIC_KEY/SECRET_KEY values must match the seeded project keys. Gateway local credentials must match the seeded user.
- Shared S3 event endpoint is http://langfuse-minio:9000, bucket langfuse, region auto, force-path-style=true, prefix events/.
- Web media uses public endpoint http://127.0.0.1:MINIO_PORT (default 9090) and internal endpoint http://langfuse-minio:9000, prefix media/. This is intentionally local-browser configuration.
- No direct host port publication. The gateway proxies port 3000.
- Healthcheck wget to http://$(hostname):3000/api/public/health; interval 15s, timeout 5s, start period 90s, retries 12.
- HTTP schemas, auth internals, migrations, and UI behavior remain upstream Langfuse contracts. Do not fabricate replacements or assume a floating major tag is immutable.
- No custom request handler or dedicated local web-service tests exist in this repo; verify configuration plus actual upstream health when available.

## Build prompt

Recreate the upstream Langfuse web service and shared environment alias with its worker. Wire consistent database, queue, ClickHouse, S3, seeded project, and gateway settings. Keep all credentials environment-driven, local exposure unchanged, and resource/health dependencies intact. Do not rebuild the Langfuse UI.

## Acceptance checks

- Validate Compose wiring and dependencies without printing resolved secrets.
- When the stack is available, verify internal health, local gateway UI access, and an application trace arriving in the seeded project.
- Check browser media uses the local public endpoint while server-side uploads use service DNS.
- Report actual checks, upstream/image prerequisites, and local-only security limits; config validation alone is not an ingestion test.

## Source anchors

- `SystemCode/remymy/docker-compose.yml`: langfuse-web and langfuse-worker shared environment
- `SystemCode/remymy/app/lib/server/langfuse.ts`, `app/instrumentation.ts`
- `SystemCode/remymy/mcp/eval/telemetry.py`
