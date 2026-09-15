import type { NextRequest } from "next/server";
import { errorResponse, isRecord, isSameOriginMutation, readJsonBody, RequestGuardError } from "@/lib/server/request-guards";
import { callMcpTool } from "@/lib/server/mcp-client";

export const dynamic = "force-dynamic";

const jsonNoStore = (body: unknown, status = 200): Response => Response.json(body, {
  status,
  headers: { "Cache-Control": "no-store" },
});

export async function POST(request: NextRequest): Promise<Response> {
  if (!isSameOriginMutation(request)) return jsonNoStore({ error: "Cross-origin requests are not allowed." }, 403);

  try {
    const body = await readJsonBody(request);
    if (!isRecord(body) || (body.name !== undefined && typeof body.name !== "string")) {
      throw new RequestGuardError("The name must be a string.", 400);
    }
    const name = typeof body.name === "string" ? body.name.trim() : "Remymy";
    if (name.length > 80) throw new RequestGuardError("The name is too long.", 400);

    const message = await callMcpTool("say_hello", { name: name || "Remymy" }, request.signal);
    return jsonNoStore({ message });
  } catch (error) {
    if (error instanceof RequestGuardError) return errorResponse(error);
    return jsonNoStore({ error: "The Python MCP service is unavailable. Start it and try again." }, 502);
  }
}
