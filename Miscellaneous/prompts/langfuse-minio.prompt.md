# Service prompt: langfuse-minio

## System prompt

You are configuring Remymy's S3-compatible Langfuse object store. Preserve the service DNS name langfuse-minio while using the existing RustFS image; do not confuse the historical name with the actual implementation.

## System bound

- Own object-store Compose configuration and langfuse-rustfs-data.
- Store Langfuse event/media objects, not product records or trained recommendation weights.
- The separate langfuse-minio-init job creates the bucket. Do not embed another initialization daemon.
- Keep the host endpoint loopback-only. Do not expose the object store or its development credentials publicly.

## System contract

- Image rustfs/rustfs:latest; restart unless-stopped; network remymy.
- RUSTFS_ACCESS_KEY/SECRET_KEY come from LANGFUSE_MINIO_USER/PASSWORD. RUSTFS_ADDRESS=0.0.0.0:9000; RUSTFS_CONSOLE_ENABLE=false.
- Named volume langfuse-rustfs-data:/data. Memory/swap limits 384m.
- Publish 127.0.0.1:MINIO_PORT:9000, with MINIO_PORT default 9090. No console port is enabled.
- Healthcheck curl -f http://127.0.0.1:9000/health; interval 5s, timeout 5s, start period 10s, retries 8.
- Internal S3 endpoint http://langfuse-minio:9000. Bucket langfuse, region auto, path-style access, prefixes events/ and media/ in client configuration.
- Langfuse web's browser media endpoint uses http://127.0.0.1:MINIO_PORT; internal upload endpoint remains service DNS.
- The init job waits for this service's health. Health alone does not prove the bucket exists or object operations are authorized.
- This configuration does not establish public bucket access or promise at-rest encryption. Do not invent those properties.
- The latest tag is floating; preserve current reconstruction while flagging that reproducible deployment needs an approved pinned version/digest.

## Build prompt

Recreate the RustFS-backed S3 service with its legacy DNS name, shared credentials, persistent volume, loopback publication, and healthcheck. Keep bucket creation in the one-shot init service and use the same endpoint/credentials in all Langfuse clients.

## Acceptance checks

- Validate image/environment mapping, disabled console, loopback-only port, volume, and limits.
- In disposable storage, verify health and authenticated put/get of a temporary object after bucket initialization.
- Report checks actually run; distinguish RustFS health from S3 authorization and bucket readiness. Never delete live objects as a probe.

## Source anchors

- `SystemCode/remymy/docker-compose.yml`: langfuse-minio, langfuse-minio-init, Langfuse S3 settings
