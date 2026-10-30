# Service prompt: langfuse-gateway

## System prompt

You are reconstructing Remymy's tiny local-development Langfuse gateway in Node.js. Proxy the upstream UI and establish the pre-seeded development session without building another authentication system.

## System bound

- Own `SystemCode/remymy/langfuse-gateway/server.mjs`, its Dockerfile, and gateway Compose configuration.
- Proxy LANGFUSE_UPSTREAM (default http://langfuse-web:3000). Use configured local seed-user credentials solely for upstream sign-in.
- This intentionally removes the login step for anyone who can reach it. Bind the published host port to 127.0.0.1 only; never present this as production authentication.
- Do not change product sessions, grant broader access, store user passwords, or expose credentials to the browser/logs.

## System contract

- Use node:22-alpine and built-in node:http/fetch; no proxy framework required.
- Server listens on container 0.0.0.0:PORT (default 3030). Compose publishes 127.0.0.1:LANGFUSE_PORT:3030 and waits for healthy langfuse-web.
- Configuration: LANGFUSE_UPSTREAM, LANGFUSE_PUBLIC_URL, LANGFUSE_LOCAL_EMAIL, LANGFUSE_LOCAL_PASSWORD, PORT. Missing email/password fails startup.
- Paths under /api/public/ bypass automatic session acquisition. Requests with a next-auth.session-token cookie normally proxy directly; /auth/sign-in still triggers acquisition.
- To acquire a session: GET upstream /api/auth/csrf with the configured public host, collect CSRF cookies/token, then form-POST /api/auth/callback/credentials with csrfToken/email/password/callbackUrl/json=true and manual redirect handling.
- Require a successful login response containing next-auth.session-token. Forward upstream session Set-Cookie headers in a 302 redirect back to the original request URL, with Cache-Control:no-store.
- Otherwise proxy the original method/path/body/headers, rewriting Host to the upstream host; stream upstream status, headers, and body.
- Proxy/sign-in failures return 502. The current implementation can include upstream error text; do not claim comprehensive error redaction.
- Healthcheck proxies /api/public/health with wget; interval 10s, timeout 5s, start period 10s, retries 6.
- Dockerfile selects user node; current Compose overrides user to 0:0. Do not claim the deployed container is unprivileged.
- Do not claim WebSocket forwarding, HTTPS upstream support, expired-cookie recovery, or hardened reverse-proxy behavior beyond the current node:http implementation.

## Build prompt

Implement the single-file gateway, CSRF/credential exchange, cookie redirect, streaming HTTP proxy, and container configuration. Preserve the loopback-only exposure and do not add a passwordless public endpoint elsewhere. Keep secrets supplied by environment and aligned with Langfuse seed credentials.

## Acceptance checks

- Test public-API bypass, existing-cookie proxying, forced sign-in route, CSRF/login cookie exchange, redirect/no-store headers, and upstream failures using a local fake upstream.
- Verify host publication is strictly 127.0.0.1 and the upstream health request succeeds when the stack is available.
- No dedicated gateway suite currently exists; add a focused test if changing logic. Report checks and development-only risk.

## Source anchors

- `SystemCode/remymy/langfuse-gateway/server.mjs`, `Dockerfile` in that directory
- `SystemCode/remymy/docker-compose.yml`: langfuse-gateway and langfuse-web seed settings
