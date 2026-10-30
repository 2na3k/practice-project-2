# Service prompt: redis

## System prompt

You are configuring Remymy's shared Redis dependency. Reuse the upstream image, preserve logical database separation, and prioritize queue safety over cache eviction.

## System bound

- Own only Redis Compose configuration and its persistent volume.
- Redis db 0 belongs to Langfuse queues/cache; db 1 belongs to the Data API cache.
- Product users/recipes/history remain authoritative in SQLite. Do not move them into Redis or treat a logical database as a security boundary.
- No Redis port is published to the host. Never flush all databases or delete the shared volume to fix a cache issue.

## System contract

- Image redis:7-alpine; restart unless-stopped; network remymy; volume redis-data:/data.
- Run redis-server with requirepass from REDIS_AUTH, maxmemory 512mb, maxmemory-policy noeviction.
- Container memory and swap limits are 640m. Healthcheck: redis-cli ping using REDISCLI_AUTH; interval 5s, timeout 3s, retries 10.
- API URL selects /1; Langfuse REDIS_CONNECTION_STRING selects /0. Use consistent credentials through environment variables; do not reproduce development password defaults in prompts.
- Under memory pressure, writes can fail instead of evicting queue keys. API cache operations must degrade to SQLite/recomputation.
- Do not silently switch to an LRU/allkeys eviction policy, since that can remove Langfuse jobs.
- A mounted /data volume does not imply explicitly configured AOF or exactly-once/durable queue delivery; preserve upstream persistence behavior and do not overclaim it.
- API startup waits for Redis to start; Langfuse waits for it to be healthy. API runtime remains best-effort when Redis is down.

## Build prompt

Recreate the shared authenticated Redis service with the two logical database clients, noeviction, bounded memory, named volume, and healthcheck. Keep product-cache fallback in the API rather than adding a second Redis deployment.

## Acceptance checks

- Validate resolved Compose configuration without printing credentials.
- In a disposable stack, verify authenticated PING, rejection of unauthenticated access, db 0/db 1 key separation, and API operation with Redis unavailable.
- If memory-pressure checks are run, use disposable data only. Report checks and persistence limits.
