import { NextRequest, NextResponse } from "next/server";

const DEFAULT_DATA_API_URL = "http://localhost:8002";

// The recipes the user saved for later, newest first, from the Data API's database.
export async function GET(_request: NextRequest, context: { params: Promise<{ username: string }> }) {
  const { username } = await context.params;
  if (!username || username.length > 200 || username.includes("/")) {
    return NextResponse.json({ error: "Invalid username." }, { status: 400 });
  }
  try {
    const base = process.env.DATA_API_URL ?? DEFAULT_DATA_API_URL;
    const response = await fetch(new URL(`/api/users/${encodeURIComponent(username)}/saved`, base), { cache: "no-store" });
    return new NextResponse(await response.text(), {
      status: response.status,
      headers: { "content-type": response.headers.get("content-type") ?? "application/json" },
    });
  } catch {
    return NextResponse.json({ error: "Saved recipes service is unavailable." }, { status: 502 });
  }
}
