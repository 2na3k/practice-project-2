import type { NextRequest } from "next/server";

export const MAX_JSON_BODY_BYTES = 96 * 1024;
export const MAX_API_KEY_CHARS = 4096;
export const MAX_CHAT_TURNS = 24;
export const MAX_CHAT_TEXT_CHARS = 2000;

export function isSameOriginMutation(request: NextRequest): boolean {
  const requestUrl = new URL(request.url);
  // Next may construct request.url from the bind address (0.0.0.0) in local
  // development, while the browser uses localhost or 127.0.0.1. The Host
  // header is the authority actually used for this request, so accept that
  // origin as well while still rejecting a different site.
  const allowedOrigins = new Set([requestUrl.origin]);
  const host = request.headers.get("host")?.trim();
  if (host) allowedOrigins.add(`${requestUrl.protocol}//${host}`);
  const forwardedHost = request.headers.get("x-forwarded-host")?.split(",")[0]?.trim();
  const forwardedProto = request.headers.get("x-forwarded-proto")?.split(",")[0]?.trim();
  if (forwardedHost) allowedOrigins.add(`${forwardedProto || requestUrl.protocol.replace(/:$/, "")}://${forwardedHost}`);

  const origin = request.headers.get("origin");
  if (origin && !allowedOrigins.has(origin)) return false;
  const referer = request.headers.get("referer");
  if (referer) {
    try {
      if (!allowedOrigins.has(new URL(referer).origin)) return false;
    } catch {
      return false;
    }
  }
  return true;
}

export async function readJsonBody(request: Request): Promise<unknown> {
  const contentLength = request.headers.get("content-length");
  if (contentLength) {
    const length = Number(contentLength);
    if (!Number.isFinite(length) || length < 0) throw new RequestGuardError("Request body must be valid JSON.", 400);
    if (length > MAX_JSON_BODY_BYTES) throw new RequestGuardError("Request body is too large.", 413);
  }
  const contentType = request.headers.get("content-type");
  if (contentType && !/^application\/json(?:\s*;|$)/i.test(contentType)) {
    throw new RequestGuardError("Request body must be JSON.", 415);
  }
  const text = await request.text();
  if (new TextEncoder().encode(text).byteLength > MAX_JSON_BODY_BYTES) throw new RequestGuardError("Request body is too large.", 413);
  if (!text.trim()) throw new RequestGuardError("Request body must be JSON.", 400);
  try {
    return JSON.parse(text);
  } catch {
    throw new RequestGuardError("Request body must be valid JSON.", 400);
  }
}

export class RequestGuardError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
    this.name = "RequestGuardError";
  }
}

export function errorResponse(error: unknown, fallbackStatus = 500): Response {
  const headers = { "Cache-Control": "no-store" };
  if (error instanceof RequestGuardError) return Response.json({ error: error.message }, { status: error.status, headers });
  return Response.json({ error: "The request could not be completed." }, { status: fallbackStatus, headers });
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
