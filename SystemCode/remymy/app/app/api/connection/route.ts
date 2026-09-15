import type { NextRequest } from "next/server";
import type { Context, Model, Models, Api } from "@earendil-works/pi-ai";
import type { ConnectionStatus } from "@/lib/chat-types";
import { MAX_API_KEY_CHARS, errorResponse, isRecord, isSameOriginMutation, readJsonBody, RequestGuardError } from "@/lib/server/request-guards";
import { CONNECTION_COOKIE, createSession, deleteSession, getSession, publicConnectionStatus } from "@/lib/server/session-store";
import { findRegistryProvider, getPiModels, resolveSupportedModel } from "@/lib/server/model-registry";

export const dynamic = "force-dynamic";

const connectionContext = (): Context => ({
  systemPrompt: "Reply with exactly OK.",
  messages: [{ role: "user", content: "Reply with exactly OK.", timestamp: Date.now() }],
});

const jsonNoStore = (body: unknown, status = 200): Response => Response.json(body, { status, headers: { "Cache-Control": "no-store" } });

export async function GET(request: NextRequest): Promise<Response> {
  const session = getSession(request.cookies.get(CONNECTION_COOKIE)?.value);
  return jsonNoStore(publicConnectionStatus(session));
}

function withConnectionCookie(response: Response, request: NextRequest, token: string, maxAge: number): Response {
  const secure = new URL(request.url).protocol === "https:" ? "; Secure" : "";
  response.headers.set("Set-Cookie", `${CONNECTION_COOKIE}=${encodeURIComponent(token)}; Path=/; Max-Age=${maxAge}; HttpOnly; SameSite=Strict${secure}`);
  return response;
}

export async function verifyModelConnection(model: Model<Api>, apiKey: string, signal: AbortSignal, models: Models = getPiModels()): Promise<boolean> {
  const result = await models.completeSimple(model, connectionContext(), {
    apiKey,
    maxTokens: 16,
    timeoutMs: 60_000,
    maxRetries: 0,
    signal,
  });
  return result.stopReason !== "error" && result.stopReason !== "aborted";
}

export async function POST(request: NextRequest): Promise<Response> {
  if (!isSameOriginMutation(request)) return jsonNoStore({ error: "Cross-origin requests are not allowed." }, 403);
  try {
    const body = await readJsonBody(request);
    if (!isRecord(body) || typeof body.provider !== "string" || typeof body.model !== "string" || typeof body.apiKey !== "string") {
      throw new RequestGuardError("Provider, model, and API key are required.", 400);
    }
    const provider = body.provider.trim();
    const modelId = body.model.trim();
    const apiKey = body.apiKey.trim();
    if (!provider || !modelId || !apiKey || apiKey.length > MAX_API_KEY_CHARS) throw new RequestGuardError("Provider, model, and API key are invalid.", 400);
    const model = resolveSupportedModel(provider, modelId);
    const providerInfo = findRegistryProvider(provider);
    if (!model || !providerInfo) throw new RequestGuardError("Choose a supported provider and model.", 400);
    if (!await verifyModelConnection(model, apiKey, request.signal)) return jsonNoStore({ error: "The provider could not verify this connection." }, 502);
    const previous = request.cookies.get(CONNECTION_COOKIE)?.value;
    if (previous) deleteSession(previous);
    const session = createSession(provider, modelId, apiKey);
    return withConnectionCookie(Response.json({ connected: true, provider, model: modelId, expiresAt: session.expiresAt } satisfies ConnectionStatus), request, session.token, Math.floor(8 * 60 * 60));
  } catch (error) {
    return errorResponse(error, 502);
  }
}

/** Switch models within the connected provider without asking the browser to resend its key. */
export async function PATCH(request: NextRequest): Promise<Response> {
  if (!isSameOriginMutation(request)) return jsonNoStore({ error: "Cross-origin requests are not allowed." }, 403);
  const previous = request.cookies.get(CONNECTION_COOKIE)?.value;
  const current = getSession(previous);
  if (!current) return jsonNoStore({ error: "Connect a model before switching models." }, 401);
  try {
    const body = await readJsonBody(request);
    if (!isRecord(body) || typeof body.model !== "string") throw new RequestGuardError("A model is required.", 400);
    const modelId = body.model.trim();
    if (!modelId) throw new RequestGuardError("A model is required.", 400);
    const model = resolveSupportedModel(current.provider, modelId);
    if (!model) throw new RequestGuardError("Choose a supported model for the connected provider.", 400);
    if (!await verifyModelConnection(model, current.apiKey, request.signal)) return jsonNoStore({ error: "The provider could not verify this model." }, 502);
    deleteSession(current.token);
    const session = createSession(current.provider, modelId, current.apiKey);
    return withConnectionCookie(Response.json({ connected: true, provider: current.provider, model: modelId, expiresAt: session.expiresAt } satisfies ConnectionStatus), request, session.token, Math.floor(8 * 60 * 60));
  } catch (error) {
    return errorResponse(error, 502);
  }
}

export async function DELETE(request: NextRequest): Promise<Response> {
  if (!isSameOriginMutation(request)) return jsonNoStore({ error: "Cross-origin requests are not allowed." }, 403);
  const token = request.cookies.get(CONNECTION_COOKIE)?.value;
  deleteSession(token);
  return withConnectionCookie(Response.json({ connected: false } satisfies ConnectionStatus), request, "", 0);
}
