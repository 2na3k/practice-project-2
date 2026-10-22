import { NextRequest, NextResponse } from "next/server";
import { errorResponse, isRecord, isSameOriginMutation, readJsonBody } from "../../../../../lib/server/request-guards";

const DEFAULT_DATA_API_URL = "http://localhost:8002";

// Replace the user's preferences in the Data API's users database (PUT /api/users/{username}/set-preferences), which
// validates them against its schemas.Preferences.
export async function PUT(request: NextRequest, context: { params: Promise<{ username: string }> }) {
  if (!isSameOriginMutation(request)) return NextResponse.json({ error: "Cross-origin requests are not allowed." }, { status: 403 });
  const { username } = await context.params;
  if (!username || username.length > 200 || username.includes("/")) {
    return NextResponse.json({ error: "Invalid username." }, { status: 400 });
  }
  let body: unknown;
  try {
    body = await readJsonBody(request);
  } catch (error) {
    return errorResponse(error, 400);
  }
  if (!isRecord(body)) {
    return NextResponse.json({ error: "Send the preferences as a JSON object." }, { status: 400 });
  }
  try {
    const base = process.env.DATA_API_URL ?? DEFAULT_DATA_API_URL;
    const response = await fetch(new URL(`/api/users/${encodeURIComponent(username)}/set-preferences`, base), {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
      cache: "no-store",
    });
    return new NextResponse(await response.text(), {
      status: response.status,
      headers: { "content-type": response.headers.get("content-type") ?? "application/json" },
    });
  } catch {
    return NextResponse.json({ error: "Preferences service is unavailable." }, { status: 502 });
  }
}
