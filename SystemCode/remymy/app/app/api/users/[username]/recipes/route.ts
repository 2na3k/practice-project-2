import { NextRequest, NextResponse } from "next/server";

const DEFAULT_DATA_API_URL = "http://localhost:8002";

// The recipes the user wrote themselves, newest first and each whole, from the Data API.
export async function GET(_request: NextRequest, context: { params: Promise<{ username: string }> }) {
  const { username } = await context.params;
  if (!username || username.length > 200 || username.includes("/")) {
    return NextResponse.json({ error: "Invalid username." }, { status: 400 });
  }
  try {
    const base = process.env.DATA_API_URL ?? DEFAULT_DATA_API_URL;
    const response = await fetch(new URL(`/api/users/${encodeURIComponent(username)}/recipes`, base), { cache: "no-store" });
    return new NextResponse(await response.text(), {
      status: response.status,
      headers: { "content-type": response.headers.get("content-type") ?? "application/json" },
    });
  } catch {
    return NextResponse.json({ error: "Recipe service is unavailable." }, { status: 502 });
  }
}
