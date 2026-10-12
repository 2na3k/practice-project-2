import { NextRequest, NextResponse } from "next/server";

const DEFAULT_DATA_API_URL = "http://localhost:8002";

// The user's history, newest first, from the Data API's users database.
export async function GET(request: NextRequest, context: { params: Promise<{ username: string }> }) {
  const { username } = await context.params;
  if (!username || username.length > 200 || username.includes("/")) {
    return NextResponse.json({ error: "Invalid username." }, { status: 400 });
  }
  try {
    const base = process.env.DATA_API_URL ?? DEFAULT_DATA_API_URL;
    const url = new URL(`/api/users/${encodeURIComponent(username)}/history`, base);
    const limit = request.nextUrl.searchParams.get("limit");
    if (limit !== null) url.searchParams.set("limit", limit);
    const response = await fetch(url, { cache: "no-store" });
    return new NextResponse(await response.text(), {
      status: response.status,
      headers: { "content-type": response.headers.get("content-type") ?? "application/json" },
    });
  } catch {
    return NextResponse.json({ error: "History service is unavailable." }, { status: 502 });
  }
}
