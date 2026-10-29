import { NextRequest, NextResponse } from "next/server";

const DEFAULT_DATA_API_URL = "http://localhost:8002";
const GUEST_COOKIE = "remymy-guest";
const GUEST_USERNAME = /^guest-[0-9a-f]{32}$/;
const MAX_RECIPE_IDS = 100;

export async function POST(request: NextRequest) {
  const username = request.cookies.get(GUEST_COOKIE)?.value;
  if (!username || !GUEST_USERNAME.test(username)) {
    return NextResponse.json({ error: "A valid guest user is required." }, { status: 401 });
  }

  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Invalid impression." }, { status: 400 });
  }
  if (!body || typeof body !== "object" || Array.isArray(body)) {
    return NextResponse.json({ error: "Invalid impression." }, { status: 400 });
  }
  const payload = body as { recipe_ids?: unknown; trace_id?: unknown };
  if (!Array.isArray(payload.recipe_ids) || payload.recipe_ids.length < 1 || payload.recipe_ids.length > MAX_RECIPE_IDS ||
      !payload.recipe_ids.every((id) => typeof id === "string" && id.length > 0 && id.length <= 200) ||
      (payload.trace_id !== undefined && payload.trace_id !== null && typeof payload.trace_id !== "string")) {
    return NextResponse.json({ error: "Invalid impression." }, { status: 400 });
  }

  try {
    const base = process.env.DATA_API_URL ?? DEFAULT_DATA_API_URL;
    const response = await fetch(new URL(`/api/users/${encodeURIComponent(username)}/impressions`, base), {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ recipe_ids: payload.recipe_ids, trace_id: payload.trace_id ?? null }),
      cache: "no-store",
    });
    return new NextResponse(await response.text(), {
      status: response.status,
      headers: { "content-type": response.headers.get("content-type") ?? "application/json" },
    });
  } catch {
    return NextResponse.json({ error: "Impression service is unavailable." }, { status: 502 });
  }
}
