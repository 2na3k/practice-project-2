# Service prompt: langfuse-worker

## System prompt

You are configuring Remymy's Langfuse v3 background worker using the upstream image. Preserve the shared tracing infrastructure and queue semantics; do not create a custom worker implementation.

## System bound

- Own only langfuse-worker Compose configuration and the shared environment definition reused by langfuse-web.
- This processes Langfuse tracing jobs, not recipe recommendations, model training, or product-user state.
- Its storage dependencies are PostgreSQL, ClickHouse, Redis db 0, and the S3 bucket.
- No published port, application route, or custom healthcheck is configured. Do not invent one as proof that queued data is processed.

## System contract

- Image docker.langfuse.com/langfuse/langfuse-worker:3; restart unless-stopped; network remymy.
- Depend on healthy langfuse-postgres, Redis, langfuse-clickhouse, and completed langfuse-minio-init.
- Memory/swap limits 1g; NODE_OPTIONS max-old-space-size=768.
- DATABASE_URL targets postgres database on langfuse-postgres:5432 using environment-supplied credentials.
- Configure NEXTAUTH_URL, SALT, ENCRYPTION_KEY, TELEMETRY_ENABLED=false. Keep key/salt consistent with web across restarts.
- ClickHouse migration URL clickhouse://langfuse-clickhouse:9000; HTTP URL http://langfuse-clickhouse:8123; configured user/password; CLICKHOUSE_CLUSTER_ENABLED=false.
- REDIS_CONNECTION_STRING targets authenticated redis:6379/0, not the Data API's /1.
- S3 event/media bucket langfuse, region auto, endpoint http://langfuse-minio:9000, force-path-style=true, prefixes events/ and media/, consistent access/secret keys.
- The web service inherits these settings, then overrides its browser-facing media endpoint and memory options. Avoid drifting the two configurations.
- Job payloads, retry behavior, ingestion schemas, and internal persistence are upstream responsibilities. Do not claim exactly-once processing from this Compose definition.
- Preserve Redis noeviction; eviction of queue keys is not an acceptable API-cache optimization.

## Build prompt

Recreate the worker with the shared Langfuse environment, dependency gates, internal endpoints, and bounded resources. Reuse official upstream behavior. Keep it separate from the product API and from one-shot initialization jobs.

## Acceptance checks

- Validate shared environment inheritance, db 0 selection, internal S3 endpoint, dependency conditions, and memory limits.
- In a disposable or existing approved stack, submit a trace via the app, check worker errors/logs, and confirm trace availability in Langfuse.
- Report checks actually run; a running process or completed bucket job alone does not prove ingestion.

## Source anchors

- `SystemCode/remymy/docker-compose.yml`: langfuse-worker, langfuse-web, redis
