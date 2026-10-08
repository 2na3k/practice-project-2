import http from "node:http";

const upstream = process.env.LANGFUSE_UPSTREAM ?? "http://langfuse-web:3000";
const publicUrl = process.env.LANGFUSE_PUBLIC_URL ?? "http://localhost:3030";
const email = process.env.LANGFUSE_LOCAL_EMAIL;
const password = process.env.LANGFUSE_LOCAL_PASSWORD;
const port = Number(process.env.PORT ?? 3030);

if (!email || !password) throw new Error("Local Langfuse credentials are required.");

const cookieHeader = (setCookies) =>
  setCookies.map((cookie) => cookie.split(";", 1)[0]).join("; ");

async function createSessionCookies() {
  const csrfResponse = await fetch(`${upstream}/api/auth/csrf`, {
    headers: { host: new URL(publicUrl).host },
  });
  if (!csrfResponse.ok) throw new Error(`CSRF request failed (${csrfResponse.status}).`);
  const csrfCookies = csrfResponse.headers.getSetCookie();
  const { csrfToken } = await csrfResponse.json();
  const body = new URLSearchParams({
    csrfToken,
    email,
    password,
    callbackUrl: publicUrl,
    json: "true",
  });
  const loginResponse = await fetch(`${upstream}/api/auth/callback/credentials`, {
    method: "POST",
    redirect: "manual",
    headers: {
      "content-type": "application/x-www-form-urlencoded",
      cookie: cookieHeader(csrfCookies),
      host: new URL(publicUrl).host,
    },
    body,
  });
  const sessionCookies = loginResponse.headers.getSetCookie();
  if (!loginResponse.ok || !sessionCookies.some((cookie) => cookie.startsWith("next-auth.session-token="))) {
    throw new Error(`Langfuse local sign-in failed (${loginResponse.status}).`);
  }
  return sessionCookies;
}

function proxy(request, response) {
  const target = new URL(upstream);
  const headers = { ...request.headers, host: target.host };
  const upstreamRequest = http.request({
    protocol: target.protocol,
    hostname: target.hostname,
    port: target.port,
    method: request.method,
    path: request.url,
    headers,
  }, (upstreamResponse) => {
    response.writeHead(upstreamResponse.statusCode ?? 502, upstreamResponse.headers);
    upstreamResponse.pipe(response);
  });
  upstreamRequest.on("error", (error) => {
    if (!response.headersSent) response.writeHead(502, { "content-type": "text/plain; charset=utf-8" });
    response.end(`Langfuse is unavailable: ${error.message}`);
  });
  request.pipe(upstreamRequest);
}

const server = http.createServer(async (request, response) => {
  const path = new URL(request.url ?? "/", publicUrl).pathname;
  const isPublicApi = path.startsWith("/api/public/");
  const hasSession = /(?:^|;\s*)next-auth\.session-token=/.test(request.headers.cookie ?? "");
  const needsSession = !isPublicApi && (!hasSession || path.startsWith("/auth/sign-in"));

  if (!needsSession) {
    proxy(request, response);
    return;
  }

  try {
    const cookies = await createSessionCookies();
    response.writeHead(302, {
      location: request.url ?? "/",
      "cache-control": "no-store",
      "set-cookie": cookies,
    });
    response.end();
  } catch (error) {
    response.writeHead(502, { "content-type": "text/plain; charset=utf-8" });
    response.end(error instanceof Error ? error.message : "Langfuse local sign-in failed.");
  }
});

server.listen(port, "0.0.0.0", () => {
  console.log(`Langfuse local gateway listening on ${port}`);
});
