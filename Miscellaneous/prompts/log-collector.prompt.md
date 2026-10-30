# Service prompt: log-collector

## System prompt

You are reconstructing Remymy's central Docker log archiver using only Python's standard library. Preserve container stdout/stderr and per-service hourly storage without changing the application services.

## System bound

- Own `SystemCode/remymy/logging/collect.py` and the log-collector Compose configuration.
- Read Docker Engine API through its Unix socket. Write only archived logs under LOG_DIR.
- Do not mutate containers, read application databases, inject per-container logging wrappers, or forward logs to an external provider.
- Docker socket access is privileged: a read-only bind does not make the Docker API permission-limited. Keep this a trusted local operator component.

## System contract

- Run python:3.12-alpine with `python -u /opt/remymy-logging/collect.py`, restart unless-stopped, network remymy.
- Read-only mounts: collector script and /var/run/docker.sock. Writable mount: LOG_DIR_HOST (default ./logs) → /var/log/remymy.
- Environment: COMPOSE_PROJECT default remymy; LOG_DIR default /var/log/remymy; LOG_RETENTION_HOURS default 168; DOCKER_SOCKET default /var/run/docker.sock.
- Poll every second for all containers labeled com.docker.compose.project=<project>, including exited one-shot containers. Derive service names from Compose labels and skip log-collector itself.
- Inspect TTY status and read stdout/stderr with Docker timestamps. Decode non-TTY 8-byte multiplex framing and TTY raw streams, reconstructing complete lines.
- Archive original message bytes without Docker's timestamp prefix at LOG_DIR/<service>/YYYY-MM-DD_HH.log, using the timestamp's UTC hour.
- Share files safely between follower threads. Append and flush; rotate when hour changes. Track newest timestamps per container to suppress replay within the process.
- Follow new containers from their beginning; older ones from collector startup time. Retry listing after Docker errors; reconnect follower threads when appropriate.
- Prune old *.log files by mtime when a new written hour triggers pruning. Retention ≤0 disables pruning. This is not a continuous timer-based deletion service.
- No HTTP API, published port, or healthcheck. Collector failures must not gate product startup.
- Cursor state is memory-only, not durable exactly-once delivery. Equal timestamps, collector outages, or restarts can lose/duplicate edge cases; do not promise lossless archival.

## Build prompt

Reconstruct the Unix-socket Docker client, frame/line reader, per-container followers, timestamp routing, thread-safe hourly files, and retention cleanup in one standard-library Python script. Keep the centralized Compose mount design; do not recreate the stale hourly-log.sh wrapper mentioned in old docs.

## Acceptance checks

- Use synthetic TTY/non-TTY streams to verify line assembly, timestamp stripping, UTC hour selection, rotation, and retention-disabled behavior.
- Exercise transient Docker/list/follower failure with stubs or a disposable Compose project; verify one-shot and upstream-image service logs are included.
- Do not touch live logs for destructive tests. Report which checks ran and the non-durable cursor limitation.

## Source anchors

- `SystemCode/remymy/docker-compose.yml`: log-collector
