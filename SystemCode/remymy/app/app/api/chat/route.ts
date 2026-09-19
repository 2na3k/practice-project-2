import type { NextRequest } from "next/server";
import { errorResponse, isSameOriginMutation, readJsonBody } from "@/lib/server/request-guards";
import { CONNECTION_COOKIE, getSession } from "@/lib/server/session-store";
import { getPiModels, resolveSupportedModel } from "@/lib/server/model-registry";
import { ChatServiceError, runChat, validateChatRequest } from "@/lib/server/chat-service";
import { flushLangfuse } from "@/lib/server/langfuse";

export const dynamic = "force-dynamic";

const jsonNoStore = (body: unknown, status = 200): Response => Response.json(body, { status, headers: { "Cache-Control": "no-store" } });

export async function POST(request: NextRequest): Promise<Response> {
  if (!isSameOriginMutation(request)) return jsonNoStore({ error: "Cross-origin requests are not allowed." }, 403);
  const session = getSession(request.cookies.get(CONNECTION_COOKIE)?.value);
  if (!session) return jsonNoStore({ error: "Connect a model before starting a chat." }, 401);
  try {
    const body = await readJsonBody(request);
    const chatRequest = validateChatRequest(body);
    if (!chatRequest) return jsonNoStore({ error: "The chat request is invalid." }, 400);
    const model = resolveSupportedModel(session.provider, session.model);
    if (!model) return jsonNoStore({ error: "The connected model is no longer available." }, 502);
    const result = await runChat(chatRequest, model, session.apiKey, getPiModels(), request.signal);
    return jsonNoStore(result);
  } catch (error) {
    if (error instanceof ChatServiceError) return jsonNoStore({ error: error.message }, error.status);
    return errorResponse(error, 502);
  } finally {
    await flushLangfuse();
  }
}
