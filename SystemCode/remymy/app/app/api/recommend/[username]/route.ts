import { NextRequest, NextResponse } from "next/server";

const DEFAULT_DATA_API_URL = "http://localhost:8002";

export async function GET(request: NextRequest, context: { params: Promise<{ username: string }> }) {
  const { username } = await context.params;
  if (!username || username.length > 200 || username.includes("/")) {
    return NextResponse.json({ error: "Invalid username." }, { status: 400 });
  }
  try {
    const base = process.env.DATA_API_URL ?? DEFAULT_DATA_API_URL;
    const url = new URL(`/api/recommend/${encodeURIComponent(username)}`, base);
    const topK = request.nextUrl.searchParams.get("top_k");
    if (topK !== null) url.searchParams.set("top_k", topK);
    const response = await fetch(url, { cache: "no-store" });
    const body = await response.text();
    const headers: Record<string, string> = { "content-type": response.headers.get("content-type") ?? "application/json" };
    // a 503 means the API is retrying the model in the background, and says when to ask again
    const retryAfter = response.headers.get("retry-after");
    if (retryAfter) headers["retry-after"] = retryAfter;
    return new NextResponse(body, { status: response.status, headers });
  } catch {
    return NextResponse.json({ error: "Recommendation service is unavailable." }, { status: 502 });
  }
}
